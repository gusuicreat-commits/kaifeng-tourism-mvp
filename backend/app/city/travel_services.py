"""Trip-scoped service workspace. Provider candidates never bypass the city fact gate."""
import json
import re
from datetime import date, datetime, timedelta
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field
from .schema import Strict, TZ
from . import hotels, state, location_resolution  # compatibility export for existing adapter fakes
from .providers import CapabilityRequest, default_registry
from ..db import connect
from ..memory.session import Session, current, limit
from ..memory.storage import encoded, stamp

router=APIRouter(prefix='/api/city/v1/trips/{trip_id}/services',tags=['旅行服务候选'])

def init(db):
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

NUMBER = r'(\d+|[零一二两三四五六七八九十]+)'

def number(value):
    if value.isdigit():return int(value)
    digits={c:i for i,c in enumerate('零一二三四五六七八九')};digits['两']=2
    if '十' in value:
        left,right=value.split('十',1)
        return (digits[left] if left else 1)*10+(digits[right] if right else 0)
    return digits[value]

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

def semantic_requirements(intent,text,previous,today):
    """Apply only typed, current-turn service fields to the scoped workspace."""
    from .agent.planning_intent import resolve_date
    from .agent.evidence import query_evidence, service_condition_evidence
    req=json.loads(json.dumps(previous or {}));req['category']=intent.category
    attrs=req.setdefault('attributes',{});seen={};strengths=req.setdefault('field_strengths',{})
    query_evidence(text,intent.quote)
    for f in intent.fields:
        service_condition_evidence(text,f.quote,intent.quote,literal_name=f.field in ('location_text','room_type'))
        if f.field in seen and seen[f.field]!=f.value:raise ValueError('conflicting service fields')
        seen[f.field]=f.value
        optional=bool(re.search(r'都行|无所谓|没有也行|不强求|可选|不要求|不需要',f.quote))
        hard=bool(re.search(r'必须|一定|不能缺|不可少',f.quote))
        if optional and f.strength!='optional':raise ValueError('optional condition promoted')
        if hard and not optional and f.strength!='hard':raise ValueError('hard condition weakened')
        strength=f.strength
        prior=attrs.get(f.field,{}).get('strength',strengths.get(f.field))
        # A new preference is not permission to silently waive an earlier must.
        if prior=='hard' and strength!='hard' and not optional:strength='hard'
        strengths[f.field]=strength
        if f.field in ('check_in','check_out'):
            if not isinstance(f.value,str) or f.value not in f.quote:raise ValueError('service date absent')
            req[f.field]=resolve_date(f.value,today).isoformat()
        elif f.field in ('nights','guests','rooms','max_price','stay_budget_fen'):
            limits={'nights':30,'guests':100,'rooms':20,'max_price':100000,'stay_budget_fen':100000000}
            if type(f.value) is not int or not 1<=f.value<=limits[f.field]:raise ValueError('invalid service number')
            # Bind a value to its unit/scope; finding the same number elsewhere
            # in a broad quote is insufficient evidence for that field.
            units={'nights':r'\s*(?:个)?\s*(?:晚上|晚|夜)', 'guests':r'\s*(?:个)?\s*(?:人|位)',
                   'rooms':r'\s*间(?:房)?', 'max_price':r'\s*元', 'stay_budget_fen':r'\s*(?:元)?'}
            expected=f.value//100 if f.field=='stay_budget_fen' else f.value
            if f.field=='stay_budget_fen' and f.value%100:raise ValueError('fractional budget evidence unsupported')
            numeric=[m for m in re.finditer(NUMBER+units[f.field],f.quote) if number(m[1])==expected]
            if not numeric:raise ValueError('service number or unit absent')
            if f.field=='max_price':
                if not re.search(r'每晚|一晚|房价|酒店预算|\d+\s*元(?:以内|以下)',f.quote):raise ValueError('nightly money scope absent')
                if re.search(r'(?:两|二|\d+)\s*(?:天|晚).{0,8}预算|预算.{0,8}(?:两|二|\d+)\s*(?:天|晚)|总(?:预算|价)|合计|一共|整趟|全程',f.quote):raise ValueError('stay budget is not nightly price')
            if f.field=='stay_budget_fen':
                if not re.search(r'预算|费用|合计',f.quote):raise ValueError('stay money scope absent')
                if re.search(r'整趟|全程|旅行总预算|每晚|一晚|房价',f.quote):raise ValueError('money scope differs from stay')
            req[f.field]=f.value
        elif f.field in ('location_text','room_type'):
            if not isinstance(f.value,str) or not f.value.strip() or f.value not in f.quote or len(f.value)>120:raise ValueError('service text absent')
            req[f.field]=f.value
        elif f.field in hotels.ATTRS:
            if type(f.value) is not bool:raise ValueError('service attribute must be boolean')
            aliases={'window':r'窗户|有窗','external_window':r'外窗','clean':r'卫生|干净'}.get(f.field,re.escape(hotels.ATTRS[f.field]))
            if not re.search(aliases,f.quote):raise ValueError('service attribute absent')
            if re.search(r'都行|无所谓|没有也行|不强求',f.quote) and f.strength!='optional':raise ValueError('optional attribute promoted')
            if re.search(r'必须|一定',f.quote) and f.strength!='hard':raise ValueError('hard attribute weakened')
            negative=bool(re.search(r'(?:不要|不含|没有|不带|无).{0,3}(?:'+aliases+r')',f.quote))
            if not optional and f.value==negative:raise ValueError('attribute polarity differs from evidence')
            attrs[f.field]={'value':f.value,'strength':strength,'quote':f.quote}
    # Field order cannot change the meaning of an explicit check-out override.
    if 'check_out' in seen and 'nights' not in seen:req.pop('nights',None)
    stay_dates(req)
    if 'check_out' in seen and req['check_out']!=resolve_date(seen['check_out'],today).isoformat():raise ValueError('stay dates conflict')
    return req

