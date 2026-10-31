"""Stable tool inputs; operational facts are server-only, never HTTP request fields."""
from datetime import date, timedelta
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime, Field, model_validator
from ..budget_models import PricingOptions, BudgetSummary
from ..preferences import TripPreferences
from ..schema import (Strict, EntityId, Mode, Evidence, TravelEstimate, Stop, TZ, DayIdentity, DayWindow,
                      DateRange, Overnight, validate_days, validate_overnights)


class Goal(Strict):
    goal_id: EntityId
    target_kind: Literal['place', 'session', 'program', 'food']
    target_id: EntityId
    kind: Literal['visit', 'show', 'dining', 'rest'] = 'visit'
    required: bool = False
    importance: int = Field(default=5, ge=0, le=10)
    fixed_place_id: EntityId | None = None
    fixed_session_id: EntityId | None = None
    duration_seconds: int | None = Field(default=None, ge=1, le=21600)
    window_start: AwareDatetime | None = None
    window_end: AwareDatetime | None = None

    @model_validator(mode='after')
    def valid(self):
        if self.fixed_session_id and self.target_kind not in ('session','program'):raise ValueError('只有演出目标可固定场次')
        if self.fixed_place_id and self.target_kind=='place' and self.fixed_place_id!=self.target_id:raise ValueError('固定地点与目标不一致')
        if self.fixed_session_id and self.target_kind=='session' and self.fixed_session_id!=self.target_id:raise ValueError('固定场次与目标不一致')
        if self.target_kind in ('session','program') and self.kind != 'show':
            raise ValueError('场次/节目目标须使用 show')
        if self.target_kind == 'food' and self.kind != 'dining':raise ValueError('菜品目标须使用 dining')
        if self.target_kind == 'place' and self.kind == 'show':raise ValueError('演出须指定场次或节目')
        if self.window_start and self.window_end and self.window_end <= self.window_start:raise ValueError('目标时间窗无效')
        return self


class Requirements(Strict):
    schema_version: Literal['city-engine/1'] = 'city-engine/1'
    business_date: date
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    origin_place_id: EntityId
    destination_place_id: EntityId
    goals: tuple[Goal, ...] = Field(min_length=0, max_length=30)
    modes: tuple[Mode, ...] = Field(default=('walking',), min_length=1, max_length=3)
    pace: Literal['relaxed','standard','compact'] = 'standard'
    objective: Literal['balanced','less_walking','less_waiting','lower_cost'] = 'balanced'
    max_walking_seconds: int | None = Field(default=None, ge=1, le=43200)
    budget_fen: int | None = Field(default=None, ge=0, le=100000000)
    pricing: PricingOptions | None = None
    access_confirmations: tuple[EntityId, ...] = Field(default=(), max_length=60)
    show_buffer_seconds: int = Field(default=300, ge=0, le=3600)
    solve_seconds: float = Field(default=1, ge=0.05, le=3)
    alternatives: int = Field(default=3, ge=1, le=3)

    @model_validator(mode='after')
    def valid(self):
        if self.ends_at <= self.starts_at:raise ValueError('结束须晚于开始')
        if any(t.astimezone(TZ).date()!=self.business_date for t in (self.starts_at,self.ends_at)):
            raise ValueError('仅支持上海时区同一业务日')
        if len({g.goal_id for g in self.goals})!=len(self.goals):raise ValueError('目标 ID 重复')
        if len(set(self.modes))!=len(self.modes):raise ValueError('交通方式重复')
        return self


class Boundary(Strict):
    park_id: EntityId
    gate_place_id: EntityId
    direction: Literal['entry','exit']
    seconds: int = Field(ge=0, le=7200)
    evidence: Evidence
    cost_upper_fen: int | None = Field(default=None, ge=0)
    cost_unit: Literal['per_person','per_party'] | None = None


