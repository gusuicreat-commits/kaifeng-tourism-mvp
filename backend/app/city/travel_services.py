"""Trip-scoped service workspace. Provider candidates never bypass the city fact gate."""
import json
import re
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from .schema import Strict, TZ
from .budget_models import StayPriceInput
from . import hotel_binding
from . import hotels, state, location_resolution  # compatibility export for existing adapter fakes
from .providers import CapabilityRequest, default_registry
from .agent.dates import find_date_expressions, resolve_date
from ..db import connect
from ..memory.session import Session, current, limit
from ..memory.storage import encoded, stamp

router=APIRouter(prefix='/api/city/v1/trips/{trip_id}/services',tags=['旅行服务候选'])

def init(db):
    hotel_binding.init(db)
    db.execute('''CREATE TABLE IF NOT EXISTS city_service_workspaces (
      trip_id TEXT PRIMARY KEY REFERENCES city_trip_states(id) ON DELETE CASCADE,
      version INTEGER NOT NULL, requirements_json TEXT NOT NULL, result_json TEXT,
      selection_json TEXT, previous_json TEXT, updated_at TEXT NOT NULL)''')

def read(db,trip_id):
    r=db.execute('SELECT * FROM city_service_workspaces WHERE trip_id=?',(str(trip_id),)).fetchone()
    if not r:return {'version':0,'requirements':{},'result':None,'selection':None,'previous':None}
    return {'version':r['version'],'requirements':json.loads(r['requirements_json']),
            **{k:json.loads(r[c]) if r[c] else None for k,c in [('result','result_json'),('selection','selection_json'),('previous','previous_json')]}}

def write(db,trip_id,w):
    db.execute('''INSERT INTO city_service_workspaces VALUES (?,?,?,?,?,?,?) ON CONFLICT(trip_id) DO UPDATE SET
       version=excluded.version,requirements_json=excluded.requirements_json,result_json=excluded.result_json,
       selection_json=excluded.selection_json,previous_json=excluded.previous_json,updated_at=excluded.updated_at''',
       (str(trip_id),w['version'],encoded(w['requirements']),encoded(w['result']) if w['result'] else None,
        encoded(w['selection']) if w['selection'] is not None else None,encoded(w['previous']) if w['previous'] is not None else None,stamp()))

def matches(text,requirements=None):
    hotel_query=re.search(r'(?:找|搜|查询|推荐|比较|预订|订|换|选择|要住)[^，,。；;\n]{0,20}(?:酒店|住宿|民宿)|(?:酒店|住宿|民宿)[^，,。；;\n]{0,30}(?:搜索|推荐|预算|入住|退房|窗|早餐|干湿)',text)
    poi_query=re.search(r'(?:找|搜|查询|推荐).{0,30}停车场|(?:搜索|查询).*(?:餐厅|饭店|商家)|高德.*美食',text)
    followup=requirements and re.search(r'换一家|换一个|便宜一点|改成.*(?:元|自驾|公交)|早餐|干湿分离|窗户|隔音',text)
    return bool(hotel_query or poi_query or followup) and not re.search(r'不要(?:搜索|查询)|别(?:搜索|查询)|取消查询',text)

NUMBER = r'(\d+|[零一二两三四五六七八九十百千万]+)'

def number(value):
    if value.isdigit():return int(value)
    digits={c:i for i,c in enumerate('零一二三四五六七八九')};digits['两']=2
    units={'十':10,'百':100,'千':1000,'万':10000};total=section=current=0
    for char in value:
        if char in digits:current=digits[char]
        elif char in units:
            unit=units[char]
            if unit==10000:section=(section+current)*unit;total+=section;section=current=0
            else:section+=(current or 1)*unit;current=0
        else:raise ValueError('invalid number')
    return total+section+current

