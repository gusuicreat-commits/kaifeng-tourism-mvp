"""Bounded, offline snapshot matrix. An unknown arc is absent, never zero-filled."""
from datetime import timedelta
from .. import facts
from .models import Journey


class TravelTimeService:
    """Request-local cache only; no persistent cache of licensed map responses.

    Operational journeys must be loaded by the server. Dynamic HTTP providers can
    produce an explicit conservative horizon envelope before invoking this engine.
    Failure to cover the horizon is deliberately an unavailable arc.
    """
    def __init__(self, context):
        self.ctx=context;self.queries=0
        self.index={}
        for j in context.operations.journeys:
            t=j.travel
            self.index.setdefault((t.origin_place_id,t.destination_place_id,t.mode),[]).append(j)

    def usable(self,j,req):
        t=j.travel;c=self.ctx
        provider = j.time_basis == 'provider_estimate'
        if provider:
            if (j.estimated_seconds is None or not j.quote_expires_at
                    or t.path_status != 'estimated' or t.evidence.kind != 'estimate'):
                return False
        if ((not provider and t.lower_seconds is None) or not c.evidence_ready(t.evidence,req.starts_at,req.ends_at)
                or t.path_status not in (('verified','estimated','demo') if c.synthetic else ('verified','estimated'))
                or t.coordinate_system not in (('WGS84','GCJ02','local_demo') if c.synthetic else ('WGS84','GCJ02'))
                or not t.path_ref or not c.synthetic and j.time_basis not in ('survey_envelope','provider_estimate')):
            return False
        if not c.synthetic and (not j.source_ids or any(
                not isinstance(c.facts.get(('source',sid)), facts.Source)
                or not c.ready(c.facts.get(('source',sid)),req.starts_at,req.ends_at)
                for sid in j.source_ids)):
            return False
        if req.budget_fen is not None and journey_cost_upper(j) is None and not c.demo_reference:return False
        origin=set(c.parks(t.origin_place_id));dest=set(c.parks(t.destination_place_id))
        expected={(p,'exit') for p in origin-dest}|{(p,'entry') for p in dest-origin}
        actual={(b.park_id,b.direction) for b in j.boundaries}
        # MVP provider estimates may route between provider POI centers when a
        # real gate relation is not available. Keep the result as reference data;
        # surveyed park boundaries remain required for certified navigation.
        if j.time_basis != 'provider_estimate' and (actual!=expected or len(actual)!=len(j.boundaries)):
            return False
        if j.time_basis == 'provider_estimate' and actual and actual!=expected:
            return False
        for b in j.boundaries:
            if not c.evidence_ready(b.evidence,req.starts_at,req.ends_at):return False
            gates=[r for r in c.facts.values() if isinstance(r,facts.Entrance) and r.place_id==b.park_id
                   and r.entrance_place_id==b.gate_place_id and r.direction in (b.direction,'both')]
            if not any(c.ready(g,req.starts_at,req.ends_at) for g in gates):return False
            if not c.synthetic and b.direction=='entry':
                rules=c.planning_rules(b.park_id,req.business_date,req.starts_at,req.ends_at)
                if len(rules)!=1:return False
                rule=rules[0]
                if (not c.ready(rule,req.starts_at,req.ends_at)
                    or rule.planning_status != 'reference' and (not rule.opens_at or not rule.closes_at)
                    or (rule.planning_status != 'reference' and (not rule.last_entry_at or rule.reentry=='unknown'))
                    or facts.access_unknown(rule)
                    or facts.access_required(rule) and b.park_id not in req.access_confirmations):
                    return False
        return True

    def matrix(self,req,activities):
        places={req.origin_place_id,req.destination_place_id}|{a.place_id for a in activities}
        output={}
        for a in places:
            for b in places:
                self.queries+=1
                choices=[]
                for mode in req.modes:
                    choices.extend(j for j in self.index.get((a,b,mode),()) if self.usable(j,req))
                # Keep modes as parallel arcs: fewer metres may require more minutes.
                output[a,b]=tuple(choices)
        return output


def travel_seconds(j):
    seconds = (j.estimated_seconds + j.planning_buffer_seconds
               if j.time_basis == 'provider_estimate' and j.estimated_seconds is not None else j.travel.upper_seconds)
    return seconds+sum(b.seconds for b in j.boundaries)


def walking_metres(j):
    return j.distance_m if j.travel.mode=='walking' else 0


def entry_deadline(j,req,ctx):
    """Conservative whole-leg arrival before stop-entry deadline; no inferred zero boundary."""
    latest=None
    for b in j.boundaries:
        if b.direction!='entry':continue
        for pid in (b.park_id,b.gate_place_id):
            for r in ctx.planning_rules(pid,req.business_date,req.starts_at,req.ends_at):
                if not ctx.ready(r,req.starts_at,req.ends_at):continue
                value=r.last_entry_at or r.closes_at
                if value:latest=value if latest is None else min(latest,value)
    return latest


def journey_cost_upper(j):
    if j.cost_upper_fen is None or any(b.cost_upper_fen is None for b in j.boundaries):return None
    return j.cost_upper_fen+sum(b.cost_upper_fen for b in j.boundaries)


def entry_opening(j,req,ctx):
    """A boundary cannot be crossed before the known opening, including transfer-only plans."""
    values=[r.opens_at for b in j.boundaries if b.direction=='entry'
            for pid in (b.park_id,b.gate_place_id) for r in ctx.planning_rules(pid,req.business_date,req.starts_at,req.ends_at)
            if ctx.ready(r,req.starts_at,req.ends_at) and r.opens_at]
    return max(values) if values else None