class Journey(Strict):
    journey_id: EntityId
    revision: int = Field(default=1, ge=1)
    travel: TravelEstimate
    distance_m: float = Field(ge=0, le=1000000)
    cost_lower_fen: int | None = Field(default=None, ge=0)
    cost_upper_fen: int | None = Field(default=None, ge=0)
    cost_unit: Literal['per_person','per_party'] | None = None
    # 按车计；全程 pricing 开启时用于打车预算与省钱求解。
    vehicle_cost_fen: int | None = Field(default=None, ge=0)
    tolls_fen: int | None = Field(default=None, ge=0)
    vehicle_cost_source: Literal['amap_taxi_estimate'] | None = None
    boundaries: tuple[Boundary, ...] = Field(default=(), max_length=8)
    # Curated source facts carry provenance and usage rights; a URL string is insufficient.
    source_ids: tuple[EntityId, ...] = Field(default=(), max_length=20)
    # A conservative static envelope: it must cover the entire planning horizon.
    time_basis: Literal['survey_envelope','provider_estimate','synthetic']
    estimated_seconds: int | None = Field(default=None, ge=0, le=172800)
    planning_buffer_seconds: int = Field(default=0, ge=0, le=7200)
    quote_expires_at: AwareDatetime | None = None

    @model_validator(mode='after')
    def costs(self):
        if (self.cost_lower_fen is None)!=(self.cost_upper_fen is None):raise ValueError('费用区间须完整或未知')
        if self.cost_lower_fen is not None and self.cost_upper_fen<self.cost_lower_fen:raise ValueError('费用上界小于下界')
        return self


class PlaceCost(Strict):
    place_id: EntityId
    revision: int = Field(default=1, ge=1)
    lower_fen: int = Field(ge=0)
    upper_fen: int = Field(ge=0)
    unit: Literal['per_person','per_party'] | None = None
    evidence: Evidence

    @model_validator(mode='after')
    def valid(self):
        if self.upper_fen < self.lower_fen:raise ValueError('费用区间无效')
        return self


class OperationalBundle(Strict):
    schema_version: Literal['city-operations/1'] = 'city-operations/1'
    journeys: tuple[Journey, ...] = Field(default=(), max_length=5000)
    costs: tuple[PlaceCost, ...] = Field(default=(), max_length=2000)


class Issue(Strict):
    code: str
    message: str
    affected_ids: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()


class ScheduledActivity(Strict):
    activity_id: EntityId
    goal_id: EntityId
    place_id: EntityId
    kind: Literal['visit','show','dining','rest']
    session_id: EntityId | None = None
    arrives_at: AwareDatetime
    starts_at: AwareDatetime
    departs_at: AwareDatetime
    duration_seconds: int
    wait_seconds: int
    required: bool
    adjustable: bool
    state: Literal['planned'] = 'planned'
    data_status: Literal['verified','reference','synthetic']
    evidence_refs: tuple[str, ...]


class ScheduledLeg(Strict):
    journey: Journey
    departs_at: AwareDatetime
    arrives_at: AwareDatetime


class Itinerary(Strict):
    objectives: tuple[Literal['balanced','less_walking','less_waiting','lower_cost'], ...] = Field(default=('balanced',), min_length=0, max_length=3)
    activities: tuple[ScheduledActivity, ...]
    legs: tuple[ScheduledLeg, ...]
    completed_prefix: tuple[Stop, ...] = ()
    ongoing: dict | None = None
    returns_at: AwareDatetime
    total_seconds: int
    travel_seconds: int
    walking_distance_m: float
    known_cost_upper_fen: int
    history_cost_fen: int = 0
    history_cost_unknown: bool = False
    history_cost_basis: Literal['none','user_reported'] = 'none'
    cost_unknown: bool
    score: dict[str, float]
    evidence_mode: Literal['verified_with_estimates','reference_with_estimates','synthetic']
    navigation_certified: Literal[False] = False


class SolveResult(Strict):
    status: Literal['feasible','infeasible','needs_data','timeout','unavailable','invalid']
    itineraries: tuple[Itinerary, ...] = ()
    issues: tuple[Issue, ...] = ()
    warnings: tuple[str, ...] = ()
    metrics: dict = Field(default_factory=dict)
    computed_at: AwareDatetime


