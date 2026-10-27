from typing import Literal
from uuid import UUID
from pydantic import Field, model_validator
from ..schema import Strict
from ..basket import ObjectRef


class Action(Strict):
    kind: Literal['query','search_dining','search_services','modify','preference','cancel','progress','position'] = Field(
        description='query查;search_dining餐饮;search_services服务;modify改已排路线;preference偏好;cancel只撤销待确认修改;progress进度;position位置。出发前不去/候补/改优先级不是action，填planning.choices。')
    quote: str = Field(min_length=1, max_length=500)
    target_name: str | None = Field(default=None, max_length=120,
                                    description='具体菜/店/地名/节目；泛指null。')
    purpose: Literal['trip','next','shows','dining','travel','history','reference'] | None = Field(
        default=None, description='普通query给purpose=trip/next/shows/dining/travel/history/reference。trip=行程;next=下一站;shows=演出;dining=餐饮;travel=交通;history=历史;reference=引用。')
    # search_dining only: the visitor limited the search to where they are now.
    near: bool = Field(default=False, description='true=附近；false=不限。')
    dining: dict = Field(default_factory=dict, description='taste口味顿号隔;spicy=none不辣/mild微辣/hot辣;max_fen人均上限分;period早餐/午餐/晚餐/夜宵;dish=true招牌')

    @model_validator(mode='before')
    @classmethod
    def dining_conditions(cls,value):
        # Conditions are a filter, not a request: one the model wrote off-format is dropped
        # (the reply echoes the conditions actually applied) instead of failing the turn.
        if not isinstance(value,dict) or 'dining' not in value:return value
        if not value['dining']:return dict(value,dining={})
        dining=value['dining'] if isinstance(value['dining'],dict) else {}
        if not (value.get('kind')=='search_dining' or value.get('kind')=='query' and value.get('purpose')=='dining'):dining={}
        spicy={'不辣':'none','微辣':'mild','辣':'hot'}
        kept={}
        for key,item in dining.items():
            if key=='spicy':item=spicy.get(item,item)
            if key=='dish' and type(item) is bool or key=='max_fen' and type(item) is int and 0<=item<=100000000 \
               or key=='spicy' and item in ('none','mild','hot') or key=='period' and item in ('早餐','午餐','晚餐','夜宵') \
               or key=='taste' and isinstance(item,str) and item.strip() and len(item)<=100:
                kept[key]=item
        return dict(value,dining=kept)


IN_TRIP_CHANGES=frozenset({'skip_replan','delay','extend','rest'})


