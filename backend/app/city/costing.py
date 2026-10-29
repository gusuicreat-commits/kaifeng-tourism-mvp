"""One unit-aware calculation used by the solver, summary and confirmation verifier."""
from datetime import datetime
from .budget_models import CostItem, BudgetSummary
from .schema import TZ


def count_amount(amount,unit,pricing):
    if amount==0:return 0,1
    count=pricing.party_size if unit=='per_person' else 1 if unit=='per_party' else None
    return (amount*count,count) if count is not None else (None,None)


def place_amount(cost,pricing,kind,goal_id=None):
    if pricing is None:return cost.upper_fen if cost else None
    if pricing.spending and goal_id and 'activity:'+goal_id in pricing.spending.covered_cost_ids:return 0
    if kind=='dining' and pricing.dining_per_person_day_fen is not None:return 0
    if not cost:return None
    return count_amount(cost.upper_fen,cost.unit,pricing)[0]


def travel_amount(j,pricing=None):
    if pricing is None:
        if j.cost_upper_fen is None or any(b.cost_upper_fen is None for b in j.boundaries):return None
        return j.cost_upper_fen+sum(b.cost_upper_fen for b in j.boundaries)
    if any(b.cost_upper_fen is None for b in j.boundaries):return None
    fees=[count_amount(b.cost_upper_fen,b.cost_unit,pricing)[0] for b in j.boundaries]
    if any(f is None for f in fees):return None
    boundary=sum(fees)
    if j.travel.mode=='driving':
        if pricing.driving_basis!='taxi' or j.vehicle_cost_fen is None:return None
        # AMap's taxi estimate is per vehicle. Toll inclusion is not established;
        # do not add tolls a second time. The summary names this uncertainty.
        return j.vehicle_cost_fen*pricing.vehicle_count+boundary
    if j.cost_upper_fen is None:return None
    amount,_=count_amount(j.cost_upper_fen,j.cost_unit,pricing)
    return amount+boundary if amount is not None else None


def limit(pricing,day_count):
    if not pricing:return None
    limits=[pricing.total_budget_fen] if pricing.total_budget_fen is not None else []
    if pricing.per_person_budget_fen is not None and pricing.party_size:
        limits.append(pricing.per_person_budget_fen*pricing.party_size*(day_count if pricing.per_person_daily else 1))
    return min(limits) if limits else None


def stay_item(selected,overnights,moment,pricing=None):
    if not overnights:return ()
    price=(selected or {}).get('price_selection')
    base=dict(cost_id='lodging:'+((selected or {}).get('id') or 'unselected'),category='lodging',
        label='住宿',day=overnights[0].night_date,unit='stay',quantity=1)
    if not price:return (CostItem(**base,amount_fen=None,basis='unknown',source='所选住宿尚无适用报价'),)
    if price.get('expires_at') and datetime.fromisoformat(price['expires_at'])<=moment:
        return (CostItem(**base,amount_fen=None,basis='unknown',source='酒店报价已过期，请重新选择报价'),)
    start=price.get('check_in');end=price.get('check_out')
    nights={o.night_date.isoformat() for o in overnights}
    if not start or not end or min(nights)<start or max(nights)>=end:
        return (CostItem(**base,amount_fen=None,basis='unknown',source='报价日期未覆盖住宿日期'),)
    amount=price.get('amount_fen');unit=price.get('unit');rooms=price.get('rooms',1)
    if type(amount) is not int or amount<0:
        return (CostItem(**base,amount_fen=None,basis='unknown',source='酒店报价金额或计价单位待核对'),)
    from datetime import date
    span=(date.fromisoformat(end)-date.fromisoformat(start)).days
    if unit=='per_room_night':quantity=rooms*span
    elif unit=='per_room_stay':quantity=rooms
    elif unit=='party_stay':quantity=1
    else:return (CostItem(**base,amount_fen=None,basis='unknown',source='酒店报价计价单位待核对'),)
    base.update(unit=unit,quantity=quantity)
    notes=('按所选完整入住区间计费；额外住宿日期也计入该笔住宿支出。',) if len(nights)!=span else ()
    result=(CostItem(**base,amount_fen=amount*quantity,basis=price['basis'],source=price['source'],notes=notes),)
    if price['basis']=='quoted' and pricing:
        try:capacity=int(price.get('max_occupancy'))*rooms
        except (ValueError,TypeError):capacity=None
        issue='所选房型可住人数待核对' if capacity is None or not pricing.party_size else '所选房间无法容纳全部同行人，请重新选择报价' if pricing.party_size>capacity else None
        if issue:result+=(CostItem(cost_id=base['cost_id']+':occupancy',category='lodging',label=issue,
            day=base['day'],amount_fen=None,unit='unknown',basis='unknown',source='房型人数条件'),)
    return result


