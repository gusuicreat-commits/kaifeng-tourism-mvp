"""Request-scoped provider estimates; only derived leg values enter trip snapshots."""
import hashlib
import logging
import math
import os
import time
from dataclasses import replace
from datetime import timedelta

from .. import facts
from ..schema import Evidence, TravelEstimate, TZ
from .amap import AmapProvider
from .models import Boundary, Journey, MultiDayRequirements, MultiDayItinerary

SOURCE_ID = 'src_mvp_amap'
logger = logging.getLogger(__name__)


def enabled():
    return os.getenv('AMAP_PLANNING_ENABLED', '').lower() in ('1', 'true', 'yes')


def provider_from_environment():
    return AmapProvider.from_environment(
        enabled=enabled(), terms_reviewed=os.getenv('AMAP_TERMS_REVIEWED', '').lower() in ('1', 'true', 'yes'),
        api_version='v5', timeout_seconds=5, max_requests=60, qps=3, retries=0)


def selected_places(req, ctx):
    ids = {req.origin_place_id, req.destination_place_id}
    for goal in req.goals:
        if goal.target_kind == 'place':
            ids.add(ctx.canonical('place', goal.target_id))
        elif goal.target_kind in ('session', 'program'):
            ids.update(s.stage_place_id for s in ctx.facts.values() if isinstance(s, facts.ShowSession)
                       and (s.fact_id == goal.target_id or s.program_id == goal.target_id)
                       and s.business_date == req.business_date)
        else:
            for s in ctx.facts.values():
                if isinstance(s, facts.Supply) and s.food_id == goal.target_id:
                    v = ctx.facts.get(('vendor', s.vendor_id))
                    if v and (not goal.fixed_place_id or goal.fixed_place_id == v.place_id):
                        ids.add(v.place_id)
    return sorted(pid for pid in ids if ctx.ready(ctx.facts.get(('place', pid)), req.starts_at, req.ends_at))


def to_gcj02(place):
    """Project documented WGS84 coordinates before calling AMap's GCJ-02 API."""
    if place.coordinate_system == 'GCJ02':
        return place
    if place.coordinate_system != 'WGS84' or place.longitude is None or place.latitude is None:
        return place
    lon, lat = place.longitude, place.latitude
    a, ee = 6378245.0, 0.00669342162296594323
    def delta(x, y):
        import math
        dlat = -100 + 2*x + 3*y + .2*y*y + .1*x*y + .2*math.sqrt(abs(x))
        dlat += (20*math.sin(6*x*math.pi) + 20*math.sin(2*x*math.pi))*2/3
        dlat += (20*math.sin(y*math.pi) + 40*math.sin(y/3*math.pi))*2/3
        dlat += (160*math.sin(y/12*math.pi) + 320*math.sin(y*math.pi/30))*2/3
        dlon = 300 + x + 2*y + .1*x*x + .1*x*y + .1*math.sqrt(abs(x))
        dlon += (20*math.sin(6*x*math.pi) + 20*math.sin(2*x*math.pi))*2/3
        dlon += (20*math.sin(x*math.pi) + 40*math.sin(x/3*math.pi))*2/3
        dlon += (150*math.sin(x/12*math.pi) + 300*math.sin(x/30*math.pi))*2/3
        return dlon, dlat
    radlat = math.radians(lat)
    magic = 1 - ee * math.sin(radlat)**2
    dlat, dlon = delta(lon-105, lat-35)
    dlat = dlat/((a*(1-ee))/(magic*math.sqrt(magic))*math.pi)*180
    dlon = dlon/(a/math.sqrt(magic)*math.cos(radlat)*math.pi)*180
    return place.model_copy(update={'longitude': lon+dlon, 'latitude': lat+dlat, 'coordinate_system': 'GCJ02'})


def gate(ctx, park, direction, req):
    matches = [r for r in ctx.facts.values() if isinstance(r, facts.Entrance) and r.place_id == park
               and r.direction in (direction, 'both') and ctx.ready(r, req.starts_at, req.ends_at)]
    return ctx.facts['place', matches[0].entrance_place_id] if len(matches) == 1 else None


def applies_to_day(journey, req):
    identity=journey.journey_id
    return not identity.startswith('amap:day:') or identity.split(':',3)[2]==str(req.business_date)


def enrich(ctx, req, *, provider=None, deadline=None, clock=None, quotes=None, metrics=None):
    if not isinstance(req,MultiDayRequirements):
        return _enrich_day(ctx,req,provider=provider,deadline=deadline,clock=clock,quotes=quotes,metrics=metrics)
    if ctx.synthetic or not enabled():return ctx
    from .multi_day import applicable,day_requirements
    clock=clock or time.monotonic;deadline=clock()+15 if deadline is None else deadline
    own=provider is None;provider=provider or provider_from_environment()
    quotes={} if quotes is None else quotes
    metrics={'cache_hits':0,'budget_skipped':0} if metrics is None else metrics
    initial_requests=provider.request_count
    try:
        for day in req.days:
            daily=day_requirements(req,day,[g for g in req.goals if applicable(g,day)],
                                   day.objective or req.objective,req.solve_seconds)
            ctx=_enrich_day(ctx,daily,provider=provider,deadline=deadline,clock=clock,dated=True,
                            quotes=quotes,metrics=metrics)
        return ctx
    finally:
        logger.info('Multi-day travel estimates: provider_requests=%s cache_hits=%s budget_skipped=%s',
                    provider.request_count-initial_requests,metrics['cache_hits'],metrics['budget_skipped'])
        if own:provider.close()