def stay_dates(req):
    # Nights are independent requirements: retain them even before check-in is
    # known, then recompute check-out when check-in moves.
    if req.get('check_in') and req.get('nights'):
        req['check_out']=(date.fromisoformat(req['check_in'])+timedelta(days=req['nights'])).isoformat()
    elif req.get('check_in') and req.get('check_out'):
        span=(date.fromisoformat(req['check_out'])-date.fromisoformat(req['check_in'])).days
        if 1<=span<=30:req['nights']=span

def service_readiness(req,today):
    questions=[];missing=[]
    if req['category']=='hotels':
        if not req.get('check_in'):
            questions.append('请提供入住日期。');missing.append('check_in')
        if not req.get('check_out') and not req.get('nights'):
            questions.append('请提供退房日期或住几晚。');missing.append('check_out_or_nights')
        if req.get('check_in') and req.get('check_out'):
            start,end=date.fromisoformat(req['check_in']),date.fromisoformat(req['check_out'])
            if start<today or not 1<=(end-start).days<=30:
                questions.append('请选择未来入住日期及 1–30 晚的住宿区间。');missing.append('stay_dates')
    return {'questions':questions,'missing_user_fields':missing}


def service_questions(req,today):
    return service_readiness(req,today)['questions']

def _field_value(f,text,quote,today):
    from .agent.evidence import service_condition_evidence
    if f.quote not in text:raise ValueError('quote_not_in_request')
    if f.quote not in quote:raise ValueError('field_not_in_query')
    try:service_condition_evidence(text,f.quote,quote,literal_name=f.field in ('location_text','room_type'))
    except ValueError:raise ValueError('invalid_evidence') from None
    if f.field in ('check_in','check_out'):
        if not isinstance(f.value,str) or f.value not in f.quote:raise ValueError('value_not_in_quote')
        try:return resolve_date(f.value,today).isoformat()
        except ValueError:raise ValueError('date_unresolved') from None
    if f.field in ('nights','guests','rooms','max_price','stay_budget_fen'):
        limits={'nights':30,'guests':100,'rooms':20,'max_price':100000,'stay_budget_fen':100000000}
        if type(f.value) is not int or not 1<=f.value<=limits[f.field]:raise ValueError('invalid_value')
        # Bind a value to its unit/scope, not another number in a broad quote.
        units={'nights':r'\s*(?:个)?\s*(?:晚上|晚|夜)', 'guests':r'\s*(?:个)?\s*(?:人|位)',
               'rooms':r'\s*间(?:房)?', 'max_price':r'', 'stay_budget_fen':r''}
        expected=f.value//100 if f.field=='stay_budget_fen' else f.value
        if f.field=='stay_budget_fen' and f.value%100:raise ValueError('value_not_in_quote')
        try:matched=any(number(m[1])==expected for m in re.finditer(NUMBER+units[f.field],f.quote))
        except (KeyError,ValueError):raise ValueError('invalid_value') from None
        if not matched:
            raise ValueError('value_not_in_quote')
    elif f.field in ('location_text','room_type'):
        if not isinstance(f.value,str) or not f.value.strip() or len(f.value)>120:raise ValueError('invalid_value')
    elif f.field in hotels.ATTRS:
        if type(f.value) is not bool:raise ValueError('invalid_value')
    return f.value