class PreviewRequest(Strict):
    request_id: UUID
    expected_version: int = Field(ge=1)
    requirements: Requirements


class OngoingInput(Strict):
    stop_id: EntityId
    remaining_seconds: int = Field(ge=1, le=21600)
    basis: Literal['user_reported','prior_plan_estimate'] = 'user_reported'
    # Explicit user estimate or preserved plan estimate; never a measured queue.


class ReplanRequest(PreviewRequest):
    skipped_goal_ids: tuple[EntityId, ...] = Field(default=(), max_length=1,
        description='本轮明确跳过的一项剩余目标，允许在预览中解除必选；仅确认新路线时记录跳过。')
    ongoing: OngoingInput | None = None
    end_ongoing: bool = False
    spent_cost_fen: int | None = Field(default=None, ge=0, le=100000000)
    spend_as_of: AwareDatetime | None = None
    fixed_goal_ids: tuple[EntityId, ...] = Field(default=(), max_length=30)
    restore_route_version: int | None = Field(default=None, ge=1)


class ConfirmRequest(Strict):
    request_id: UUID
    expected_version: int = Field(ge=1)
    candidate_id: UUID
    alternative_index: int = Field(default=0, ge=0, le=2)


class TripGoal(Goal):
    allowed_dates: tuple[date,...] = Field(min_length=1,max_length=3)
    fixed_date: date | None = None

    @model_validator(mode='after')
    def dates_valid(self):
        if len(set(self.allowed_dates))!=len(self.allowed_dates):raise ValueError('目标允许日期重复')
        if self.fixed_date and self.fixed_date not in self.allowed_dates:raise ValueError('目标固定日期必须属于允许日期')
        return self


class DatedAccessConfirmation(Strict):
    place_id: EntityId
    allowed_dates: tuple[date,...] = Field(min_length=1,max_length=3)


class DayRequirements(DayWindow):
    modes: tuple[Mode,...] | None = Field(default=None,min_length=1,max_length=3)
    pace: Literal['relaxed','standard','compact'] | None = None
    objective: Literal['balanced','less_walking','less_waiting','lower_cost'] | None = None
    access_confirmations: tuple[EntityId,...] | None = Field(default=None,max_length=60)
    max_walking_seconds: int | None = Field(default=None,ge=1,le=43200)

    @model_validator(mode='after')
    def modes_valid(self):
        if self.modes and len(set(self.modes))!=len(self.modes):raise ValueError('交通方式重复')
        return self


class MultiDayRequirements(DateRange):
    schema_version: Literal['city-engine/2']
    days: tuple[DayRequirements,...] = Field(min_length=1,max_length=3)
    goals: tuple[TripGoal,...] = Field(max_length=30)
    overnights: tuple[Overnight,...] = Field(default=(),max_length=2)
    modes: tuple[Mode,...] = Field(default=('walking',),min_length=1,max_length=3)
    pace: Literal['relaxed','standard','compact'] = 'standard'
    objective: Literal['balanced','less_walking','less_waiting','lower_cost'] = 'balanced'
    access_confirmations: tuple[DatedAccessConfirmation,...] = Field(default=(),max_length=60)
    show_buffer_seconds: int = Field(default=300,ge=0,le=3600)
    solve_seconds: float = Field(default=1,ge=0.05,le=3)
    trip_solve_seconds: float | None = Field(default=None,ge=0.05,le=9,
        description='多天整趟搜索的总时间（秒）；为空时按 min(3, 天数) 秒。')
    alternatives: int = Field(default=3,ge=1,le=3)
    pricing: PricingOptions | None = None

    @model_validator(mode='after')
    def valid(self):
        validate_days(self.start_date,self.end_date,self.days)
        validate_overnights(self.days,self.overnights)
        dates={d.business_date for d in self.days}
        if len({g.goal_id for g in self.goals})!=len(self.goals):raise ValueError('全程目标 ID 重复')
        if len(set(self.modes))!=len(self.modes):raise ValueError('交通方式重复')
        for g in self.goals:
            if not set(g.allowed_dates)<=dates:raise ValueError('目标作用域指向不存在的日')
            allowed={g.fixed_date} if g.fixed_date else set(g.allowed_dates)
            if not any((not g.window_start or g.window_start<d.ends_at) and (not g.window_end or g.window_end>d.starts_at)
                       for d in self.days if d.business_date in allowed):raise ValueError('目标绝对时间窗与允许日期没有交集')
        for a in self.access_confirmations:
            if len(set(a.allowed_dates))!=len(a.allowed_dates) or not set(a.allowed_dates)<=dates:
                raise ValueError('票务确认作用域必须属于日范围且不能重复')
        return self