def _enrich_day(ctx, req, *, provider=None, deadline=None, clock=None, dated=False, quotes=None, metrics=None):
    if ctx.synthetic or not enabled():
        return ctx
    source = ctx.facts.get(('source', SOURCE_ID))
    if not ctx.ready(source, req.starts_at, req.ends_at):
        return ctx
    own = provider is None
    provider = provider or provider_from_environment()
    journeys = list(ctx.operations.journeys)
    ids = selected_places(req, ctx)
    # Bound both endpoint expansion and network work; missing arcs remain unavailable.
    if len(ids) > 8:
        if own: provider.close()
        return ctx
    checked = ctx.assessed_at
    clock=clock or time.monotonic;stopped=False;skipped=0
    evidence = Evidence(evidence_id='amap:planning', revision='1', kind='estimate',
                        source='AMap Web Service v5', checked_at=checked, valid_from=checked,
                        valid_until=max(req.ends_at + timedelta(seconds=1), checked + timedelta(minutes=15)))
    try:
        for a in ids:
            for b in ids:
                parks_a, parks_b = set(ctx.parks(a)), set(ctx.parks(b))
                boundaries = []
                origin, destination = to_gcj02(ctx.facts['place', a]), to_gcj02(ctx.facts['place', b])
                unresolved = False
                for park, direction in [(p, 'exit') for p in sorted(parks_a - parks_b)] + [(p, 'entry') for p in sorted(parks_b - parks_a)]:
                    anchor = gate(ctx, park, direction, req)
                    if anchor is None:
                        # Use the provider POI center for MVP estimates. The
                        # route remains reference data and is never navigation-certified.
                        unresolved = True
                        break
                    if direction == 'exit': origin = anchor
                    else: destination = anchor
                    boundaries.append(Boundary(park_id=park, gate_place_id=anchor.fact_id,
                        direction=direction, seconds=180 if direction == 'entry' else 60, evidence=evidence))
                if unresolved:
                    origin, destination, boundaries = to_gcj02(ctx.facts['place', a]), to_gcj02(ctx.facts['place', b]), []
                for mode in req.modes:
                    quote = None
                    expired=deadline is not None and clock()>=deadline
                    # Uncached calls keep the old prefix, including zero-distance hops.
                    if quotes is None and (stopped or expired):
                        stopped=True;skipped+=1;continue
                    if (origin.longitude, origin.latitude) == (destination.longitude, destination.latitude):
                        if expired:
                            skipped+=1;continue
                        seconds, metres, buffer = 0, 0, 0
                    else:
                        quote_key=(origin.longitude,origin.latitude,destination.longitude,destination.latitude,
                                   mode,req.starts_at.astimezone(TZ).strftime('%H:%M'))
                        if quotes is not None and quote_key in quotes:
                            quote=quotes[quote_key]
                            if metrics is not None:metrics['cache_hits']+=1
                        else:
                            if expired or provider.request_count>=getattr(provider,'max_requests',60):
                                stopped=True;skipped+=1;continue
                            if provider.request_count:
                                time.sleep(provider.interval)
                            if (deadline is not None and clock()>=deadline
                                    or provider.request_count>=getattr(provider,'max_requests',60)):
                                stopped=True;skipped+=1;continue
                            quote = provider.estimate(origin, destination, mode, req.starts_at, checked)
                            if quotes is not None:quotes[quote_key]=quote
                        if quote.status != 'estimated' or quote.duration_seconds is None or not quote.geometry_available:
                            continue
                        seconds, metres = quote.duration_seconds, quote.distance_m
                        buffer = max(120, math.ceil(seconds * .15))
                    key=f'{a}|{b}|{mode}'
                    if dated:key+=f'|{req.business_date}|{req.starts_at.isoformat()}|{req.ends_at.isoformat()}|{checked.isoformat()}'
                    identity=hashlib.sha256(key.encode()).hexdigest()[:20]
                    if dated:identity=f'day:{req.business_date}:'+identity
                    travel = TravelEstimate(origin_place_id=a, destination_place_id=b, mode=mode,
                        evidence=evidence, path_status='estimated', path_ref='amap:' + identity, coordinate_system='GCJ02')
                    journeys.append(Journey(journey_id='amap:' + identity, travel=travel, distance_m=metres,
                        source_ids=(SOURCE_ID,), time_basis='provider_estimate', estimated_seconds=seconds,
                        planning_buffer_seconds=buffer, quote_expires_at=checked + timedelta(minutes=15),
                        boundaries=tuple(boundaries), cost_lower_fen=0 if mode=='walking' else None,
                        cost_upper_fen=0 if mode=='walking' else None,
                        # A zero-distance hop needs no ride: known to cost nothing, not an unknown fare.
                        vehicle_cost_fen=(quote.taxi_cost_fen if quote else 0) if mode=='driving' else None,
                        tolls_fen=(quote.tolls_fen if quote else 0) if mode=='driving' else None,
                        vehicle_cost_source='amap_taxi_estimate' if mode=='driving' and quote and quote.taxi_cost_fen is not None else None))
    finally:
        if metrics is not None:metrics['budget_skipped']+=skipped
        logger.info('Daily travel estimates: date=%s budget_skipped=%s',req.business_date,skipped)
        if own: provider.close()
    return replace(ctx, operations=ctx.operations.model_copy(update={'journeys':tuple(journeys)}))


def restore_snapshot(ctx, itinerary):
    """Called only with an owned candidate/route loaded from server storage."""
    days=tuple(d.itinerary for d in itinerary.days) if isinstance(itinerary,MultiDayItinerary) else (itinerary,)
    saved = [leg.journey for day in days for leg in day.legs if leg.journey.time_basis == 'provider_estimate']
    existing = {j.journey_id: j for j in ctx.operations.journeys}
    existing.update({j.journey_id:j for j in saved})
    return replace(ctx, operations=ctx.operations.model_copy(update={'journeys':tuple(existing.values())}))
