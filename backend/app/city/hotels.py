"""FlyAI boundary: official pinned CLI, never shell or provider JSON as domain state."""
import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from .schema import TZ

ROOT = Path(__file__).resolve().parents[3]
GATE = threading.BoundedSemaphore(2)
LOCK = threading.Lock()
CACHE = {}
LAST = 0.0

class Search(BaseModel):
    model_config = ConfigDict(extra='forbid')
    check_in: str
    check_out: str
    max_price: int | None = Field(default=None, ge=1, le=100000)
    rooms: int | None = Field(default=None, ge=1, le=20)
    guests: int | None = Field(default=None, ge=1, le=100)

class RawHotel(BaseModel):
    model_config = ConfigDict(extra='ignore', coerce_numbers_to_str=True)
    shId: str = Field(min_length=1,max_length=100)
    name: str = Field(min_length=1,max_length=200)
    address: str | None = Field(default=None,max_length=500)
    price: str | None = Field(default=None,max_length=80)
    mainPic: str | None = Field(default=None,max_length=2000)
    detailUrl: str | None = Field(default=None,max_length=2000)
    star: str | None = Field(default=None,max_length=80)

class Hotel(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str
    name: str
    address: str | None
    price_display: str | None
    nightly_from_fen: int | None
    price_basis: Literal['provider_display_starting_price'] = 'provider_display_starting_price'
    total_fen: None = None
    image: str | None
    detail_url: str | None
    category: str | None
    source: str = 'FlyAI search-hotel'
    scope: str = 'hotel_search_result_not_selected_room_or_rate_plan'
    checked_at: str
    expires_at: str
    attributes: dict
    # Optional real-time quote data. Search-only providers may legitimately leave
    # these empty; the normalizer never derives them from a display starting price.
    room_types: list[dict] = Field(default_factory=list)
    rate_plans: list[dict] = Field(default_factory=list)
    availability_status: Literal['quoted', 'unknown'] = 'unknown'

ATTRS = {'window':'窗户','external_window':'外窗','dry_wet':'干湿分离','breakfast':'早餐','free_parking':'免费停车',
         'charging':'充电设施','clean':'卫生','quiet':'隔音','cancellation':'取消政策','accessible':'无障碍'}

def safe_url(value, image=False):
    if not value:return None
    p=urlsplit(value)
    domains=('alicdn.com',) if image else ('feizhu.com','fliggy.com','fly.ai')
    return value if p.scheme=='https' and not p.username and not p.password and any(p.hostname==d or (p.hostname or '').endswith('.'+d) for d in domains) else None


def _text(value, limit=500):
    if value is None:return None
    if not isinstance(value,(str,int,float,bool)):return None
    value=' '.join(str(value).split())
    return value[:limit] or None


def _list(value, *keys):
    if isinstance(value,list):return value
    if isinstance(value,dict):
        for key in keys:
            candidate=value.get(key)
            if isinstance(candidate,list):return candidate
    return []


def _pick(mapping, *keys):
    if not isinstance(mapping,dict):return None
    for key in keys:
        if key in mapping:return mapping[key]
    return None


def _fen(value):
    """Accept provider integer-fen values without treating display yuan as totals."""
    if isinstance(value,bool):return None
    if isinstance(value,int) and 0<=value<=1000000000:return value
    if isinstance(value,str) and value.isdigit():
        parsed=int(value)
        return parsed if parsed<=1000000000 else None
    return None


def _quote(raw):
    if not isinstance(raw,dict):return None
    dates=_list(_pick(raw,'checkin_checkout_prices','checkInCheckoutPrices'),
                'check_in_check_out_price','checkInCheckOutPrice')
    date_item=dates[0] if dates and isinstance(dates[0],dict) else raw
    check_in=_text(_pick(date_item,'checkin_date','checkInDate'),40)
    check_out=_text(_pick(date_item,'checkout_date','checkOutDate'),40)
    total=_fen(_pick(date_item,'price','inclusive'))
    if not check_in or not check_out or total is None:return None
    promotions=_list(_pick(date_item,'promotions'),'promotion_price','promotionPrice')
    promotion_total=0
    for promotion in promotions:
        if isinstance(promotion,dict):promotion_total+=_fen(_pick(promotion,'amount')) or 0
    return {'check_in':check_in,'check_out':check_out,
            'stay_days':_pick(date_item,'stay_days','stayDays'),
            'total_fen':total,'original_total_fen':_fen(_pick(date_item,'original_price','originalPrice')),
            'promotion_fen':promotion_total or None,'price_basis':'inclusive',
            'breakfast_count':_pick(date_item,'breakfast_count','breakfastCount')}


def _room_and_rates(row):
    rooms=_list(_pick(row,'room_prices','roomPrices'),'sroom_price','sroomPrice')[:20]
    room_types=[];rate_plans=[]
    for room in rooms:
        if not isinstance(room,dict):continue
        room_id=_text(_pick(room,'srid','room_id','roomId'),100)
        name=_text(_pick(room,'name'),200)
        if not room_id or not name:continue
        room_types.append({'room_id':room_id,'name':name,
                           'bed_info':_text(_pick(room,'bed_json','bedJson')),
                           'window_info':_text(_pick(room,'window_json','windowJson'))})
        rates=_list(_pick(room,'rate_prices','ratePrices'),
                    'realtime_rate_price','rate_price','realtimeRatePrice')[:50]
        for rate in rates:
            if not isinstance(rate,dict):continue
            rate_id=_text(_pick(rate,'rate_id','rateId'),100)
            if not rate_id:continue
            item={'room_id':room_id,'rate_id':rate_id,
                  'rateplan_id':_text(_pick(rate,'rateplan_id','rateplanId'),100),
                  'item_id':_text(_pick(rate,'item_id','itemId'),100),
                  'currency':_text(_pick(rate,'currency_code','currencyCode'),12),
                  'max_occupancy':_pick(rate,'max_occupancy','maxOccupancy'),
                  'min_stay_days':_pick(rate,'min_stay_days','minStayDays'),
                  'max_stay_days':_pick(rate,'max_stay_days','maxStayDays'),
                  'min_advance_hours':_pick(rate,'min_advance_hour','minAdvanceHour'),
                  'max_advance_hours':_pick(rate,'max_advance_hour','maxAdvanceHour'),
                  'effective_start':_text(_pick(rate,'effective_start_time','effectiveStartTime'),40),
                  'effective_end':_text(_pick(rate,'effective_end_time','effectiveEndTime'),40),
                  'breakfast_count':_pick(rate,'breakfast_count','breakfastCount'),
                  'cancel_policy_desc':_text(_pick(rate,'cancel_policy_desc','cancelPolicyDesc')),
                  'order_url':safe_url(_pick(rate,'order_url','orderUrl')),
                  'quote':_quote(rate)}
            rate_plans.append(item)
    return room_types,rate_plans

def normalize(raw, checked_at=None):
    if not isinstance(raw,dict) or raw.get('status')!=0 or type(raw.get('status')) is not int:return {'status':'provider_error','items':[]}
    data=raw.get('data')
    if not isinstance(data,dict) or not isinstance(data.get('itemList'),list):return {'status':'invalid_response','items':[]}
    moment=checked_at or datetime.now(TZ);items=[];seen=set()
    for row in data['itemList'][:30]:
        try:r=RawHotel.model_validate(row)
        except ValidationError:continue
        if r.shId in seen:continue
        seen.add(r.shId)
        price=re.fullmatch(r'[¥￥]\s*(\d+(?:\.\d{1,2})?)',r.price or '')
        from decimal import Decimal
        room_types,rate_plans=_room_and_rates(row)
        item=Hotel(id='flyai:'+r.shId,name=r.name,address=r.address,price_display=r.price,
                   nightly_from_fen=int(Decimal(price[1])*100) if price else None,
                   image=safe_url(r.mainPic,True),detail_url=safe_url(r.detailUrl),category=r.star,
                   checked_at=moment.isoformat(),expires_at=(moment+timedelta(minutes=3)).isoformat(),
                   attributes={k:{'label':v,'status':'unknown','value':None,'source_field':None,
                                  'scope':'selected_room_or_rate_plan','checked_at':moment.isoformat()} for k,v in ATTRS.items()},
                   room_types=room_types,rate_plans=rate_plans,
                   availability_status='quoted' if any(r.get('quote') for r in rate_plans) else 'unknown')
        items.append(item.model_dump())
    return {'status':'ok' if items else 'empty' if not data['itemList'] else 'invalid_response','items':items,
            'system_message':str(raw['systemMessage'])[:500] if raw.get('systemMessage') else None,
            'reviews_status':'not_available','provider':'FlyAI','queried_at':moment.isoformat()}

def receipt(result, invocations, *, cache_hit=False):
    # The pinned CLI does not expose its internal HTTP count. Never substitute
    # a subprocess/tool invocation count for actual upstream HTTP attempts.
    return result|{'cache_hit':cache_hit,'transport':{'cli_invocations':invocations,
        'http_attempts':None if invocations else 0,
        'basis':'unobserved_inside_cli' if invocations else 'cache' if cache_hit else 'blocked_before_cli'}}

def search(query: Search):
    if os.getenv('FLYAI_ENABLED','').lower() not in ('1','true','yes') or not os.getenv('FLYAI_API_KEY','').strip():return receipt({'status':'not_configured','items':[]},0)
    path=Path(os.getenv('FLYAI_CLI_PATH','.runtime/flyai/flyai.cjs'))
    if not path.is_absolute():path=ROOT/path
    node=shutil.which('node')
    if not path.is_file() or not node:return receipt({'status':'cli_missing','items':[]},0)
    key=(os.getenv('FLYAI_API_KEY'),query.model_dump_json())
    with LOCK:
        cached=CACHE.get(key)
        if cached and time.monotonic()-cached[0]<180:return receipt(json.loads(json.dumps(cached[1])),0,cache_hit=True)
    if not GATE.acquire(blocking=False):return receipt({'status':'busy','items':[]},0)
    try:
        global LAST
        with LOCK:
            if time.monotonic()-LAST<1:return receipt({'status':'rate_limited','items':[]},0)
            LAST=time.monotonic()
        args=[node,str(path),'search-hotel','--dest-name','开封','--check-in-date',query.check_in,'--check-out-date',query.check_out]
        if query.max_price:args+=['--max-price',str(query.max_price)]
        # Ignore ambient CLI debug/config overrides; this provider only contacts official FlyAI.
        env={k:v for k,v in os.environ.items() if not k.startswith(('DEBUG_FLYAI','FLYAI_'))}
        env['FLYAI_API_KEY']=os.environ['FLYAI_API_KEY']
        try:
            p=subprocess.run(args,env=env,capture_output=True,text=True,timeout=12,check=False)
            if p.returncode:return receipt({'status':'provider_unavailable','items':[]},1)
            if len(p.stdout)>2000000:return receipt({'status':'invalid_response','items':[]},1)
            result=normalize(json.loads(p.stdout))
        except subprocess.TimeoutExpired:return receipt({'status':'timeout','items':[]},1)
        except (OSError,ValueError,TypeError):return receipt({'status':'invalid_response','items':[]},1)
        result['query']=query.model_dump();result['occupancy_verified']=False
        if result['status'] in ('ok','empty'):
            with LOCK:
                if len(CACHE)>=64:CACHE.clear()
                CACHE[key]=(time.monotonic(),result)
        return receipt(result,1)
    finally:GATE.release()