class DayConstraints(DayIdentity):
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    origin_place_id: EntityId | None = None
    destination_place_id: EntityId | None = None
    origin_text: str | None = Field(default=None,max_length=120)
    destination_text: str | None = Field(default=None,max_length=120)
    modes: tuple[Mode,...] | None = Field(default=None,min_length=1,max_length=3)
    pace: Literal['relaxed','standard','compact'] | None = None
    objective: Literal['balanced','less_walking','less_waiting','lower_cost'] | None = None
    max_walking_seconds: int | None = Field(default=None,ge=1,le=43200)
    access_confirmations: tuple[EntityId,...] | None = Field(default=None,max_length=60)

    @model_validator(mode='after')
    def valid(self):
        if self.starts_at and self.ends_at and self.ends_at<=self.starts_at:raise ValueError('每日结束须晚于开始')
        if any(t and t.astimezone(TZ).date()!=self.business_date for t in (self.starts_at,self.ends_at)):
            raise ValueError('每日时间窗必须属于上海时区同一业务日')
        if self.modes and len(set(self.modes))!=len(self.modes):raise ValueError('交通方式重复')
        return self


class PendingDayField(Strict):
    field: Literal['start_time','end_time','origin','destination']
    value: str | None = Field(max_length=120)
    day_ref: str | None = Field(default=None,max_length=120)
    scope: Literal['every_day'] | None = None


class MultiDayConstraints(Strict):
    preferences: TripPreferences | None = None
    preferences_asked: bool = False
    preferences_pending: bool = False
    schema_version: Literal['city-constraints/2']
    start_date: date | None = None
    end_date: date | None = None
    trip_days: int | None = Field(default=None,ge=1,le=30)
    trip_nights: int | None = Field(default=None,ge=0,le=30)
    pending_fields: tuple[PendingDayField,...] = Field(default=(),max_length=30)
    clarifications: tuple[str,...] = Field(default=(),max_length=12)
    range_impacts: tuple[str,...] = Field(default=(),max_length=60)
    timezone: Literal['Asia/Shanghai'] = 'Asia/Shanghai'
    days: tuple[DayConstraints,...] = Field(default=(),max_length=3)
    goals: tuple[TripGoal,...] = Field(default=(),max_length=30)
    overnights: tuple[Overnight,...] = Field(default=(),max_length=2)
    modes: tuple[Mode,...] = Field(default=('walking',),min_length=1,max_length=3)
    pace: Literal['relaxed','standard','compact'] = 'standard'
    objective: Literal['balanced','less_walking','less_waiting','lower_cost'] = 'balanced'
    access_confirmations: tuple[DatedAccessConfirmation,...] = Field(default=(),max_length=60)
    # Checked against known per-person costs after preview; never a solver input.
    per_person_budget_fen: int | None = Field(default=None,ge=0,le=100000000)
    per_person_budget_daily: bool = False
    party_size: int | None = Field(default=None,ge=1,le=50)
    pricing: PricingOptions | None = None

    @model_validator(mode='before')
    @classmethod
    def pricing_aliases(cls,value):
        if isinstance(value,dict) and value.get('pricing') is not None:
            price=PricingOptions.model_validate(value['pricing'])
            return value|{'party_size':price.party_size,'per_person_budget_fen':price.per_person_budget_fen,
                'per_person_budget_daily':price.per_person_daily}
        return value

    @model_validator(mode='after')
    def valid(self):
        if len({d.day_id for d in self.days})!=len(self.days):raise ValueError('day_id 重复')
        if self.start_date and self.end_date:
            if not 1<=(self.end_date-self.start_date).days+1<=3:raise ValueError('日范围必须为连续 1–3 天')
            if any(not self.start_date<=d.business_date<=self.end_date for d in self.days):raise ValueError('每日条件不属于日范围')
            if tuple(d.business_date for d in self.days)!=tuple(sorted(d.business_date for d in self.days)):raise ValueError('每日日期须按顺序排列')
        elif self.days:raise ValueError('每日条件须先明确完整日范围')
        dates={self.start_date+timedelta(days=i) for i in range((self.end_date-self.start_date).days+1)} if self.start_date and self.end_date else set()
        if len({g.goal_id for g in self.goals})!=len(self.goals):raise ValueError('全程目标 ID 重复')
        if any(not set(g.allowed_dates)<=dates for g in self.goals):raise ValueError('目标作用域指向不存在的日')
        if len(set(self.modes))!=len(self.modes):raise ValueError('交通方式重复')
        validate_overnights(self.days,self.overnights)
        if any(not set(a.allowed_dates)<=dates or len(set(a.allowed_dates))!=len(a.allowed_dates) for a in self.access_confirmations):
            raise ValueError('票务确认作用域必须属于日范围且不能重复')
        return self


