"""Read projections of existing state, routes and M7 messages, in one authorized snapshot."""
import json
import time
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import HTTPException

from ...db import connect
from ...memory.context import redact
from ...memory.session import recheck
from ...memory.storage import now, stamp
from .. import facts, state
from ..engine import service as engine
from ..engine.models import Requirements, MultiDayRequirements, MultiDayItinerary
from ..state_schema import CityDraft, CityDraftV2
from ..versions import read_requirements, read_draft, read_result, read_itinerary
from .models import CityContext, Identity


@dataclass
class Snapshot:
    row: dict
    draft: CityDraft
    planning: object
    route: dict
    requirements: Requirements | None
    pending: list
    envelope: CityContext
    history_enabled: bool
    workspace: dict
    materials: list
    services: dict = field(default_factory=dict)
    multi_requirements: MultiDayRequirements | None = None
    multi_draft: CityDraftV2 | None = None
    multi_itinerary: MultiDayItinerary | None = None
    day_id: str | None = None


def label(items, place_id):
    item = items.get(('place', place_id))
    return redact(item.name)[:120] if item and facts.planning_visible(item) else '资料待核对的地点'


def recent(db, session, trip_id, enabled):
    if not enabled: return [], {'text':'','source_message_ids':[],'authoritative':False}, []
    scope = 'city_chat:' + str(trip_id)
    rows = db.execute('''SELECT id,role,content_redacted,sequence FROM conversation_messages
        WHERE session_id=? AND expires_at>? AND turn_id IN
        (SELECT request_id FROM city_receipts WHERE session_id=? AND trip_id=? AND (scope=? OR scope=?))
        ORDER BY sequence DESC LIMIT 24''', (session.id, stamp(), session.id, str(trip_id), scope, 'agent_chat:'+str(trip_id))).fetchall()
    latest = [{'id':r['id'],'role':r['role'],'text':r['content_redacted'][:500]} for r in reversed(rows[:8])]
    older = list(reversed(rows[8:]))
    # Rebuild from this trip's sources, never from another trip's session summary.
    summary = {'text':'\n'.join(r['role'] + ': ' + r['content_redacted'][:65] for r in older)[:600],
               'source_message_ids':[r['id'] for r in older], 'authoritative':False}
    references = []
    for receipt in db.execute('SELECT result_json FROM city_receipts WHERE session_id=? AND trip_id=? AND (scope=? OR scope=?) ORDER BY created_at DESC LIMIT 8',
                              (session.id, str(trip_id), scope, 'agent_chat:'+str(trip_id))):
        values = json.loads(receipt['result_json']).get('references', [])
        if values:
            references = values[:6]
            break
    return latest, summary, references


def fact_view(item, items, moment):
    """Names are untrusted data; no arbitrary introductions, coordinates or private evidence notes."""
    check = facts.status(item, items, moment)
    result = {'kind':item.kind,'id':item.fact_id,'version':item.version, **check,
              'business_status':item.business_status,'is_demo':item.is_demo,
              'checked_at':item.verified_at.isoformat() if item.verified_at else None,
              'effective_to':item.effective_to.isoformat() if item.effective_to else None,
              'source_ids':list(item.source_ids)}
    for key in ('name','place_id','program_id','stage_place_id','food_id','vendor_id','business_date',
                'starts_at','ends_at','admission_deadline','opens_at','closes_at'):
        value = getattr(item, key, None)
        if value is not None: result[key] = value.isoformat() if hasattr(value,'isoformat') else redact(value) if isinstance(value,str) else value
    return result


