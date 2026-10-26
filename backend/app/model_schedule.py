"""Nonblocking, process-local model admission. No sleepers, credentials or retries.

A request that is not admitted gets ModelWaiting with the next allowed time; the agent
task keeps running and retries on its next step, so waiting happens in the task loop.
"""
import math
import os
import re
import threading
import time
from collections import deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from fastapi import HTTPException


class ModelWaiting(HTTPException):
    def __init__(self,detail):super().__init__(429,detail=detail)


def acceptance_seconds():
    """Operator-only switch, restricted to an explicitly isolated development/test DB."""
    if os.getenv('TOURISM_AGENT_MODE','product')!='live_acceptance':return 45
    root=os.getenv('TOURISM_ACCEPTANCE_DB_ROOT','');db=os.getenv('DATABASE_PATH','')
    if (os.getenv('TOURISM_ENV') not in ('test','development') or
        os.getenv('TOURISM_ENABLE_LIVE_ACCEPTANCE')!='1' or not root or not db or
        Path(root).resolve()==Path(root).resolve().parent or
        not Path(db).resolve().is_relative_to(Path(root).resolve())):
        raise HTTPException(503,detail={'code':'ISOLATED_ACCEPTANCE_REQUIRED'})
    seconds=float(os.getenv('TOURISM_ACCEPTANCE_SECONDS','240'))
    if not 180<=seconds<=240:raise HTTPException(503,detail={'code':'INVALID_ACCEPTANCE_BUDGET'})
    return seconds


def retry_seconds(value,wall):
    if not value:return 0
    try:
        if str(value).isdigit():return int(value)
        stamp=parsedate_to_datetime(value)
        if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
        return max(0,math.ceil((stamp-wall).total_seconds()))
    except (ValueError,TypeError,OverflowError):return 0