class OngoingActivity(Strict):
    stop_id: EntityId
    place_id: EntityId
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    remaining_basis: Literal['user_reported','prior_plan_estimate']
    original_activity: ScheduledActivity

    @model_validator(mode='after')
    def valid(self):
        if self.ends_at<=self.starts_at:raise ValueError('正在进行项的剩余时间无效')
        if self.stop_id!=self.original_activity.goal_id or self.place_id!=self.original_activity.place_id:
            raise ValueError('正在进行项与原活动身份不一致')
        return self


class DayItinerary(DayIdentity):
    original_requirements: DayRequirements
    requirements: Requirements
    itinerary: Itinerary
    status: Literal['pending','active','closed'] = 'pending'
    issues: tuple[Issue,...] = ()

    @model_validator(mode='after')
    def valid(self):
        original=self.original_requirements;req=self.requirements;plan=self.itinerary
        if original.day_id!=self.day_id or req.business_date!=self.business_date:raise ValueError('每日需求与 day_id 不一致')
        if req.starts_at<original.starts_at or req.ends_at>original.ends_at:raise ValueError('剩余时窗不能越出原始每日需求')
        if plan.returns_at.astimezone(TZ).date()!=self.business_date:raise ValueError('返程时刻不属于所在业务日')
        from ..schema import CityTripDay
        CityTripDay.model_validate(original.model_dump(include={'day_id','business_date','starts_at','ends_at','origin_place_id','destination_place_id'})|
            {'stops':plan.completed_prefix,'status':self.status})
        if len(plan.completed_prefix)+len(plan.activities)+(1 if plan.ongoing else 0)>24:raise ValueError('每日至多 24 站')
        if any(s.progress=='pending' for s in plan.completed_prefix):raise ValueError('每日历史前缀不能含 pending')
        for a in plan.activities:
            if any(t.astimezone(TZ).date()!=self.business_date for t in (a.arrives_at,a.starts_at,a.departs_at)):
                raise ValueError('活动时刻必须属于所在业务日')
        for l in plan.legs:
            if any(t.astimezone(TZ).date()!=self.business_date for t in (l.departs_at,l.arrives_at)):
                raise ValueError('交通段不能跨日')
        if plan.ongoing:
            ongoing=OngoingActivity.model_validate(plan.ongoing)
            if any(t.astimezone(TZ).date()!=self.business_date for t in (ongoing.starts_at,ongoing.ends_at)):
                raise ValueError('正在进行项必须属于所在业务日')
        return self