def semantic_requirements(intent,text,previous,today,dropped=None):
    """Keep valid fields; only the service query's evidence can reject the turn."""
    from .agent.evidence import query_evidence
    req=json.loads(json.dumps(previous or {}));req['category']=intent.category
    attrs=req.setdefault('attributes',{});strengths=req.setdefault('field_strengths',{})
    query_evidence(text,intent.quote)
    kept=[];seen={};conflicts=set()
    def drop(f,check):
        if dropped is not None:dropped.append({'field':f.field,'check':check,'quote':f.quote})
    for f in intent.fields:
        try:value=_field_value(f,text,intent.quote,today)
        except ValueError as exc:
            drop(f,str(exc));continue
        if f.field in seen and seen[f.field]!=value:conflicts.add(f.field)
        seen[f.field]=value;kept.append((f,value))
    # Reject the conflicting field as a whole, independent of model field order.
    for f,value in kept:
        if f.field in conflicts:drop(f,'conflicting_values')
    kept=[(f,value) for f,value in kept if f.field not in conflicts]
    values={f.field:value for f,value in kept}
    start=values.get('check_in',req.get('check_in'));nights=values.get('nights',req.get('nights'))
    if 'check_out' in values and 'nights' in values and start and nights:
        if values['check_out']!=(date.fromisoformat(start)+timedelta(days=nights)).isoformat():
            for f,value in kept:
                if f.field=='check_out':drop(f,'conflicting_values')
            kept=[(f,value) for f,value in kept if f.field!='check_out']
            values.pop('check_out')
    for f,value in kept:
        strength=f.strength
        strengths[f.field]=strength
        if f.field in hotels.ATTRS:attrs[f.field]={'value':value,'strength':strength,'quote':f.quote}
        else:req[f.field]=value
    # Field order cannot change the meaning of an explicit check-out override.
    if 'check_out' in values and 'nights' not in values:req.pop('nights',None)
    stay_dates(req)
    return req

def validate_intent(intent,text,snapshot,dropped=None):
    return semantic_requirements(intent,text,snapshot.services.get('requirements',{}),snapshot.envelope.identity.generated_at.astimezone(TZ).date(),dropped)

def parse(text,previous=None):
    req=json.loads(json.dumps(previous or {}));today=datetime.now(TZ).date();questions=[]
    category='hotels' if re.search(r'酒店|住宿|民宿',text) else 'parking' if '停车场' in text else 'merchants' if re.search(r'餐厅|饭店|美食|商家',text) else req.get('category','hotels')
    req['category']=category
    # Full dates and relative check-in dates. Never infer room count or guest count.
    dates=find_date_expressions(text)
    try:
        if dates:
            req['check_in']=resolve_date(dates[0],today).isoformat()
            if len(dates)>1:req['check_out']=resolve_date(dates[1],today).isoformat()
            elif not req.get('nights'):req.pop('check_out',None)
        nights=re.search(r'(?:住|住宿)\s*'+NUMBER+r'\s*(?:个)?\s*(?:晚上|晚|夜)',text)
        if nights:
            req['nights']=number(nights[1])
        if len(dates)>1 and not nights:req.pop('nights',None)
        stay_dates(req)
    except ValueError:questions.append('日期格式无效，请用 YYYY-MM-DD 给出入住和退房日期。')
    # Money scope must be explicit. A hotel follow-up with “200元以内” is nightly only.
    total=re.search(r'(?:总预算|预算|一共)\s*(\d+)\s*元',text)
    if total:req['total_budget_fen']=int(total[1])*100
    nightly=re.search(r'(?:每晚|一晚|房价|酒店预算)\s*(\d+)\s*元',text) or re.search(r'(\d+)\s*元(?:以内|以下)',text)
    if nightly and category=='hotels':req['max_price']=int(nightly[1])
    stay_budget=re.search(r'预算\s*(?:'+NUMBER+r'\s*(?:个)?\s*(?:天|晚))\s*(\d+)\s*(?:元)?',text)
    if stay_budget and category=='hotels':req['stay_budget_fen']=int(stay_budget[2])*100
    room_type=re.search(r'标间|双床房|大床房|家庭房',text)
    if room_type:req['room_type']=room_type[0]
    location=re.search(r'(?:离|靠近|在)\s*([^，,。；;]{2,30}?)(?:近的|附近|周边|旁边)',text)
    if location:req['location_text']=location[1]
    for field,pattern in [('rooms',r'(\d+)\s*间(?:房)?'),('guests',r'([两一二三四五六七八九]|\d+)\s*(?:个人|人)')]:
        m=re.search(pattern,text)
        if m:req[field]=int(m[1]) if m[1].isdigit() else 2 if m[1]=='两' else '一二三四五六七八九'.index(m[1])+1
    for tokens,mode in [(('自驾','开车'),'driving'),(('公交','公共交通'),'transit'),(('步行',),'walking'),(('打车',),'taxi')]:
        if any(t in text and not re.search(r'(?:不|别|取消).{0,2}'+re.escape(t),text) for t in tokens):req['mode']=mode
    if '少走路' in text:req['less_walking']=True
    attrs=req.setdefault('attributes',{})
    for field,label in hotels.ATTRS.items():
        aliases={'window':('窗户','有窗'),'clean':('卫生','干净')}.get(field,(label,))
        token=next((t for t in aliases if t in text),None)
        if token is None:continue
        clause=next((c for c in re.split(r'[，,。；;]',text) if token in c),text)
        if re.search(r'(?:不要求|不需要|取消).{0,8}'+re.escape(token),clause):attrs.pop(field,None);continue
        strength='optional' if re.search(r'都行|无所谓|有没有.*都|没有也行|不强求',clause) else 'soft' if re.search(r'最好|尽量|希望|优先',clause) and not re.search(r'必须|一定|不能',clause) else 'hard'
        attrs[field]={'strength':strength,'value':True,'quote':clause[:500]}
    req['query_text']=text[:500]  # transient request only, removed before persistence
    questions.extend(service_questions(req,today))
    return req,questions

