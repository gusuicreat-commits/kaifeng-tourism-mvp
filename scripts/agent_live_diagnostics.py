"""Serial evaluation-only admission control. Never retries a rejected request."""
import time
from datetime import datetime, timezone


class ModelCallBudget:
    def __init__(self, generate, cap=12, interval=2.0, *, clock=time.monotonic, sleep=time.sleep):
        self.generate=generate;self.cap=cap;self.interval=interval;self.clock=clock;self.sleep=sleep
        self.calls=[];self.blocked=None;self.case='preflight'

    def __call__(self,*args,**kwargs):
        if self.blocked:raise RuntimeError('Online evaluation stopped after provider failure')
        if len(self.calls)>=self.cap:raise RuntimeError('Evaluation model call cap reached')
        if self.calls:
            remaining=self.interval-(self.clock()-self.calls[-1]['started_monotonic'])
            if remaining>0:self.sleep(remaining)
        started=self.clock();payload=args[1]
        row={'sequence':len(self.calls)+1,'case':self.case,'source':'mock_model',
             'phase':'decision' if 'eligible_actions' in payload else 'understand' if 'text' in payload else 'preflight',
             'started_at':datetime.now(timezone.utc).isoformat(),'started_monotonic':started,
             'interval_since_previous_start_ms':round((started-self.calls[-1]['started_monotonic'])*1000,2) if self.calls else None,
             'status':'requested','eligible_actions':payload.get('eligible_actions',[])}
        self.calls.append(row)
        try:
            result,meta=self.generate(*args,**kwargs)
            meta={**meta,'evaluation_request_sequence':row['sequence']}
            row.update(meta,status='passed')
            if row['phase']=='decision':row['returned_action']={k:v for k,v in result.items() if k in ('tool','place_id','relax_optional_food')}
            return result,meta
        except Exception as exc:
            detail=getattr(exc,'detail',None)
            if isinstance(detail,dict) and detail.get('code')=='MODEL_WAITING' and detail.get('http_attempts')==0:
                self.calls.pop();raise
            row.update(status='failed',elapsed_ms=round((self.clock()-started)*1000,2))
            if isinstance(detail,dict):row['source']=detail.get('source','mock_model');row['failure']=detail;row.update({k:detail[k] for k in ('usage','upstream_status','error_type','request_id','retry_after','retry_after_seconds','limit_classification') if k in detail})
            else:row['failure']={'code':'EVALUATION_OR_PROVIDER_FAILURE'}
            # No automatic retry, including 429. Further cases must be a separate explicit run.
            self.blocked=row['failure'];raise

    @property
    def http_attempt_count(self):
        return sum(row.get('http_attempts',row.get('failure',{}).get('http_attempts',0)) for row in self.calls)

    def records(self):return [{k:v for k,v in row.items() if k!='started_monotonic'} for row in self.calls]
