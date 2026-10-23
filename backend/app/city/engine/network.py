"""NetworkX adapter over explicitly supplied, field-verified directed walking edges."""
import networkx as nx
from ..schema import Evidence, TravelEstimate
from .models import Journey


class VerifiedWalkingNetwork:
    def __init__(self,graph,context):
        self.graph=graph;self.ctx=context

    def journey(self,origin,destination,requirements):
        g=nx.DiGraph()
        for a,b,record in self.graph.edges(data=True):
            j=record.get('journey')
            if not j or j.travel.mode!='walking' or (j.travel.origin_place_id,j.travel.destination_place_id)!=(a,b):continue
            if not all(self.ctx.ready(self.ctx.facts.get(('place',pid)),requirements.starts_at,requirements.ends_at) for pid in (a,b)):continue
            from .travel import TravelTimeService
            if TravelTimeService(self.ctx).usable(j,requirements):
                g.add_edge(a,b,weight=j.travel.upper_seconds,journey=j)
        try:path=nx.shortest_path(g,origin,destination,weight='weight')
        except (nx.NetworkXNoPath,nx.NodeNotFound):return None
        # Same-point movement requires an explicit record too; do not invent a zero arc.
        if len(path)<2:return None
        edges=[g[a][b]['journey'] for a,b in zip(path,path[1:])]
        if any(j.boundaries for j in edges):return None  # Cross-park transitions use explicit server journeys.
        kinds={j.travel.coordinate_system for j in edges}
        if len(kinds)!=1:return None
        evs=[j.travel.evidence for j in edges]
        ev=Evidence(evidence_id='network:envelope',revision='1',kind='demo' if self.ctx.synthetic else 'verified',
                    source=';'.join(j.journey_id for j in edges)[:500],checked_at=max(e.checked_at for e in evs),
                    valid_from=max(e.valid_from for e in evs),valid_until=min(e.valid_until for e in evs))
        return Journey(journey_id='network:route',travel=TravelEstimate(origin_place_id=origin,destination_place_id=destination,
            mode='walking',lower_seconds=sum(j.travel.lower_seconds for j in edges),upper_seconds=sum(j.travel.upper_seconds for j in edges),
            evidence=ev,path_status='demo' if self.ctx.synthetic else 'verified',path_ref='network:path',coordinate_system=kinds.pop()),
            distance_m=sum(j.distance_m for j in edges),cost_lower_fen=0,cost_upper_fen=0,
            time_basis='synthetic' if self.ctx.synthetic else 'survey_envelope')