def validate_intent(intent,text,snapshot):
    return semantic_requirements(intent,text,snapshot.services.get('requirements',{}),snapshot.envelope.identity.generated_at.astimezone(TZ).date())

def parse(text,previous=None):
    req=json.loads(json.dumps(previous or {}));today=datetime.now(TZ).date();questions=[]
    category='hotels' if re.search(r'酒店|住宿|民宿',text) else 'parking' if '停车场' in text else 'merchants' if re.search(r'餐厅|饭店|美食|商家',text) else req.get('category','hotels')
    req['category']=category
    # Full dates and relative check-in dates. Never infer room count or guest count.
    dates=re.findall(r'20\d{2}-\d{2}-\d{2}',text)
    try:
        if dates:
            req['check_in']=date.fromisoformat(dates[0]).isoformat()
            if len(dates)>1:req['check_out']=date.fromisoformat(dates[1]).isoformat()
            elif not req.get('nights'):req.pop('check_out',None)
        else:
            relative=next((d for token,d in [('后天',2),('明天',1),('今天',0)] if token in text),None)
            if relative is not None:
                req['check_in']=(today+timedelta(days=relative)).isoformat()
                if not req.get('nights'):req.pop('check_out',None)
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

def query(session,trip_id,text,expected_version=None,cancelled=None,*,intent=None,today=None):
    with connect() as db:
        row=state.owned(db,session,trip_id)
        if row['status'] in state.TERMINAL:raise HTTPException(409,detail='旅行已结束，请新建旅行。')
        w=read(db,trip_id);original_version=w['version'];trip_version=row['version']
        if expected_version is not None and expected_version!=original_version:raise HTTPException(409,detail='旅行服务条件已变化，请刷新。')
    if intent:
        req=semantic_requirements(intent,text,w['requirements'],today or datetime.now(TZ).date())
        questions=service_questions(req,today or datetime.now(TZ).date())
        req['query_text']=text[:500]
    else:req,questions=parse(text,w['requirements'])
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
    reply='；'.join(questions) if questions else f"已查询到 {len(result.get('items',[]))} 个{'酒店' if req['category']=='hotels' else '地点'}候选。" if result.get('items') else '查询暂未取得候选（'+result['status']+'），可稍后重试。'
    reply+=' 房型设施、评价及完整预算尚未核实；候选选择不会改动正式路线。'
    return {'reply':reply,'services':w,'formal_state_changed':False}

class Query(Strict):
    text: str = Field(min_length=1,max_length=2000)
    expected_version: int = Field(ge=0)

class Choice(Strict):
    request_id: UUID
    expected_version: int = Field(ge=1)
    candidate_id: str = Field(min_length=1,max_length=160)

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
        result={'preview_id':str(uuid4()),'before':w['selection'],'after':item,'expected_version':w['version'],
                'trip_version':row['version'],'formal_route_changed':False,'planning_status':'needs_data',
                'affected':['住宿费用','出发地点与转场','后续时间安排'],
                'blockers':['房型与入住人数报价待核实','地点与入口尚未映射正式事实','正式交通依据待核实','多日住宿不在同日求解器范围内'],
                'expires_at':item['expires_at']}
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
        old=dict(w['selection'] or {});w['selection']=old|{w['requirements']['category']:p['after']};w['previous']=old
        w['version']+=1;w['result'].pop('preview',None);write(db,trip_id,w)
        result={'workspace':w,'formal_state_changed':False,'planning_status':'needs_data','reply':'已保存候选意向；正式路线未变，需补齐核验资料后才能由现有引擎规划。'}
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