def nearby(req):
    """Compatibility helper; all new calls go through the registry."""
    from .providers.adapters import AmapPoiAdapter
    execution=AmapPoiAdapter().execute(CapabilityRequest(
        capability='search_parking' if req.get('category')=='parking' else 'search_merchants',
        params={'query_text':req.get('query_text',''),'category':req.get('category','parking')},
    ))
    return execution.result.data | {'provider_trace':execution.provider_id}

def query(session,trip_id,text,expected_version=None,cancelled=None,*,intent=None,today=None,trip_dates=None):
    dropped=[]
    with connect() as db:
        row=state.owned(db,session,trip_id)
        if row['status'] in state.TERMINAL:raise HTTPException(409,detail='旅行已结束，请新建旅行。')
        w=read(db,trip_id);original_version=w['version'];trip_version=row['version']
        if expected_version is not None and expected_version!=original_version:raise HTTPException(409,detail='旅行服务条件已变化，请刷新。')
    if intent:
        req=semantic_requirements(intent,text,w['requirements'],today or datetime.now(TZ).date(),dropped)
        req['query_text']=text[:500]
    else:req,questions=parse(text,w['requirements'])
    inferred=[]
    if intent and req['category']=='hotels' and trip_dates:
        start,end=map(date.fromisoformat,trip_dates);span=(end-start).days
        if span>0:
            for key,value in (('check_in',start.isoformat()),('nights',span)):
                if not req.get(key) and not (key=='nights' and req.get('check_out')):
                    req[key]=value;inferred.append(key)
            stay_dates(req)
    if intent:questions=service_questions(req,today or datetime.now(TZ).date())
    if questions:result={'status':'needs_clarification','items':[],'questions':questions,'provider_trace':[]}
    else:
        capability='search_hotels' if req['category']=='hotels' else 'search_parking' if req['category']=='parking' else 'search_merchants'
        params={k:req[k] for k in ('check_in','check_out','max_price','rooms','guests') if k in req}
        params.update({'query_text':req.get('query_text',''), 'category':req['category']})
        execution=default_registry().execute(CapabilityRequest(
            capability=capability, params=params, requirements_version=original_version,
            requested_scope='trip_service_candidate',
        ))
        result=dict(execution.result.data)
        result.setdefault('status',execution.result.status)
        result.setdefault('items',[])
        result['provider_trace']=execution.provider_trace
        result['provider_id']=execution.result.provider_id
        result['capability']=capability
        result['evidence_status']=execution.result.evidence_status
        result['provider_scope']=execution.result.scope
        if execution.result.limitations:result['limitations']=execution.result.limitations
    if req['category']=='hotels' and req.get('max_price'):
        result['items']=[i for i in result.get('items',[]) if i['nightly_from_fen'] is None or i['nightly_from_fen']<=req['max_price']*100]
        if result.get('status')=='ok' and not result['items']:result['status']='empty'
    trace=result.get('provider_trace',[])
    attempts=[t.get('http_attempts') for t in trace]
    result['query_diagnostics']={'workflow_steps':1,'adapter_executions':sum(bool(t.get('attempted')) for t in trace),
        'http_attempts':sum(attempts) if all(type(v) is int for v in attempts) else None,
        'cache_hits':sum(bool(t.get('cache_hit')) for t in trace),'provider_status':result['status'],
        'normalized_candidate_count':len(result.get('items',[])),
        'execution_basis':'missing_user_fields' if questions else 'provider_registry'}
    req.pop('query_text',None)
    # Every unmet hard condition stays visible; search candidates are not matching offers.
    for item in result.get('items',[]):
        item['unverified_conditions']=[hotels.ATTRS[k] for k in req['attributes']]
        item['unverified_conditions'].extend(label+'（'+req[key]+'）' for key,label in [('room_type','房型'),('location_text','位置')] if req.get(key))
        evidence=[{'field':k,'label':hotels.ATTRS[k],'strength':a['strength'],'status':'unknown'} for k,a in req['attributes'].items()]
        evidence.extend({'field':k,'label':label+'（'+req[k]+'）','strength':req.get('field_strengths',{}).get(k,'hard'),'status':'unknown'}
            for k,label in [('room_type','房型'),('location_text','位置')] if req.get(k))
        item['condition_evidence']=evidence
        item['hard_conditions_verified']=not any(e['strength']=='hard' for e in evidence)
        item['condition_status']='unknown' if evidence else 'not_requested'
        item['budget_verified']=False
    result['requirements']=req;result['budget']={'known_total_fen':None,'feasibility':'unknown',
        'missing':['selected_room_rate','occupancy','parking','dining','tickets','transport']}
    with connect() as db:
        db.execute('BEGIN IMMEDIATE');row=state.owned(db,session,trip_id);fresh=read(db,trip_id)
        if fresh['version']!=original_version or row['version']!=trip_version:raise HTTPException(409,detail='查询期间行程或条件变化，请重新查询。')
        if cancelled and cancelled():raise HTTPException(409,detail='查询已取消，条件未保存。')
        w.update(version=original_version+1,requirements=req,result=result)
        write(db,trip_id,w)
    reply=''.join(questions) if questions else f"已查询到 {len(result.get('items',[]))} 个{'酒店' if req['category']=='hotels' else '地点'}候选。" if result.get('items') else '查询暂未取得候选（'+result['status']+'），可稍后重试。'
    if req['category']=='hotels':
        if inferred:
            day=date.fromisoformat(req['check_in'])
            reply+=f" 按行程 {day.month} 月 {day.day} 日入住、住 {req['nights']} 晚，如需调整请告诉我。"
        reply+=' 房型、设施和评价还没核实，价格以预订页面为准。'
        labels={'hard':'必须','soft':'最好','optional':'可选'}
        condition_parts=[]
        for field,data in req.get('attributes',{}).items():
            condition_parts.append(f"{hotels.ATTRS[field]}（{labels.get(data['strength'],data['strength'])}）")
        for field,label in (('room_type','房型'),('location_text','位置')):
            if req.get(field):condition_parts.append(f"{label}：{req[field]}（{labels.get(req.get('field_strengths',{}).get(field,'hard'),req.get('field_strengths',{}).get(field,'hard'))}）")
        if condition_parts:reply+=' 酒店条件：'+'、'.join(condition_parts)+'。'
        if req.get('max_price') is not None:reply+=f" 价格口径：每晚不超过 {req['max_price']} 元。"
        if req.get('stay_budget_fen') is not None:reply+=f" 价格口径：住宿合计不超过 {req['stay_budget_fen']//100} 元。"
    conditions=list(dict.fromkeys('“'+d['quote']+'”' if d['quote'] in text else '未听准的住宿条件' for d in dropped))
    if conditions:reply+='、'.join(conditions)+('这条' if len(conditions)==1 else '这几条')+'暂时没法按条件筛选，候选里需要自己核对。'
    return {'reply':reply,'services':w,'formal_state_changed':False,**({'inferred_fields':inferred} if inferred else {})}