class StateIntent(Strict):
    operation: Literal['complete','skip','skip_replan','delay','extend','rest','position','cancel','restore'] = Field(
        description='状态操作用state_request：complete/skip/position/cancel/restore；目标用原文完整名称，缺对象不猜。complete=完成;skip=跳过;position=位置;cancel=取消;restore=恢复。途中delay=已耽误(堵车/排队X分钟);extend=当前站多待/多逛;rest=歇/坐会儿;skip_replan=不去下一站或某站。否定不填。')
    quote: str = Field(min_length=1,max_length=500)
    target_name: str | None = Field(default=None,max_length=120)
    minutes: int | None = Field(default=None,ge=1,le=360,description='分钟须对应原话；缺失null追问。')
    next_stop: bool = Field(default=False,description='指下一站为true，否则false给站名。')
    start_time: str | None = Field(default=None,pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$',description='实际开始时间给HH:MM及start_quote；恢复给明确restore_version。HH:MM为24小时制，小时00–23、分钟00–59。')
    start_quote: str | None = Field(default=None,max_length=200)
    restore_version: int | None = Field(default=None,ge=1)


class Constraint(Strict):
    field: Literal['food','preserve_shows','walking_seconds','return_by','meal_at','visit_at','objective','pace'] = Field(
        description='food=菜;preserve_shows=保演出;walking_seconds=步行上限;return_by=返回;meal_at=用餐;visit_at=活动;objective=目标;pace=节奏。')
    strength: Literal['hard','soft','optional','unknown'] = Field(
        description='hard=必须;soft=尽量;optional=可放弃;unknown=不明。')
    value: str | int | None = Field(default=None)
    quote: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def typed_value(self):
        if self.field == 'walking_seconds' and self.value is not None:
            if type(self.value) is not int or not 1 <= self.value <= 43200:
                raise ValueError('walking_seconds must be an integer in 1..43200')
        if self.field in ('meal_at','return_by','visit_at') and self.value is not None:
            import re
            if not isinstance(self.value,str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',self.value):
                raise ValueError('clock must be HH:MM')
        if self.field == 'objective' and self.value not in ('balanced','less_walking','less_waiting','lower_cost'):
            raise ValueError('unknown objective')
        if self.field == 'pace' and self.value not in ('relaxed','standard','compact'):
            raise ValueError('unknown pace')
        return self


class Correction(Strict):
    before: str = Field(max_length=200)
    after: str = Field(max_length=200, description='口语纠正取最后有效值。')
    quote: str = Field(min_length=1, max_length=500)


class PlanningField(Strict):
    field: Literal['city','date','start_time','end_time','origin','destination','pace','objective','mode','budget_fen','trip_days'] = Field(
        description='city城市;date日期;start_time/end_time起止;origin/destination出发/返回;pace节奏;objective目标;mode交通;budget_fen预算分;trip_days天。')
    value: str | int = Field(description='日期抄原文；晚间沿用到后续钟点（今晚五点半…十点回→17:30、22:00），无钟点不猜，几点前回来用end_time。pace=relaxed放松/standard标准/compact紧凑;objective=balanced均衡/less_walking少走路/less_waiting少等待/lower_cost省钱;少走路/走不动/腿脚不便=less_walking;无分钟不填walking_seconds。')
    quote: str = Field(min_length=1, max_length=500, description='quote连续摘本轮，field.quote含value；往返复用origin.quote。')
    budget_basis: Literal['total','per_person'] | None = Field(
        default=None, description='trip_days填整数，“两三天”取上限；budget_fen同时给budget_basis（total总预算/per_person每人预算），由你判断口径，系统会把理解结果告诉游客。分：300元=30000；每人金额不乘人数。')

    @model_validator(mode='after')
    def typed_value(self):
        allowed={'pace':('relaxed','standard','compact'),
                 'objective':('balanced','less_walking','less_waiting','lower_cost'),
                 'mode':('walking','driving','transit')}
        if self.field=='budget_fen':
            if type(self.value) is not int or not 0<=self.value<=100000000:raise ValueError('invalid budget')
        elif self.field=='trip_days':
            if type(self.value) is not int or not 1<=self.value<=30:raise ValueError('invalid trip days')
        elif self.budget_basis is not None:
            raise ValueError('budget basis only applies to budget')
        elif not isinstance(self.value,str) or not self.value.strip() or len(self.value)>120:
            raise ValueError('planning value must be bounded text')
        if self.field in allowed and self.value not in allowed[self.field]:raise ValueError('unknown preference')
        return self


class MaterialChoice(Strict):
    name: str = Field(min_length=1,max_length=120)
    priority: Literal['must','want','optional'] = Field(
        description='priority：must=游客明确表示非去不可，want=一般想去（默认），optional=有空/顺路再去，没有明确表态时用want，不放宽必选。想去/去趟/顺路/看看=want或optional；必须/一定/非去不可=must。')
    status: Literal['pending','standby'] = Field(
        default='pending', description='pending=候选;standby=候补。')
    quote: str = Field(min_length=1,max_length=500, description='choices含素材全名/priority/status；quote含整段意愿/优先级/不去，仅地名不填。')
    priority_stated: bool = Field(default=False,
        description='明确优先级才true；仅true改已有项；不去/候补用choices。')


class BasketProposalItem(ObjectRef):
    kind: Literal['scenic','food','place','poi','show','vendor','program'] = Field(
        description='对象类型：scenic=景区;food=菜品;place=地点;poi=兴趣点;show=演出;vendor=商家;program=节目。')
    operation: Literal['put','standby'] = Field(description='put=加入;standby=候补。')
    name: str = Field(min_length=1,max_length=120)
    priority: Literal['must','want','optional'] = Field(
        description='优先级：must=明确非去不可；want=一般想去，未表态默认；optional=有空或顺路再去。')
    quote: str = Field(min_length=1,max_length=500)


class BasketProposal(Strict):
    items: tuple[BasketProposalItem,...] = Field(min_length=1,max_length=12)
    basket_version: int = Field(ge=0)


class BasketCandidate(ObjectRef):
    kind: Literal['scenic','food','place','poi','show','vendor','program'] = Field(
        description='对象类型：scenic=景区;food=菜品;place=地点;poi=兴趣点;show=演出;vendor=商家;program=节目。')
    name: str = Field(min_length=1,max_length=120)
    address: str | None = Field(default=None,max_length=300)


class BasketChoicePrompt(Strict):
    query: str = Field(min_length=1,max_length=120)
    quote: str = Field(min_length=1,max_length=500)
    priority: Literal['must','want','optional'] = Field(
        description='优先级：must=明确非去不可；want=一般想去，未表态默认；optional=有空或顺路再去。')
    operation: Literal['put','standby'] = Field(default='put',description='put=加入或改优先级;standby=候补。')
    priority_stated: bool = True
    candidates: tuple[BasketCandidate,...] = Field(min_length=2,max_length=40)


class PlanningIntent(Strict):
    operation: Literal['update','continue','weather_replan'] = Field(
        default='update', description='update=改条件;weather_replan=天气调整。“就这样/开始安排”用continue，fields/choices空。')
    quote: str = Field(min_length=1,max_length=500)
    fields: tuple[PlanningField,...] = Field(default=(),max_length=12)
    choices: tuple[MaterialChoice,...] = Field(default=(),max_length=12)


class Conversation(Strict):
    reply: str = Field(min_length=1, max_length=1200)


class ReferenceRouteIntent(Strict):
    quote: str = Field(min_length=1,max_length=500)
    origin: str | None = Field(default=None,min_length=1,max_length=80)
    destination: str = Field(min_length=1,max_length=80)
    use_current_position: bool = False
    mode: Literal['walking','driving','transit'] = Field(
        default='walking', description='walking=步行;driving=驾车;transit=公交。')

    @model_validator(mode='after')
    def endpoint_roles(self):
        if bool(self.origin)==self.use_current_position:raise ValueError('explicit origin or visitor position, never both')
        return self


class WeatherIntent(Strict):
    quote: str = Field(min_length=1,max_length=500)


class ServiceField(Strict):
    field: Literal['check_in','check_out','nights','guests','rooms','max_price','stay_budget_fen',
                   'location_text','room_type','window','external_window','dry_wet','breakfast',
                   'free_parking','clean','quiet'] = Field(
        description='check_in=入住;check_out=退房;nights=晚;guests=人;rooms=房;max_price=每晚上限;stay_budget_fen=住宿总分;location_text=位置;room_type=房型;window=窗;external_window=外窗;dry_wet=干湿;breakfast=早餐;free_parking=停车;clean=干净;quiet=安静。')
    value: str | int | bool = Field(description='入住/退房date value抄原文；nights按晚数独立保存；room_type、location_text抄原词，标间不能推人数/间数。max_price为每晚元；stay_budget_fen为住宿合计分，不能混成整趟预算或报价；金额口径由你判断。')
    quote: str = Field(min_length=1,max_length=500)
    strength: Literal['hard','soft','optional'] = Field(
        default='hard', description='设施/房间条件、真假与强弱由你判断，strength填hard/soft/optional；系统会把理解结果告诉游客。hard=必须；soft=尽量；optional=可放弃。')


class ServiceIntent(Strict):
    quote: str = Field(min_length=1,max_length=500)
    category: Literal['hotels','parking','merchants'] = Field(
        default='hotels', description='hotels=酒店；parking=停车；merchants=商家。')
    fields: tuple[ServiceField,...] = Field(default=(),max_length=16)


class ReferenceSelection(Strict):
    request_id: UUID
    expected_step: int = Field(ge=0,le=12)
    role: Literal['origin','destination'] = Field(description='origin=起点;destination=终点。')
    resolution_id: UUID
    candidate_id: str = Field(min_length=1,max_length=160)


class Understanding(Strict):
    # A turn may contain several bounded subrequests.  The server still
    # validates each typed branch and executes it through its own whitelist.
    schema_version: Literal['travel-intent/3.6'] = Field(
        default='travel-intent/3.6', description='travel-intent/3.6=当前契约版本。')
    actions: tuple[Action, ...] = Field(default=(), max_length=8)
    constraints: tuple[Constraint, ...] = Field(default=(), max_length=16)
    corrections: tuple[Correction, ...] = Field(default=(), max_length=4)
    unresolved: tuple[str, ...] = Field(default=(), max_length=8)
    planning: PlanningIntent | None = None
    conversation: Conversation | None = None
    route_query: ReferenceRouteIntent | None = None
    weather_query: WeatherIntent | None = None
    service_query: ServiceIntent | None = None
    state_request: StateIntent | None = None

    @model_validator(mode='before')
    @classmethod
    def accept_legacy_schema(cls,value):
        if isinstance(value,dict) and value.get('schema_version') in ('travel-intent/3.1','travel-intent/3.2','travel-intent/3.3','travel-intent/3.4','travel-intent/3.5'):
            planning=value.get('planning')
            if isinstance(planning,dict):
                planning=dict(planning,choices=[dict(c,priority_stated=False) if isinstance(c,dict) else c for c in planning.get('choices') or []])
                value=dict(value,planning=planning)
            return dict(value,schema_version='travel-intent/3.6')
        return value

    @model_validator(mode='before')
    @classmethod
    def canonical_action_index(cls,value):
        # Typed branches carry the meaning; actions are only an execution index derived
        # from them. Fill the index entry a branch implies so the model states each
        # request once. This grants nothing: every branch still passes its own gates.
        if not isinstance(value,dict):return value
        actions=list(value.get('actions') or [])
        def has(kind,quote=None):
            return any(isinstance(a,dict) and a.get('kind')==kind and (quote is None or a.get('quote')==quote) for a in actions)
        planning=value.get('planning')
        if isinstance(planning,dict) and planning.get('quote') and not has('modify'):
            actions=[a for a in actions if not (isinstance(a,dict) and a.get('kind')=='query' and a.get('quote')==planning['quote'])]
            actions.append({'kind':'modify','quote':planning['quote']})
        for branch,kind in (('weather_query','query'),('route_query','query'),('service_query','search_services')):
            quote=(value.get(branch) or {}).get('quote') if isinstance(value.get(branch),dict) else None
            if quote and not has(kind,quote):actions.append({'kind':kind,'quote':quote})
        state=value.get('state_request')
        if isinstance(state,dict) and isinstance(state.get('quote'),str):
            if state.get('operation')=='skip' and state.get('next_stop'):state=dict(state,operation='skip_replan')
            if state.get('operation') in IN_TRIP_CHANGES and state.get('next_stop') and state.get('target_name') and state['target_name'] not in state['quote']:
                state=dict(state,target_name=None)
            value=dict(value,state_request=state)
            q=state['quote']
            actions=[a for a in actions if not (isinstance(a,dict) and a.get('kind') in ('modify','progress','position','cancel')
                and isinstance(a.get('quote'),str) and a['quote'] in q
                and (not a.get('target_name') or a.get('target_name')==state.get('target_name')))]
        if isinstance(state,dict) and state.get('quote'):
            kind={'complete':'progress','skip':'progress','skip_replan':'modify','delay':'modify','extend':'modify','rest':'modify','position':'position','cancel':'cancel','restore':'modify'}.get(state.get('operation'))
            if kind and not has(kind,state['quote']):actions.append({'kind':kind,'quote':state['quote']})
        return dict(value,actions=actions) if actions!=list(value.get('actions') or []) else value

    @model_validator(mode='after')
    def validate_subrequests(self):
        if self.state_request:
            expected={'complete':'progress','skip':'progress','skip_replan':'modify','delay':'modify','extend':'modify','rest':'modify','position':'position','cancel':'cancel','restore':'modify'}[self.state_request.operation]
            if not any(a.kind==expected and a.quote==self.state_request.quote for a in self.actions):
                raise ValueError('state request needs matching action')
        if self.route_query and self.route_query.use_current_position and self.route_query.origin:
            raise ValueError('route query cannot carry both origin and current position')
        if self.weather_query and not any(a.kind=='query' and a.quote==self.weather_query.quote for a in self.actions):
            raise ValueError('weather query needs a matching read action')
        if self.service_query and not any(a.kind=='search_services' and a.quote==self.service_query.quote for a in self.actions):
            raise ValueError('service query needs a matching search action')
        if self.route_query and not any(a.kind=='query' and a.quote==self.route_query.quote for a in self.actions):
            raise ValueError('route query needs a matching read action')
        if self.planning and not any(a.kind=='modify' for a in self.actions):
            raise ValueError('planning needs a modify action')
        return self


class StartTask(Strict):
    request_id: UUID
    text: str = Field(min_length=1, max_length=2000)
    expected_version: int = Field(ge=1)
    expected_route_version: int = Field(ge=0)
    page_focus: ObjectRef | None = None


class AdvanceTask(Strict):
    request_id: UUID
    expected_step: int = Field(ge=0, le=12)


class Decision(Strict):
    tool: Literal['search_dining','query_shows','verify_travel','preview_plan','search_locations','query_reference_route','finish','clarify'] = Field(
        description='下一步工具：search_dining=查餐饮；query_shows=查演出；verify_travel=核交通衔接；preview_plan=预览行程；search_locations=查地点；query_reference_route=查参考路线；finish=结束；clarify=请求澄清。')
    place_id: str | None = Field(default=None, max_length=100, description='其他动作留空。')
    relax_optional_food: bool = Field(default=False, description='仅当餐饮要求全为可放弃条件且服务器允许时设为 true。')


class Budget(Strict):
    max_tools: int = Field(default=4, ge=1, le=4)
    max_model_calls: int = Field(default=6, ge=1, le=6)
    max_seconds: float = Field(default=45, ge=1, le=240)
    max_repairs: int = Field(default=1, ge=0, le=1)