class ModelScheduler:
    def __init__(self,*,rpm=3,interval=25.0,tpm=None,tpd=None,max_attempts=None,concurrency=1,
                 clock=time.monotonic,wall=lambda:datetime.now(timezone.utc)):
        if rpm<1 or interval<0 or not 1<=concurrency<=16 or (tpm is not None and tpm<1) or (tpd is not None and tpd<1):raise ValueError('invalid model limits')
        self.rpm=rpm;self.interval=max(interval,60/rpm+0.1);self.tpm=tpm;self.tpd=tpd
        self.max_attempts=max_attempts;self.clock=clock;self.wall=wall;self.lock=threading.RLock()
        self.records=deque();self.last=None;self.inflight=0;self.concurrency=concurrency;self.cool_until=0;self.stopped=None
        self.attempts=0;self.day=None;self.day_tokens=0
        self.stats={'scope':'single_python_process','organization_global':False,'rpm':rpm,'interval_seconds':self.interval,
                    'concurrency_limit':concurrency,'tpm':tpm,'tpd':tpd,'token_basis':'conservative_utf8_reservation_then_usage',
                    'unknown_provider_token_limits':tpm is None or tpd is None,'automatic_retries':0}

    def _prune(self,now):
        while self.records and self.records[0]['at']<=now-60:self.records.popleft()
        today=self.wall().date()
        if today!=self.day:self.day=today;self.day_tokens=0

    def _wait(self,now,next_at,reason):
        delay=max(0,next_at-now)
        return {'code':'MODEL_WAITING','reason':reason,'http_attempts':0,'retry_after_seconds':round(delay,3),
                'next_allowed_at':(self.wall()+timedelta(seconds=delay)).isoformat(),
                'scope':'single_python_process','organization_global':False}

    @contextmanager
    def permit(self,tokens):
        with self.lock:
            now=self.clock();self._prune(now)
            if self.stopped:raise HTTPException(503,detail={'code':'MODEL_SCHEDULE_STOPPED','reason':self.stopped,'http_attempts':0})
            if self.max_attempts is not None and self.attempts>=self.max_attempts:
                raise HTTPException(503,detail={'code':'MODEL_REQUEST_BUDGET','http_attempts':0})
            if (self.tpm is not None and tokens>self.tpm) or (self.tpd is not None and tokens>self.tpd):
                raise HTTPException(503,detail={'code':'MODEL_TOKEN_BUDGET','reason':'single_request_exceeds_configured_limit','http_attempts':0})
            due=self.cool_until;reason='provider_cooldown'
            if self.last is not None and self.last+self.interval>due:due=self.last+self.interval;reason='request_spacing'
            if len(self.records)>=self.rpm and self.records[-self.rpm]['at']+60.01>due:
                due=self.records[-self.rpm]['at']+60.01;reason='rpm_window'
            if self.tpm is not None:
                total=sum(r['tokens'] for r in self.records)
                for r in self.records:
                    if total+tokens<=self.tpm:break
                    total-=r['tokens']
                    if r['at']+60.01>due:due=r['at']+60.01;reason='tpm_window'
            if self.tpd is not None and self.day_tokens+tokens>self.tpd:
                tomorrow=datetime.combine(self.day+timedelta(days=1),datetime.min.time(),timezone.utc)
                due=max(due,now+(tomorrow-self.wall()).total_seconds());reason='local_utc_daily_tokens'
            if self.inflight>=self.concurrency:due=max(due,now+1);reason='concurrency'
            if due>now:raise ModelWaiting(self._wait(now,due,reason))
            self.inflight+=1;self.last=now;self.attempts+=1;self.day_tokens+=tokens
            record={'at':now,'tokens':tokens,'day':self.day,'sequence':self.attempts,'started_at':self.wall().isoformat()}
            self.records.append(record)
        try:yield record
        finally:
            with self.lock:self.inflight-=1

    def observe(self,record,response):
        with self.lock:
            now=self.clock();wall=self.wall()
            try:raw=response.json()
            except (ValueError,TypeError):raw={}
            if not isinstance(raw,dict):raw={}
            usage=raw.get('usage',{})
            # Incomplete/absent usage retains the reservation, never releases it as zero.
            if isinstance(usage,dict) and type(usage.get('total_tokens')) is int and usage['total_tokens']>=0:
                actual=usage['total_tokens'];delta=actual-record['tokens'];record['tokens']=actual
                if record['day']==self.day:self.day_tokens+=delta
            error=raw.get('error',{})
            kind=error.get('type') if isinstance(error,dict) else None
            if response.status_code in (401,403) or kind=='exceeded_current_quota_error':
                self.stopped='authorization' if response.status_code in (401,403) else 'quota'
            if response.status_code==429:
                limit=re.search(r'organization max (RPM|TPM|TPD)\s*:\s*(\d+)',str(error.get('message','')) if isinstance(error,dict) else '',re.I)
                if limit and 0<int(limit[2])<=100000000:
                    metric,value=limit[1].upper(),int(limit[2])
                    if metric=='RPM':self.rpm=min(self.rpm,value);self.interval=max(self.interval,60/self.rpm+0.1)
                    if metric=='TPM':self.tpm=min(self.tpm,value) if self.tpm is not None else value
                    if metric=='TPD':self.stopped='provider_daily_token_limit'
                self.cool_until=max(self.cool_until,now+max(60,retry_seconds(response.headers.get('retry-after'),wall)))
            # Whitelisted numeric headers only. Never persist headers or credential-derived keys.
            for name,attribute in (('x-ratelimit-limit-requests','rpm'),('x-ratelimit-limit-tokens','tpm')):
                value=response.headers.get(name,'')
                if value.isdigit() and 0<int(value)<=100000000:
                    if attribute=='rpm':
                        self.rpm=min(self.rpm,int(value));self.interval=max(self.interval,60/self.rpm+0.1)
                    else:self.tpm=min(self.tpm,int(value)) if self.tpm is not None else int(value)
            if response.headers.get('x-ratelimit-remaining-requests')=='0':
                self.cool_until=max(self.cool_until,now+max(60,retry_seconds(response.headers.get('retry-after'),wall)))

    def describe(self):
        with self.lock:
            return {**self.stats,'rpm':self.rpm,'interval_seconds':self.interval,'tpm':self.tpm,'tpd':self.tpd,
                    'unknown_provider_token_limits':self.tpm is None or self.tpd is None,
                    'attempts':self.attempts,'inflight':self.inflight,'stopped':self.stopped}


_scheduler=None
_init_lock=threading.Lock()


def scheduler():
    global _scheduler
    with _init_lock:
        if _scheduler is None:
            def optional(name):return int(os.environ[name]) if os.getenv(name,'').strip() else None
            _scheduler=ModelScheduler(rpm=int(os.getenv('TOURISM_LLM_RPM','3')),
                interval=float(os.getenv('TOURISM_LLM_INTERVAL_SECONDS','25')),
                tpm=optional('TOURISM_LLM_TPM'),tpd=optional('TOURISM_LLM_TPD'),
                concurrency=int(os.getenv('TOURISM_LLM_CONCURRENCY','').strip() or 1))
        return _scheduler


def request(client,method,url,*,headers,json=None):
    # Both V3 and language._call use this exact gate. No sleeping, retries, SQL or visitors here.
    tokens=sum(len(str(m.get('content','')).encode('utf-8'))+64 for m in (json or {}).get('messages',[]))+(json or {}).get('max_tokens',0)
    gate=scheduler()
    with gate.permit(tokens) as permit:
        response=client.request(method,url,headers=headers,json=json)
        gate.observe(permit,response)
        response.extensions['tourism_schedule']={'request_sequence':permit['sequence'],'started_at':permit['started_at'],'scope':'single_python_process'}
        return response