class Query(Strict):
    text: str = Field(min_length=1,max_length=2000)
    expected_version: int = Field(ge=0)

class Choice(Strict):
    request_id: UUID
    expected_version: int = Field(ge=1)
    candidate_id: str = Field(min_length=1,max_length=160)
    rate_id: str | None = Field(default=None,min_length=1,max_length=100)
    room_id: str | None = Field(default=None,min_length=1,max_length=100)
    rooms: int = Field(default=1,ge=1,le=20)
    manual_price: StayPriceInput | None = None


def selected_price(item,requirements,body):
    if body.manual_price and body.rate_id:raise HTTPException(422,detail='请选择接口报价或手动价格中的一种。')
    if body.manual_price or body.rate_id:
        if requirements.get('category')!='hotels':raise HTTPException(422,detail='住宿价格只能用于酒店候选。')
        if not requirements.get('check_in') or not requirements.get('check_out'):raise HTTPException(422,detail='请先明确入住和退房日期。')
    if body.room_id and not body.rate_id:raise HTTPException(422,detail='房型需要对应的价格计划。')
    dates={k:requirements.get(k) for k in ('check_in','check_out')}
    if body.manual_price:
        return body.manual_price.model_dump()|dates|{'basis':'reported','source':'用户提供的住宿预算价格','expires_at':None}
    if not body.rate_id:return None
    rates=[r for r in item.get('rate_plans',[]) if r['rate_id']==body.rate_id and r['room_id']==body.room_id]
    if len(rates)!=1:raise HTTPException(422,detail='报价或房型不存在，请重新选择。')
    rate=rates[0];quote=rate.get('quote') or {}
    if rate.get('currency')!='CNY' or any(quote.get(k)!=dates[k] for k in dates):
        raise HTTPException(422,detail='报价币种或入住日期不适用，请重新查询。')
    if quote.get('price_unit') not in ('per_room_night','per_room_stay','party_stay'):
        raise HTTPException(422,detail='接口未明确报价计价单位，请核对后手动填写住宿价格。')
    nights=(date.fromisoformat(dates['check_out'])-date.fromisoformat(dates['check_in'])).days
    for key,too in (('min_stay_days',lambda n:nights<n),('max_stay_days',lambda n:nights>n)):
        value=rate.get(key)
        if value is not None:
            try:invalid=too(int(value))
            except (ValueError,TypeError):raise HTTPException(422,detail='报价入住晚数条件不明确。') from None
            if invalid:raise HTTPException(422,detail='入住晚数不符合价格计划条件。')
    if requirements.get('guests') and rate.get('max_occupancy') is not None:
        try:capacity=int(rate['max_occupancy'])*body.rooms
        except (ValueError,TypeError):raise HTTPException(422,detail='报价入住人数条件不明确。') from None
        if requirements['guests']>capacity:raise HTTPException(422,detail='所选房间数不足以容纳查询人数。')
    amount=quote.get('total_fen')
    if type(amount) is not int or not 0<=amount<=100000000:raise HTTPException(422,detail='报价金额无效。')
    return dates|{'rate_id':rate['rate_id'],'room_id':rate['room_id'],'unit':quote['price_unit'],
        'rooms':body.rooms,'max_occupancy':rate.get('max_occupancy'),'amount_fen':amount,'basis':'quoted','source':'FlyAI 所选房型价格计划',
        'expires_at':item['expires_at']}

