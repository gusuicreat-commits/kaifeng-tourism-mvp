"""Translate validated semantic fields to existing executors without parsing text again."""
import re
from datetime import datetime
from ..assistant.models import Intent
from ..schema import TZ
from .evidence import request_evidence, effective_clock


def read_action(action,snapshot):
    if not action.purpose:
        return None
    return Intent(operation='query',purpose=action.purpose)


def grounded_state(value,text):
    from .understand import clock
    request_evidence(text,value.quote)
    if value.target_name and (value.target_name not in value.quote):raise ValueError('state target absent')
    if value.start_time:
        if not value.start_quote or value.start_quote not in value.quote:raise ValueError('state time evidence absent')
        request_evidence(text,value.start_quote)
        if effective_clock(text,value.start_quote,clock)!=value.start_time:raise ValueError('invented state start')
    if value.restore_version is not None:
        if value.operation!='restore' or not re.search(r'(?:第)?'+str(value.restore_version)+r'\s*版',value.quote):raise ValueError('restore version absent')
    return value


def state_action(value,snapshot):
    fields={'operation':value.operation,'purpose':'history' if value.operation=='restore' else 'progress','execute':True}
    if value.operation=='cancel':fields['purpose']='modify'
    if value.operation=='restore':fields['restore_route_version']=value.restore_version
    if value.operation=='position':
        matches=[p.fact_id for (kind,_),p in snapshot.planning.facts.items() if kind=='place' and p.rights_status=='permitted' and p.name==value.target_name]
        fields['place_id']=matches[0] if len(matches)==1 else None
    if value.operation in ('complete','skip'):
        from ..assistant.context import label
        pending=[s for s in snapshot.draft.stops if s.progress=='pending']
        matches=[s for s in pending if label(snapshot.planning.facts,s.place_id)==value.target_name] if value.target_name else []
        fields['stop_id']=matches[0].stop_id if len(matches)==1 else None
        if value.start_time and snapshot.draft.business_date:
            fields['actual_start']=datetime.combine(snapshot.draft.business_date,datetime.min.time(),TZ).replace(hour=int(value.start_time[:2]),minute=int(value.start_time[3:]))
    return Intent.model_validate(fields)
