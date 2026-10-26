"""One HTTP attempt per call. Only bounded, sanitized diagnostics may leave here."""
import json
import os
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
import httpx
from fastapi import HTTPException
from ... import language, model_schedule


def safe_text(value, limit=240):
    if not isinstance(value, str):return None
    key=os.getenv('TOURISM_LLM_API_KEY','').strip()
    if key:value=value.replace(key,'[redacted]')
    value=re.sub(r'(?i)(?:bearer\s+\S+|(?:sk|ak|org)-[\w-]+|(?:cookie|authorization)\s*[:=][^\r\n]+)', '[redacted]', value)
    value=re.sub(r'https?://\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]+|[A-Za-z0-9_=-]{32,}', '[redacted]', value)
    return ' '.join(value.split())[:limit]


def safe_request_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',value):return None
    key=os.getenv('TOURISM_LLM_API_KEY','').strip()
    if (key and key in value) or 'sk-' in value.lower():return '[redacted]'
    return value


def retry_after(value):
    """Retain safe raw header plus parsed seconds; never sleep/retry in this adapter."""
    raw=safe_text(value,100)
    if raw is None:return None,None
    try:
        if re.fullmatch(r'\d+',raw):return raw,int(raw)
        moment=parsedate_to_datetime(raw)
        if moment.tzinfo is None:moment=moment.replace(tzinfo=timezone.utc)
        return raw,max(0,round((moment-datetime.now(timezone.utc)).total_seconds()))
    except (ValueError,TypeError,OverflowError):return raw,None


def generate(system, payload, *, timeout=12.0, max_tokens=1400):
    if not language.available():raise HTTPException(503,detail='模型未配置，使用确定性流程。')
    if len(system)+len(json.dumps(payload,ensure_ascii=False)) > 12000:raise HTTPException(422,detail='模型输入超过预算。')
    model=os.environ['TOURISM_LLM_MODEL'].strip()
    body={'model':model,'max_tokens':max_tokens,'response_format':{'type':'json_object'},
          'messages':[{'role':'system','content':system},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}]}
    if model=='qwen3.7-plus' or model.startswith('qwen3.7-plus-'):
        body['enable_thinking']=False
        body['temperature']=0
    elif model=='kimi-k2.6':body['thinking']={'type':'disabled'}
    elif model=='kimi-k3':body['reasoning_effort']='low'
    elif model not in ('kimi-k2.7-code','kimi-k2.7-code-highspeed'):body['temperature']=0
    started=time.monotonic()
    meta={'source':'live_model','model':safe_text(model,100),
          'endpoint_host':urlsplit(os.environ['TOURISM_LLM_BASE_URL']).hostname,
          'http_attempts':0,'automatic_retries':0,'usage':{},'cost_cny':None,
          'cost_basis':'not_calculated_no_price_snapshot'}
    def fail(code,**details):
        raise HTTPException(503,detail={'code':code,**meta,**details,
            'elapsed_ms':round((time.monotonic()-started)*1000,2)}) from None
    try:
        # httpx default transport has retries=0; no SDK, redirects, retry loop or event hooks.
        with httpx.Client(timeout=httpx.Timeout(timeout),follow_redirects=False) as client:
            try:
                response=model_schedule.request(client,'POST',os.environ['TOURISM_LLM_BASE_URL'].rstrip('/')+'/chat/completions',
                headers={'Authorization':'Bearer '+os.environ['TOURISM_LLM_API_KEY']},json=body)
            except httpx.HTTPError:
                meta['http_attempts']=1;raise
            meta['http_attempts']=1
            meta['schedule']=response.extensions.get('tourism_schedule')
            meta['upstream_status']=response.status_code
            meta['request_id']=safe_request_id(response.headers.get('x-request-id') or response.headers.get('request-id'))
            if response.is_error:
                try:error=response.json().get('error',{})
                except (ValueError,AttributeError):error={}
                if not isinstance(error,dict):error={}
                raw,seconds=retry_after(response.headers.get('retry-after'))
                kind=safe_text(error.get('type'),100)
                classification={'engine_overloaded_error':'overloaded','rate_limit_reached_error':'rate_limit',
                    'exceeded_current_quota_error':'quota'}.get(kind,'unknown') if response.status_code==429 else 'not_429'
                limit=re.search(r'organization max (RPM|TPM|TPD)\s*:\s*(\d+)',str(error.get('message','')),re.I)
                fail('MODEL_HTTP_ERROR',error_type=kind,error_summary=safe_text(error.get('message')),
                     limit_metric=limit[1].upper() if limit else None,limit_value=int(limit[2]) if limit else None,
                     retry_after=raw,retry_after_seconds=seconds,limit_classification=classification)
            response.raise_for_status();raw=response.json()
            usage=raw.get('usage',{})
            if isinstance(usage,dict):meta['usage']={k:v for k,v in usage.items() if k in ('prompt_tokens','completion_tokens','total_tokens') and type(v) is int and v>=0}
            choice=raw['choices'][0];message=choice['message']
            if choice.get('finish_reason')!='stop' or message.get('tool_calls'):raise ValueError('incomplete or unsupported native calls')
            content=message['content']
            if not isinstance(content,str) or len(content)>14000:raise ValueError('oversize')
            result=json.loads(content)
            if not isinstance(result,dict):raise ValueError('object required')
            return result,{**meta,'elapsed_ms':round((time.monotonic()-started)*1000,2)}
    except httpx.HTTPStatusError:fail('MODEL_HTTP_ERROR')
    except httpx.TimeoutException:fail('MODEL_TRANSPORT_ERROR',transport_error='timeout')
    except httpx.HTTPError:fail('MODEL_TRANSPORT_ERROR',transport_error='network')
    except (ValueError,KeyError,IndexError,TypeError,AttributeError):fail('MODEL_OUTPUT_INVALID')