class Confirm(Strict):
    request_id: UUID
    expected_version: int = Field(ge=1)
    preview_id: UUID
    confirmed: bool

def preview_choice(session,trip_id,body):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE');row=state.owned(db,session,trip_id);w=read(db,trip_id)
        data=body.model_dump(mode='json');scope='services:preview:'+str(trip_id)
        cached=state.receipt(db,session,body.request_id,scope,data)
        if cached is not None:return cached
        if row['status'] in state.TERMINAL:raise HTTPException(409,detail='旅行已结束。')
        if body.expected_version!=w['version']:raise HTTPException(409,detail='条件已变化，请重新选择。')
        item=next((i for i in (w['result'] or {}).get('items',[]) if i['id']==body.candidate_id),None)
        if not item or datetime.fromisoformat(item['expires_at'])<=datetime.now(TZ):raise HTTPException(409,detail='候选不存在或已过期，请重新查询。')
        item=dict(item)
        price=selected_price(item,w['requirements'],body)
        if price:item['price_selection']=price
        result={'preview_id':str(uuid4()),'before':w['selection'],'after':item,'expected_version':w['version'],
                'trip_version':row['version'],'formal_route_changed':False,'planning_status':'needs_data',
                'affected':['住宿费用','出发地点与转场','后续时间安排'],
                'blockers':['房型与入住人数报价待核实','地点与入口尚未映射正式事实','正式交通依据待核实'],
                'expires_at':item['expires_at']}
        if w['requirements'].get('category')=='hotels':
            result['binding_preview']=hotel_binding.describe(db,row,w,item)
        w['result']['preview']=result;write(db,trip_id,w)
        state.remember(db,session,body.request_id,trip_id,scope,data,result)
        return result


