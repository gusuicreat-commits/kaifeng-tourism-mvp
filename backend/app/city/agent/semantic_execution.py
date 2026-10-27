"""Translate validated semantic fields to existing executors without parsing text again."""
import re
from datetime import datetime
from .. import facts
from ..assistant.models import Intent
from ..schema import TZ
from .evidence import request_evidence, effective_clock


def read_action(action,snapshot):
    if not action.purpose:
        return None
    return Intent(operation='query',purpose=action.purpose)


def grounded_state(value,text):
    from .understand import clock, duration_values
    request_evidence(text,value.quote)
    if value.target_name and (value.target_name not in value.quote):raise ValueError('state target absent')
    if value.operation in ('delay','extend','rest'):
        if value.minutes is None:raise ValueError('duration_required')
        if value.minutes not in duration_values(value.quote):raise ValueError('duration_not_in_quote')
    elif value.minutes is not None:
        raise ValueError('duration_without_change')
    if value.next_stop and value.operation!='skip_replan':raise ValueError('invalid next reference')
    if value.operation=='skip_replan' and not (value.target_name or value.next_stop):
        raise ValueError('state target required')
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
        resolved=facts.place_by_name(snapshot.planning.facts, value.target_name)
        fields['place_id']=resolved.fact_id if resolved else None
    if value.operation in ('complete','skip'):
        from ..assistant.context import label
        pending=[s for s in snapshot.draft.stops if s.progress=='pending']
        matches=[s for s in pending if label(snapshot.planning.facts,s.place_id)==value.target_name] if value.target_name else []
        fields['stop_id']=matches[0].stop_id if len(matches)==1 else None
        if value.start_time and snapshot.draft.business_date:
            fields['actual_start']=datetime.combine(snapshot.draft.business_date,datetime.min.time(),TZ).replace(hour=int(value.start_time[:2]),minute=int(value.start_time[3:]))
    return Intent.model_validate(fields)
