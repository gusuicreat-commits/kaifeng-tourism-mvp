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

ATTRS = {'window':'窗户','external_window':'外窗','dry_wet':'干湿分离','breakfast':'早餐','free_parking':'免费停车',
         'charging':'充电设施','clean':'卫生','quiet':'隔音','cancellation':'取消政策','accessible':'无障碍'}

def safe_url(value, image=False):
    if not value:return None
    p=urlsplit(value)
    domains=('alicdn.com',) if image else ('feizhu.com','fliggy.com','fly.ai')
    return value if p.scheme=='https' and not p.username and not p.password and any(p.hostname==d or (p.hostname or '').endswith('.'+d) for d in domains) else None

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
        item=Hotel(id='flyai:'+r.shId,name=r.name,address=r.address,price_display=r.price,
                   nightly_from_fen=int(Decimal(price[1])*100) if price else None,
                   image=safe_url(r.mainPic,True),detail_url=safe_url(r.detailUrl),category=r.star,
                   checked_at=moment.isoformat(),expires_at=(moment+timedelta(minutes=3)).isoformat(),
                   attributes={k:{'label':v,'status':'unknown','value':None,'source_field':None,
                                  'scope':'selected_room_or_rate_plan','checked_at':moment.isoformat()} for k,v in ATTRS.items()})
        items.append(item.model_dump())
    return {'status':'ok' if items else 'empty' if not data['itemList'] else 'invalid_response','items':items,
            'system_message':str(raw['systemMessage'])[:500] if raw.get('systemMessage') else None,
            'reviews_status':'not_available','provider':'FlyAI','queried_at':moment.isoformat()}

def search(query: Search):
    if os.getenv('FLYAI_ENABLED','').lower() not in ('1','true','yes') or not os.getenv('FLYAI_API_KEY','').strip():return {'status':'not_configured','items':[]}
    path=Path(os.getenv('FLYAI_CLI_PATH','.runtime/flyai/flyai.cjs'))
    if not path.is_absolute():path=ROOT/path
    node=shutil.which('node')
    if not path.is_file() or not node:return {'status':'cli_missing','items':[]}
    key=(os.getenv('FLYAI_API_KEY'),query.model_dump_json())
    with LOCK:
        cached=CACHE.get(key)
        if cached and time.monotonic()-cached[0]<180:return json.loads(json.dumps(cached[1]))|{'cache_hit':True}
    if not GATE.acquire(blocking=False):return {'status':'busy','items':[]}
    try:
        global LAST
        with LOCK:
            if time.monotonic()-LAST<1:return {'status':'rate_limited','items':[]}
            LAST=time.monotonic()
        args=[node,str(path),'search-hotel','--dest-name','开封','--check-in-date',query.check_in,'--check-out-date',query.check_out]
        if query.max_price:args+=['--max-price',str(query.max_price)]
        # Ignore ambient CLI debug/config overrides; this provider only contacts official FlyAI.
        env={k:v for k,v in os.environ.items() if not k.startswith(('DEBUG_FLYAI','FLYAI_'))}
        env['FLYAI_API_KEY']=os.environ['FLYAI_API_KEY']
        try:
            p=subprocess.run(args,env=env,capture_output=True,text=True,timeout=12,check=False)
            if p.returncode:return {'status':'provider_unavailable','items':[]}
            if len(p.stdout)>2000000:return {'status':'invalid_response','items':[]}
            result=normalize(json.loads(p.stdout))
        except subprocess.TimeoutExpired:return {'status':'timeout','items':[]}
        except (OSError,ValueError,TypeError):return {'status':'invalid_response','items':[]}
        result['query']=query.model_dump();result['occupancy_verified']=False
        if result['status'] in ('ok','empty'):
            with LOCK:
                if len(CACHE)>=64:CACHE.clear()
                CACHE[key]=(time.monotonic(),result)
        return result
    finally:GATE.release()
