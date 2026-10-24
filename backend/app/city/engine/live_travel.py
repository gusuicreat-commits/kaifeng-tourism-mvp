"""Request-scoped provider estimates; only derived leg values enter trip snapshots."""
import hashlib
import math
import os
import time
from dataclasses import replace
from datetime import timedelta

from .. import facts
from ..schema import Evidence, TravelEstimate
from .amap import AmapProvider
from .models import Boundary, Journey

SOURCE_ID = 'src_mvp_amap'


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


def enrich(ctx, req, *, provider=None):
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
                    if (origin.longitude, origin.latitude) == (destination.longitude, destination.latitude):
                        seconds, metres, buffer = 0, 0, 0
                    else:
                        if provider.request_count:
                            time.sleep(provider.interval)
                        quote = provider.estimate(origin, destination, mode, req.starts_at, checked)
                        if quote.status != 'estimated' or quote.duration_seconds is None or not quote.geometry_available:
                            continue
                        seconds, metres = quote.duration_seconds, quote.distance_m
                        buffer = max(120, math.ceil(seconds * .15))
                    identity = hashlib.sha256(f'{a}|{b}|{mode}'.encode()).hexdigest()[:20]
                    travel = TravelEstimate(origin_place_id=a, destination_place_id=b, mode=mode,
                        evidence=evidence, path_status='estimated', path_ref='amap:' + identity, coordinate_system='GCJ02')
                    journeys.append(Journey(journey_id='amap:' + identity, travel=travel, distance_m=metres,
                        source_ids=(SOURCE_ID,), time_basis='provider_estimate', estimated_seconds=seconds,
                        planning_buffer_seconds=buffer, quote_expires_at=checked + timedelta(minutes=15),
                        boundaries=tuple(boundaries), cost_lower_fen=0 if mode=='walking' else None,
                        cost_upper_fen=0 if mode=='walking' else None))
    finally:
        if own: provider.close()
    return replace(ctx, operations=ctx.operations.model_copy(update={'journeys':tuple(journeys)}))


def restore_snapshot(ctx, itinerary):
    """Called only with an owned candidate/route loaded from server storage."""
    saved = [leg.journey for leg in itinerary.legs if leg.journey.time_basis == 'provider_estimate']
    existing = {j.journey_id: j for j in ctx.operations.journeys}
    existing.update({j.journey_id:j for j in saved})
    return replace(ctx, operations=ctx.operations.model_copy(update={'journeys':tuple(existing.values())}))
