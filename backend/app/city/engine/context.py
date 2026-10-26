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
    # Presentation-only admission for curated reference material. This is
    # separate from synthetic fixtures and never changes fact provenance.
    demo_reference: bool = False

    def canonical(self, kind, identity):
        seen = set()
        while (kind, identity) not in seen:
            seen.add((kind, identity))
            record = self.facts.get((kind, identity))
            target = getattr(record, 'superseded_by', None)
            if not target:
                return identity
            identity = target
        raise ValueError('事实替代关系存在循环')

    def resolve(self, kind, identity):
        return self.facts.get((kind, self.canonical(kind, identity)))

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
        return all(check(record,t,True,set()) for t in (moments or (self.assessed_at,)))

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
                 'operations':self.operations.model_dump(mode='json'),'synthetic':self.synthetic,
                 'demo_reference':self.demo_reference}
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

    def planning_rules(self, place_id, day, *moments):
        """Opening rules used by this snapshot, with a demo-only choice."""
        rules=sorted(self.rules(place_id,day), key=lambda r:r.fact_id)
        if not self.demo_reference or len(rules)<=1:
            return tuple(rules)
        usable=[r for r in rules if self.ready(r,*(moments or (self.assessed_at,)))]
        curated=[r for r in usable if r.fact_id.startswith('open_mvp_')]
        if curated:
            return (curated[0],)
        if len(usable)==1:
            return (usable[0],)
        # Split windows (e.g. lunch and dinner): keep the one overlapping the trip horizon most.
        if len(usable)>1 and len(moments)>=2:
            start,end=min(moments),max(moments)
            def overlap(r):
                return ((min(r.closes_at or end,end)-max(r.opens_at or start,start)).total_seconds(),
                        -(r.opens_at or start).timestamp())
            best=max(usable,key=overlap)
            if overlap(best)[0]>0:
                return (best,)
        return tuple(usable)

    def demo_warnings(self):
        if not self.demo_reference:
            return ()
        return (
            '演示模式：参考资料按演示准入参与规划；原始来源仍标记为 reference/unverified，未转为正式核验事实。',
            '演示模式：同日营业规则冲突时优先采用 open_mvp_* 规则；分段营业时按与行程时段重叠最多的一段安排。这是本次演示假设。',
            '演示模式：营业结束时间缺失时按本次行程结束时间计算，不代表实际关门时间。',
            '演示模式：费用未知时不按预算拦截，行程显示“费用待添加”；预算只约束已知费用。',
        )


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
