"""One read projection of trip-scoped requirements; never a new store or write path."""
from .. import hotels, travel_services
from ...memory.context import redact


def planning_values(snapshot):
    from ..exploration import Constraints
    fields=Constraints.model_fields
    values={k:v for k,v in snapshot.envelope.requirements.items() if k in fields}
    # A partial legacy workspace inherits absent fields. An explicit null stays
    # null, so a cleared condition never silently reappears from the draft.
    values.update({k:v for k,v in (snapshot.workspace.get('constraints') or {}).items() if k in fields})
    return values


def read(snapshot):
    from ..exploration import Constraints
    from ..schema import TZ
    env=snapshot.envelope
    values=planning_values(snapshot)
    raw=snapshot.workspace.get('constraints')
    source=('planning_workspace' if snapshot.workspace.get('version',0) else 'legacy_planning_receipt') if raw is not None else 'trip_state'
    requirements={k:v for k,v in env.requirements.items() if k in ('interests','dish_interest_ids','locked_stop_ids','targets')}
    requirements.update(values)
    places={p.fact_id:p for (kind,_),p in snapshot.planning.facts.items() if kind=='place' and p.rights_status=='permitted'}
    missing_user=[k for k in ('business_date','starts_at','ends_at') if values.get(k) is None]
    missing_data=[]
    for role in ('origin','destination'):
        key=role+'_place_id'
        if values.get(key):
            if values[key] not in places:missing_data.append(key)
        elif values.get(role+'_text'):
            missing_data.append(key)
        else:
            missing_user.append(key if places else role+'_text')
            if not places:missing_data.append(key)
    saved=snapshot.services.get('requirements') or {}
    service_fields=('category','check_in','check_out','nights','guests','rooms','max_price','stay_budget_fen','room_type','location_text','total_budget_fen','mode','objective')
    services={k:saved[k] for k in service_fields if k in saved}
    services['attributes']={k:{f:v.get(f) for f in ('value','strength')}
        for k,v in saved.get('attributes',{}).items() if k in hotels.ATTRS}
    readiness=travel_services.service_readiness({'category':'hotels',**saved},env.identity.generated_at.astimezone(TZ).date()) if saved else {'missing_user_fields':[],'questions':[]}
    selections={kind:{k:redact(str(item[k]))[:120] for k in ('id','name') if k in item}
        for kind,item in (snapshot.services.get('selection') or {}).items()
        if kind in ('hotels','parking','merchants') and isinstance(item,dict)}
    return {
        'requirements':requirements,'requirements_source':source,
        'state_versions':{'trip_id':str(env.identity.trip_id),'trip':env.identity.version,
                          'route':env.identity.route_version,'planning':snapshot.workspace.get('version',0),'services':snapshot.services.get('version',0)},
        'planning_constraints':{'version':snapshot.workspace.get('version',0),
            'constraints':{k:v for k,v in raw.items() if k in Constraints.model_fields} if raw is not None else None,
            'rounds':snapshot.workspace.get('rounds',0)},
        'travel_service_workspace':{'version':snapshot.services.get('version',0),'requirements':services,
            'status':(snapshot.services.get('result') or {}).get('status'),
            'missing_fields':readiness['questions'],'missing_user_fields':readiness['missing_user_fields'],
            'selection':selections,'scope':'service_conditions_and_candidate_intentions_not_formal_route'},
        'planning_readiness':{'scope':'formal_itinerary_only_not_reference_route','missing_user_fields':missing_user,
            'missing_data_fields':missing_data,'place_catalog_available':bool(places),'blocked_by_missing_place_data':bool(missing_data)},
    }