def summarize(req,days,ctx):
    pricing=req.pricing
    if pricing is None:return None
    items=[]
    for d in days:
        plan=d.itinerary;day=d.business_date
        for a in plan.activities:
            if a.kind=='dining' and pricing.dining_per_person_day_fen is not None:continue
            costs=[c for c in ctx.operations.costs if c.place_id==a.place_id and ctx.evidence_ready(c.evidence,d.requirements.starts_at,d.requirements.ends_at)]
            cost=costs[0] if len(costs)==1 else None
            amount=place_amount(cost,pricing,a.kind)
            _,quantity=count_amount(cost.upper_fen,cost.unit,pricing) if cost else (None,None)
            items.append(CostItem(cost_id='activity:'+a.goal_id,category='dining' if a.kind=='dining' else 'tickets' if a.kind in ('visit','show') else 'other',
                label=getattr(ctx.facts.get(('place',a.place_id)),'name',a.place_id),day=day,
                amount_fen=amount,quantity=quantity,unit=cost.unit if cost and cost.unit else 'unknown',
                basis='unknown' if amount is None else 'estimate' if cost and cost.evidence.kind=='estimate' else 'quoted',
                source=cost.evidence.source if cost and cost.evidence.source else '地点费用或计价单位待补'))
        for i,l in enumerate(plan.legs):
            j=l.journey;amount=travel_amount(j,pricing)
            items.append(CostItem(cost_id=f'transport:{d.day_id}:{l.departs_at.isoformat()}:{i}',category='transport',label=' → '.join(getattr(ctx.facts.get(('place',pid)),'name','地点资料待补') for pid in (j.travel.origin_place_id,j.travel.destination_place_id)),
                day=day,amount_fen=amount,unit='per_vehicle' if j.travel.mode=='driving' else j.cost_unit or 'unknown',
                quantity=pricing.vehicle_count if j.travel.mode=='driving' else pricing.party_size if j.cost_unit=='per_person' else 1,
                basis='estimate' if amount is not None and j.travel.mode=='driving' else 'quoted' if amount is not None else 'unknown',
                source='高德打车估计（按车计）' if j.vehicle_cost_source=='amap_taxi_estimate' and pricing.driving_basis=='taxi' else '交通费用依据'))
            if j.travel.mode=='driving' and j.tolls_fen:
                items.append(CostItem(cost_id=f'tolls:{d.day_id}:{l.departs_at.isoformat()}:{i}',category='transport',label='过路费是否包含在打车估价中待核对',
                    day=day,amount_fen=None,unit='per_vehicle',quantity=pricing.vehicle_count,basis='unknown',source='高德过路费参考；避免重复计费'))
        if pricing.dining_per_person_day_fen is not None:
            amount,quantity=count_amount(pricing.dining_per_person_day_fen,'per_person',pricing)
            items.append(CostItem(cost_id='dining:'+d.day_id,category='dining',label='当日餐费预留',day=day,
                amount_fen=amount,unit='per_person_day',quantity=quantity,basis='reported' if amount is not None else 'unknown',source='用户设置的餐费预留'))
        elif not any(a.kind=='dining' for a in plan.activities):
            items.append(CostItem(cost_id='dining:'+d.day_id,category='dining',label='当日餐饮费用未计入',day=day,
                amount_fen=None,unit='unknown',basis='unknown',source='未设置餐费预留'))
    items.extend(ctx.stay_costs if req.overnights else ())
    if req.overnights and not ctx.stay_costs:
        items.extend(stay_item(None,req.overnights,ctx.assessed_at))
    # Consumed activities/legs retain the confirmed snapshot. A spending report
    # replaces only explicitly named items, including prepaid future charges.
    historical={i.cost_id:i for i in ctx.history_costs}
    items=list({i.cost_id:i for i in items}.values())
    items=[i for i in items if i.cost_id not in historical]+list(historical.values())
    if pricing.spending:
        report=pricing.spending;covered=set(report.covered_cost_ids)
        missing=covered-{i.cost_id for i in items}
        labels={i.cost_id:i.label for i in items}
        items=[i for i in items if i.cost_id not in covered]
        items.append(CostItem(cost_id='spending',category='spent',label='已支付费用（替代已勾选项）',day=report.as_of.astimezone(TZ).date(),
            amount_fen=report.amount_fen,unit='party_report',quantity=1,basis='reported',source='用户报告',
            covered_cost_ids=tuple(sorted(covered)),notes=tuple(labels.get(k,'已支付条目') for k in sorted(covered))))
        if missing:items.append(CostItem(cost_id='spending:missing',category='other',label='已支付费用对应项缺失',
            amount_fen=None,unit='unknown',basis='unknown',source='请重新核对已支付费用覆盖范围'))
    unknown=[i.label+'：'+i.source for i in items if i.amount_fen is None]
    if pricing.per_person_budget_fen is not None and not pricing.party_size:unknown.append('按人预算需要明确旅行人数')
    total=sum(i.amount_fen or 0 for i in items);maximum=limit(pricing,req.day_count)
    status='over' if maximum is not None and total>maximum else 'unknown' if unknown else 'not_set' if maximum is None else 'within_budget'
    categories={};dates={}
    for i in items:
        categories[i.category]=categories.get(i.category,0)+(i.amount_fen or 0)
        if i.day:dates[i.day.isoformat()]=dates.get(i.day.isoformat(),0)+(i.amount_fen or 0)
    return BudgetSummary(items=tuple(items),known_total_fen=total,per_person_fen=(total+pricing.party_size-1)//pricing.party_size if pricing.party_size else None,
        limit_fen=maximum,remaining_fen=maximum-total if maximum is not None and not unknown else None,status=status,
        estimated=any(i.basis in ('estimate','reported') for i in items),unknown_items=tuple(dict.fromkeys(unknown)),
        by_category=categories,by_day=dates,hard_limit=pricing.hard_limit)