@router.get('')
def workspace(trip_id:UUID,s:Session=Depends(current)):
    with connect() as db:state.owned(db,s,trip_id);return read(db,trip_id)

@router.post('/query')
def search(trip_id:UUID,body:Query,s:Session=Depends(current)):
    limit('travel-services:'+s.id,20)
    return query(s,trip_id,body.text,body.expected_version)

@router.post('/preview')
def preview(trip_id:UUID,body:Choice,s:Session=Depends(current)):
    return preview_choice(s,trip_id,body)

@router.post('/confirm')
def confirm(trip_id:UUID,body:Confirm,s:Session=Depends(current)):
    data=body.model_dump(mode='json');scope='services:confirm:'+str(trip_id)
    with connect() as db:
        db.execute('BEGIN IMMEDIATE');row=state.owned(db,s,trip_id)
        cached=state.receipt(db,s,body.request_id,scope,data)
        if cached is not None:return cached
        w=read(db,trip_id);p=(w['result'] or {}).get('preview')
        if row['status'] in state.TERMINAL or not body.confirmed or w['version']!=body.expected_version or not p or p['preview_id']!=str(body.preview_id) or p['trip_version']!=row['version'] or datetime.fromisoformat(p['expires_at'])<=datetime.now(TZ):raise HTTPException(409,detail='预览已过期或条件变化，请重新预览。')
        old=dict(w['selection'] or {})
        selected=dict(p['after'])
        if w['requirements']['category']=='hotels':
            selected['stay']={k:w['requirements'].get(k) for k in ('check_in','check_out','nights')}
            selected['selection_version']=w['version']+1
        w['selection']=old|{w['requirements']['category']:selected};w['previous']=old
        w['version']+=1;w['result'].pop('preview',None);write(db,trip_id,w)
        binding=hotel_binding.describe(db,row,w)
        result={'workspace':w,'formal_state_changed':False,'planning_status':'needs_data',
                'reply':binding['message'] if binding else '已记下这个意向，资料补齐后就能排进行程。'}
        if binding:result['binding']=binding
        state.remember(db,s,body.request_id,trip_id,scope,data,result);return result

class Undo(Strict):
    expected_version: int = Field(ge=1)

@router.post('/undo')
def undo(trip_id:UUID,body:Undo,s:Session=Depends(current)):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE');row=state.owned(db,s,trip_id);w=read(db,trip_id)
        if row['status'] in state.TERMINAL or w['version']!=body.expected_version or w['previous'] is None:raise HTTPException(409,detail='没有可恢复的候选或版本已变化。')
        w['selection'],w['previous']=w['previous'],w['selection'];w['version']+=1
        if w['result']:w['result'].pop('preview',None)
        write(db,trip_id,w);return w