def load(session, trip_id=None, purpose='trip', text='', page_focus=None):
    started = time.perf_counter()
    with connect() as db:
        db.execute('BEGIN')
        owner = recheck(db, session)
        if trip_id is None:
            trip_id = owner['active_city_trip_id']
            if not trip_id: return None
        row = dict(state.owned(db, session, trip_id))
        draft = read_draft(row['draft_json'])
        planning = engine.context(db)
        moment = now()
        route = engine.read_snapshot(db, session, trip_id)
        saved = engine.route_row(db, trip_id)
        req = read_requirements(saved['requirements_json']) if saved else None
        multi_draft=draft if isinstance(draft,CityDraftV2) else None
        multi_req=req if isinstance(req,MultiDayRequirements) else None
        multi_plan=read_itinerary(route['itinerary']) if route['itinerary'] and isinstance(req,MultiDayRequirements) else None
        day_id=None;today=moment.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).date()
        if multi_draft:
            day=next((d for d in draft.days if d.business_date==today),None)
            day=day or (draft.days[0] if today<draft.start_date else draft.days[-1])
            day_id=day.day_id;draft=day
            plan_day=next((d for d in multi_plan.days if d.day_id==day_id),None) if multi_plan else None
            req=plan_day.requirements if plan_day else None
            route=route|{'itinerary':plan_day.itinerary.model_dump(mode='json') if plan_day else None}
        pending = []
        from .. import basket, exploration_state, travel_services
        workspace=exploration_state.read(db,trip_id)
        services=travel_services.read(db,trip_id)
        basket_version=basket.version(db,trip_id)
        for r in db.execute('SELECT * FROM city_engine_candidates WHERE trip_id=? AND consumed=0 ORDER BY expires_at DESC LIMIT 8', (str(trip_id),)):
            parsed=read_result(r['result_json'])
            result = parsed.model_dump(mode='json')
            first = (result.get('itineraries') or [None])[0]
            extra=json.loads(r['progress_json']);candidate_day=extra.get('day_id') or day_id
            first_day=next((d for d in first['days'] if d['day_id']==candidate_day),first['days'][0]) if first and 'days' in first else None
            projected=first_day['itinerary'] if first_day else first
            legs=(projected or {}).get('legs',[])
            expired = datetime.fromisoformat(r['expires_at']) <= moment or bool(legs and datetime.fromisoformat(legs[0]['departs_at']) < moment)
            status = 'expired' if expired else 'stale' if r['base_version'] != row['version'] or r['fingerprint'] != planning.fingerprint() else 'valid'
            if extra.get('basket_version',basket_version)!=basket_version or extra.get('planning_version',workspace['version'])!=workspace['version']:status='stale'
            from ..engine.validation import compare_itineraries
            from ..engine.models import Itinerary
            baseline=next((d.itinerary for d in multi_plan.days if d.day_id==candidate_day),None) if multi_plan else None
            baseline=baseline or (Itinerary.model_validate(route['itinerary']) if route['itinerary'] else None)
            diff = compare_itineraries(baseline,
                                       Itinerary.model_validate(projected)) if projected else None
            if diff:
                from .tools import changes_for_remaining
                selected_draft=next((d for d in multi_draft.days if d.day_id==candidate_day),draft) if multi_draft else draft
                diff = changes_for_remaining(diff,selected_draft)
            if extra.get('scope')=='current_and_future' and multi_plan and first:
                from ..engine.multi_day_service import compare
                diff=compare(multi_plan,read_itinerary(first))
            pending.append({'candidate_id':r['id'],'target_kind':'route','base_version':r['base_version'],
                            'expires_at':r['expires_at'],'status':status,'changes':diff,**({'day_id':candidate_day,'scope':extra.get('scope') or 'current_day'} if first_day else {})})
        revision = state.catalog_revision(planning.facts)
        for r in db.execute('SELECT * FROM city_proposals WHERE trip_id=? AND consumed=0 ORDER BY expires_at DESC LIMIT 8', (str(trip_id),)):
            action = json.loads(r['action_json'])
            status = 'expired' if datetime.fromisoformat(r['expires_at']) <= moment else 'stale' if r['trip_version'] != row['version'] or r['fact_revision'] != revision else 'valid'
            pending.append({'candidate_id':r['id'],'target_kind':'intent' if action.get('operation') in ('assistant_intent','assistant_v3_intent') else 'state',
                            'base_version':r['trip_version'],'expires_at':r['expires_at'],'status':status,'action':action})
        messages, summary, refs = recent(db, session, trip_id, bool(owner['history_enabled']))
        from .. import basket
        intentions=basket.snapshot(db,session,trip_id)
        focus=basket.resolve(db,page_focus,planning.facts) if page_focus else None
    items = planning.facts
    stops = []
    scheduled = {s['goal_id']:s for s in (route.get('itinerary') or {}).get('activities', [])}
    for s in draft.stops:
        data = {'stop_id':s.stop_id,'place_id':s.place_id,'name':label(items,s.place_id),'kind':s.kind,
                'session_id':s.session_id,'locked':s.locked,'progress':s.progress,
                'progress_source':s.progress_source or ('legacy_report_without_source_tag' if s.progress != 'pending' else None)}
        if s.progress == 'completed':
            data.update(actual_start=s.actual_start.isoformat(),actual_end=s.actual_end.isoformat())
        if s.stop_id in scheduled:
            plan = scheduled[s.stop_id]
            data['planned'] = {k:plan[k] for k in ('arrives_at','starts_at','departs_at')}
        stops.append(data)
    official_stops = [s for s in stops if s['progress'] != 'pending']
    ongoing_record = (route.get('itinerary') or {}).get('ongoing')
    if ongoing_record:
        ongoing_stop = next((s for s in stops if s['stop_id'] == ongoing_record['stop_id'] and s['progress'] == 'pending'),None)
        if ongoing_stop: official_stops.append(ongoing_stop)
    for activity in (route.get('itinerary') or {}).get('activities',[]):
        reported = next((s for s in stops if s['stop_id'] == activity['goal_id'] and s['place_id'] == activity['place_id'] and s['session_id'] == activity['session_id']),None)
        value = dict(reported) if reported else {'stop_id':activity['goal_id'],'progress':'pending','progress_source':None}
        value.update(place_id=activity['place_id'],name=label(items,activity['place_id']),kind=activity['kind'],
                     session_id=activity['session_id'],locked=activity['required'],
                     planned={k:activity[k] for k in ('arrives_at','starts_at','departs_at')})
        if value['stop_id'] not in {s['stop_id'] for s in official_stops}: official_stops.append(value)
    remaining = [s for s in (official_stops if route['itinerary'] else stops) if s['progress'] == 'pending']
    if multi_draft:
        for stop in stops+official_stops:stop.update(day_id=day_id,business_date=draft.business_date.isoformat())
        if draft.business_date!=today or draft.status=='closed':remaining=[]
    anchor = json.loads(row['anchor_json']) if row['anchor_json'] else None
    age = (moment - datetime.fromisoformat(anchor['confirmed_at'])).total_seconds() if anchor else None
    fresh = bool(anchor and 0 <= age <= 900 and planning.ready(items.get(('place',anchor['place_id'])),moment))
    position = {'status':'fresh' if fresh else 'expired_or_unverified' if anchor else 'missing',
                'place_id':anchor['place_id'] if anchor else None, 'name':label(items,anchor['place_id']) if anchor else None,
                'confirmed_at':anchor['confirmed_at'] if anchor else None,'source':'manual_known_place' if anchor else None,
                'evidence_basis':'user_reported','live_tracking':False}
    ongoing = None
    if fresh and remaining and row['status'] in ('ACTIVE','PAUSED'):
        first = remaining[0]; timing = first.get('planned')
        reserved = (route.get('itinerary') or {}).get('ongoing')
        if first['place_id'] == anchor['place_id'] and ((timing and datetime.fromisoformat(timing['starts_at']) <= moment < datetime.fromisoformat(timing['departs_at']))
            or reserved and reserved['stop_id'] == first['stop_id']):
            ongoing = {'stop_id':first['stop_id'],'basis':'prior_plan_estimate','independently_verified':False}
    stale_reasons = []
    if route.get('stale'): stale_reasons.append('STATE_OR_FACTS_CHANGED')
    if route['itinerary'] and datetime.fromisoformat(route['itinerary']['returns_at']) < moment: stale_reasons.append('PLAN_TIME_PASSED')
    if draft.business_date and draft.business_date < moment.astimezone(__import__('zoneinfo').ZoneInfo('Asia/Shanghai')).date():
        stale_reasons.append('DIFFERENT_BUSINESS_DATE')
    official = {'status':'not_created' if not route['itinerary'] else 'stale' if stale_reasons else 'current',
                'route_version':row['route_version'],'stale_reasons':stale_reasons,'navigation_certified':False,
                'evidence_mode':(route.get('itinerary') or {}).get('evidence_mode'),
                'activities':official_stops if route['itinerary'] else [],'issues':route.get('issues',[])}
    preferences = {'business_date':draft.business_date.isoformat() if draft.business_date else None,
        'starts_at':draft.starts_at.isoformat() if draft.starts_at else None,'ends_at':draft.ends_at.isoformat() if draft.ends_at else None,
        'origin_place_id':draft.origin_place_id,'destination_place_id':draft.destination_place_id,
        'interests':list(draft.interests),'dish_interest_ids':list(draft.dish_interest_ids),'modes':list(draft.modes),
        'pace':draft.pace,'objective':draft.objective,'budget_fen':req.budget_fen if req else None,
        'locked_stop_ids':[s.stop_id for s in draft.stops if s.locked and s.progress == 'pending'],
        'targets':[{'stop_id':s.stop_id,'kind':s.kind,'place_id':s.place_id,'session_id':s.session_id} for s in draft.stops]}
    if multi_draft:
        preferences.update(schema_version='city-engine/2',start_date=multi_draft.start_date.isoformat(),
            end_date=multi_draft.end_date.isoformat(),day_id=day_id,scope='current_day',
            days=[{'day_id':d.day_id,'business_date':d.business_date.isoformat(),'status':d.status,
                   'frozen':d.business_date<today or d.status=='closed','locked':d.business_date>today,
                   'historical_pending':[s.stop_id for s in d.stops if s.progress=='pending'] if d.business_date<today else []}
                  for d in multi_draft.days])
        official.update(day_id=day_id,scope='current_day',start_date=multi_draft.start_date.isoformat(),end_date=multi_draft.end_date.isoformat())
    relevant = set()
    if purpose in ('shows','reference','modify','history'):
        relevant |= {('show_session',s.session_id) for s in draft.stops if s.session_id and s.progress == 'pending'}
    if purpose in ('dining','modify'):
        relevant |= {('place',s.place_id) for s in draft.stops if s.kind == 'dining' and s.progress == 'pending'}
    for r in refs: relevant.add((r['kind'],r['id']))
    # Explicit names may select facts; unrelated city data is never sent to the model.
    for item in items.values():
        if text and facts.name_matches(item, text):
            relevant.add((item.kind,item.fact_id))
    selected = []
    for key in sorted(relevant):
        item = items.get(key)
        if item and facts.planning_visible(item):
            selected.append(fact_view(item,items,moment))
            if isinstance(item,facts.Place) and item.place_type == 'restaurant':
                selected.extend(fact_view(r,items,moment) for r in items.values() if isinstance(r,facts.OpenRule) and r.place_id == item.fact_id and r.business_date == draft.business_date and facts.planning_visible(r))
    public_pending = []
    for p in pending:
        value = {k:v for k,v in p.items() if k != 'action'}
        action = p.get('action',{})
        if p['target_kind'] == 'intent': value['intent'] = action.get('intent') or action.get('understanding')
        elif action: value['operation'] = action['operation']
        public_pending.append(value)
    progress = {'completed':[s for s in stops if s['progress'] == 'completed'],
                'skipped':[s for s in stops if s['progress'] == 'skipped'], 'ongoing':ongoing,
                'remaining':remaining,'next':remaining[0] if remaining and route['itinerary'] else None,
                'ended':row['status'] in state.TERMINAL,**({'day_id':day_id,'business_date':draft.business_date.isoformat(),'scope':'current_day'} if multi_draft else {})}
    if purpose == 'next':
        # Keep identity/locks/progress, omit unrelated vendor/show catalogs and old dialogue.
        selected = []; messages = []; summary = {'text':'','source_message_ids':[],'authoritative':False}
    envelope = CityContext(purpose=purpose,identity=Identity(trip_id=trip_id,status=row['status'],version=row['version'],
        route_version=row['route_version'],business_date=preferences['business_date'],generated_at=moment),requirements=preferences,
        official_plan=official,progress=progress,position=position,pending_changes=public_pending,recent_messages=messages,
        summary=summary,facts=selected[:30],references=refs,page_focus=focus,
        planning_constraints={k:workspace[k] for k in ('version','constraints','rounds')},
        basket={'version':intentions['version'],'items':[{k:i[k] for k in ('key','kind','id','fact_id','priority','status')} for i in intentions['items']]},
        limits={'recent_messages':8,'summary_characters':600,'max_characters':12000,'raw_coordinates':False,'full_road_geometry':False})
    trim(envelope)
    envelope.metrics.update(assembly_ms=round((time.perf_counter()-started)*1000,3),
                            characters=0,fact_records=len(envelope.facts))
    for _ in range(3): envelope.metrics['characters'] = len(envelope.model_dump_json())
    if len(envelope.model_dump_json()) > 12000: raise HTTPException(422,detail='当前状态超出上下文预算，请缩小目标范围。')
    return Snapshot(row,draft,planning,route,req,pending,envelope,bool(owner['history_enabled']),workspace,intentions['items'],services,multi_req,multi_draft,multi_plan,day_id)


def trim(envelope):
    while len(envelope.model_dump_json()) > 11600:
        if envelope.recent_messages: envelope.recent_messages.pop(0)
        elif envelope.summary['text']:
            envelope.summary.clear(); envelope.summary.update(text='',source_message_ids=[],authoritative=False)
        elif envelope.facts: envelope.facts.pop()
        elif envelope.pending_changes: envelope.pending_changes.pop()
        else: raise HTTPException(422,detail='关键行程状态超出上下文预算，未静默丢弃锁定条件。')