class MultiDayItinerary(DateRange):
    schema_version: Literal['city-itinerary/2']
    days: tuple[DayItinerary,...] = Field(min_length=1,max_length=3)
    overnights: tuple[Overnight,...] = Field(default=(),max_length=2)
    objectives: tuple[Literal['balanced','less_walking','less_waiting','lower_cost'],...] = Field(default=('balanced',),max_length=3)
    active_seconds: int = Field(ge=0)
    elapsed_seconds: int = Field(ge=0)
    travel_seconds: int = Field(ge=0)
    walking_distance_m: float = Field(ge=0)
    known_cost_upper_fen: int = Field(ge=0)
    cost_unknown: bool
    evidence_mode: Literal['verified_with_estimates','reference_with_estimates','synthetic','unknown']
    navigation_certified: Literal[False] = False
    metrics: dict = Field(default_factory=dict)
    budget: BudgetSummary | None = None

    @model_validator(mode='after')
    def valid(self):
        validate_days(self.start_date,self.end_date,self.days)
        validate_overnights(tuple(d.original_requirements for d in self.days),self.overnights,complete=True)
        goals=[];sessions=[];ongoing=0
        for d in self.days:
            p=d.itinerary
            goals.extend(s.stop_id for s in p.completed_prefix)
            goals.extend(a.goal_id for a in p.activities)
            sessions.extend(s.session_id for s in p.completed_prefix if s.session_id)
            sessions.extend(a.session_id for a in p.activities if a.session_id)
            if p.ongoing:
                if d.status!='closed':ongoing+=1
                goals.append(p.ongoing['stop_id'])
                session=p.ongoing.get('original_activity',{}).get('session_id')
                if session:sessions.append(session)
        if len(goals)!=len(set(goals)) or len(sessions)!=len(set(sessions)):raise ValueError('全程目标或场次重复')
        if ongoing>1 or sum(d.status=='active' for d in self.days)>1:raise ValueError('全程最多一个正在进行项或 active 日')
        if self.active_seconds!=sum(d.itinerary.total_seconds for d in self.days):raise ValueError('全程 active_seconds 汇总不一致')
        elapsed=int((self.days[-1].itinerary.returns_at-self.days[0].original_requirements.starts_at).total_seconds())
        if self.elapsed_seconds!=elapsed:raise ValueError('全程 elapsed_seconds 汇总不一致')
        if self.travel_seconds!=sum(d.itinerary.travel_seconds for d in self.days):raise ValueError('全程 travel_seconds 汇总不一致')
        if abs(self.walking_distance_m-sum(d.itinerary.walking_distance_m for d in self.days))>0.001:raise ValueError('全程步行汇总不一致')
        ranks={'verified_with_estimates':0,'reference_with_estimates':1,'synthetic':2,'unknown':3}
        modes=[d.itinerary.evidence_mode for d in self.days]
        evidence={'verified':'verified_with_estimates','reference':'reference_with_estimates','synthetic':'synthetic','unknown':'unknown'}
        modes.extend(evidence[v] for o in self.overnights for v in (o.place_evidence,o.stay_evidence))
        if self.evidence_mode!=max(modes,key=ranks.get):raise ValueError('全程证据状态必须取每日与过夜的最弱者')
        if self.budget and (self.known_cost_upper_fen!=self.budget.known_total_fen or self.cost_unknown!=bool(self.budget.unknown_items)):raise ValueError('全程费用必须与预算明细一致')
        if self.budget is None and any(d.itinerary.cost_unknown for d in self.days) and not self.cost_unknown:raise ValueError('未知日费用不能被全程汇总覆盖')
        return self


class SolveResultV2(SolveResult):
    schema_version: Literal['city-engine-result/2']
    itineraries: tuple[MultiDayItinerary,...] = ()


class PreviewRequestV2(PreviewRequest):
    requirements: MultiDayRequirements


class ReplanRequestV2(ReplanRequest):
    requirements: MultiDayRequirements
    scope: Literal['current_day','current_and_future','selected_future_day'] = 'current_day'
    day_id: EntityId | None = None

    @model_validator(mode='after')
    def scope_valid(self):
        if self.scope=='selected_future_day' and not self.day_id:raise ValueError('指定未来日修改必须提供 day_id')
        if self.day_id and self.day_id not in {d.day_id for d in self.requirements.days}:raise ValueError('修改作用域指向不存在的 day_id')
        return self
