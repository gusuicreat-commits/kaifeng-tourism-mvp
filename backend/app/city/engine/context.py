"""Server fact resolution and operational snapshots. No browser-supplied evidence."""
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
from .. import facts
from ..schema import Evidence
from .models import OperationalBundle


@dataclass(frozen=True)
class PlanningContext:
    facts: dict
    operations: OperationalBundle
    assessed_at: datetime
    synthetic: bool = False  # Only standalone tests/maintenance, never an HTTP option.

    def ready(self, record, *moments):
        if record is None:return False
        def check(item, moment, date_check, seen):
            gaps=facts.gaps(item,self.facts,moment)
            if any(p!='DEMO_ONLY' or not self.synthetic for p in gaps
                   if date_check or p!='BUSINESS_DATE_NOT_APPLICABLE'):return False
            for ref in facts.references(item):
                if ref in seen:continue
                seen.add(ref);dep=self.facts.get(ref)
                if dep is None or not check(dep,moment,date_check,seen):return False
            return True
        if not check(record,self.assessed_at,False,set()):return False
        return all(check(record,t,True,set()) for t in moments)

    def evidence_ready(self, evidence:Evidence, *moments):
        for t in (self.assessed_at,)+moments:
            if not (evidence.checked_at and evidence.checked_at<=self.assessed_at
                    and evidence.valid_from and evidence.valid_until
                    and evidence.valid_from<=t<evidence.valid_until
                    and (evidence.kind in ('verified','estimate') or self.synthetic and evidence.kind=='demo')):
                return False
        return True

    def fingerprint(self):
        content={'facts':[r.model_dump(mode='json') for _,r in sorted(self.facts.items())],
                 'operations':self.operations.model_dump(mode='json'),'synthetic':self.synthetic}
        return hashlib.sha256(json.dumps(content,sort_keys=True,separators=(',',':')).encode()).hexdigest()

    def parks(self, place_id):
        output=[];seen=set()
        while place_id:
            if place_id in seen:raise ValueError('地点归属循环')
            seen.add(place_id);place=self.facts.get(('place',place_id))
            if not place:break
            if place.place_type=='scenic':output.append(place_id)
            place_id=place.parent_place_id
        return tuple(output)

    def rules(self,place_id,day):
        return [r for r in self.facts.values() if isinstance(r,facts.OpenRule)
                and r.place_id==place_id and r.business_date==day]


@dataclass(frozen=True)
class Activity:
    id: str
    goal_id: str
    place_id: str
    kind: str
    session_id: str | None
    duration: int
    earliest: int
    latest: int
    arrival_deadline: int | None
    required: bool
    reward: int
    cost: int | None
    evidence_refs: tuple[str,...]
    parks: tuple[str,...]


@dataclass
class Problem:
    requirements: object
    context: PlanningContext
    activities: list[Activity]
    matrix: dict
    issues: list = field(default_factory=list)
    blocked: list = field(default_factory=list)
    defaults: dict = field(default_factory=dict)
    prior_order: tuple[str,...] = ()
    fixed_order: tuple[str,...] = ()
    max_selected: int = 24
    history_cost_fen: int = 0
    history_cost_unknown: bool = False
    history_cost_basis: str = 'none'
