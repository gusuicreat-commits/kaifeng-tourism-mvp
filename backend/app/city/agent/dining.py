"""Filter recorded restaurant attributes; semantic normalization belongs to the model."""
from collections import Counter
from .. import facts
from ..assistant import context
from ..restaurant import price_range

LABELS={'cuisine':'类别','taste':'口味','spicy':'辣度','max_fen':'人均上限','period':'适合时段','dish':'招牌菜'}
SPICY={'none':'不辣','mild':'微辣','hot':'辣'}
SPICY_UP_TO={'none':('none',),'mild':('none','mild'),'hot':('hot',)}


def conditions(intent):
    # dish=true asks for shops' signature dishes; beside a named dish or shop it adds nothing.
    named=any(a.target_name for a in intent.actions)
    groups=({k:v for k,v in a.dining.items() if k!='dish' or v and not named} for a in intent.actions if a.dining)
    return [g for g in groups if g]


def usable(ctx,filters):
    """Conditions some vendor has recorded data for, and the labels no vendor records at all.

    Filtering on an attribute nobody has collected would reject every shop as 资料不全, so that
    condition is set aside and the reply says it was not applied."""
    vendors=[v for v in ctx.facts.values() if isinstance(v,facts.Vendor)]
    recorded={'taste':lambda v:v.taste_tags,'spicy':lambda v:v.spiciness,'period':lambda v:v.meal_periods,
              'dish':lambda v:v.signature_dishes,'max_fen':lambda v:price_range(v.price_band),
              'cuisine':lambda v:v.cuisine or v.taste_tags}
    keep=[];missing=[]
    for group in filters:
        kept={k:v for k,v in group.items() if any(recorded[k](x) for x in vendors)}
        missing+=[LABELS[k] for k in group if k not in kept]
        if kept:keep.append(kept)
    return keep,list(dict.fromkeys(missing))


def unapplied(missing,unknown=()):
    note=''.join(f'“{t}”没有对应的商家资料，未按它筛选。' for t in unknown)
    return note+(f'现有商家资料未记录{"、".join(missing)}，暂未按{"、".join(missing)}筛选。' if missing else '')


def failures(vendor,filters):
    failed=[]
    for group in filters:
        for key,value in group.items():
            if key=='dish' and not value:continue
            actual={'taste':vendor.taste_tags,'spicy':vendor.spiciness,'period':vendor.meal_periods,'dish':vendor.signature_dishes,
                    'max_fen':price_range(vendor.price_band),'cuisine':tuple(l for l in (vendor.cuisine,*vendor.taste_tags) if l)}[key]
            if not actual:failed.append(LABELS[key]+'资料不全，未确认');continue
            if key=='dish':continue
            # A stated taste matches a recorded tag when either contains the other (口味清淡 / 清淡);
            # a one-character taste (辣, 甜) matches the tags that contain it (香辣, 咸鲜微甜).
            matched=(all(any(t.strip() and (t.strip() in a and (len(t.strip())>=2 or key=='taste') or len(t.strip())>=2 and a in t.strip())
                                 for a in actual) for t in value.split('、')) if key in ('taste','cuisine') else
                     # 别太辣 (mild) is a ceiling: a not-spicy shop qualifies too.
                     actual in SPICY_UP_TO[value] if key=='spicy' else value in actual if key=='period' else actual[1]<=value)
            if not matched:failed.append(LABELS[key]+'不符合')
    return list(dict.fromkeys(failed))


def interpretation(filters):
    parts=[]
    for group in filters:
        for key,value in group.items():
            if key=='dish':continue
            value=SPICY[value] if key=='spicy' else f'{value/100:g}元/人以内' if key=='max_fen' else value
            parts.append(f'{LABELS[key]}“{value}”')
    return '按'+ '、'.join(dict.fromkeys(parts))+'筛选。' if parts else ''


def details(vendor,ctx):
    dishes=[]
    for name in vendor.signature_dishes:
        matches=[f.fact_id for f in ctx.facts.values() if isinstance(f,facts.Food) and facts.planning_visible(f)
                 and (f.name==name or name in f.aliases)]
        dishes.append({'name':name,'food_id':matches[0] if len(matches)==1 else None})
    return {'vendor_id':vendor.fact_id,'place_id':vendor.place_id,'name':context.label(ctx.facts,vendor.place_id),
            'cuisine':vendor.cuisine,'taste_tags':list(vendor.taste_tags),'spiciness':vendor.spiciness,
            'price_band':vendor.price_band,'meal_periods':list(vendor.meal_periods),'signature_dishes':dishes,
            'source_ids':list(vendor.source_ids),'version':vendor.version,'validation_status':vendor.validation_status,
            'planning_status':vendor.planning_status}


def requested_foods_safe(snapshot,intent):
    from .toolbelt import requested_foods
    return requested_foods(snapshot,intent)


def label_targets(ctx,targets):
    """Names that no dish or shop needs to own but some vendor's cuisine or taste tag shares."""
    labels={l for v in ctx.facts.values() if isinstance(v,facts.Vendor) for l in (v.cuisine,*v.taste_tags) if l and len(l)>=2}
    # A label inside the name may leave at most one character (酸甜口 / 酸甜); a longer name
    # (当地特色小吃 / 小吃) is a general wish, not that category.
    return [t for t in targets if len(t)>=2 and any(t in l or l in t and len(t)-len(l)<=1 for l in labels)]


def restaurant_query(snapshot,intent):
    if usable(snapshot.planning,conditions(intent))[0]:return True
    targets={a.target_name for a in intent.actions if a.kind=='search_dining' or a.purpose=='dining'}-{None}
    if targets and not requested_foods_safe(snapshot,intent) and len(label_targets(snapshot.planning,targets))==len(targets) \
            and not any(isinstance(p,facts.Place) and p.place_type=='restaurant' and any(t==p.name or t in p.aliases for t in targets)
                        for p in snapshot.planning.facts.values()):
        return True
    return any(isinstance(p,facts.Place) and p.place_type=='restaurant' and facts.planning_visible(p)
               and any(t==p.name or t in p.aliases for t in targets) for p in snapshot.planning.facts.values())


def recommend(snapshot,intent):
    from .toolbelt import _supply_readable, requested_foods
    ctx=snapshot.planning;filters,missing=usable(snapshot.planning,conditions(intent));rejected=Counter();items=[];unknown=[]
    targets={a.target_name for a in intent.actions if a.kind=='search_dining' or a.purpose=='dining'}-{None}
    foods=requested_foods(snapshot,intent)
    places={p.fact_id for p in ctx.facts.values() if isinstance(p,facts.Place) and p.place_type=='restaurant'
            and any(t==p.name or t in p.aliases for t in targets)}
    if targets and not places and not foods:
        # The model often puts a cuisine or taste (烧烤, 酸甜口) in the name slot. A name that
        # no dish or shop has but some vendor's recorded cuisine or taste tag shares becomes
        # that filter; an unknown name otherwise never turns into a city-wide list.
        as_filter=label_targets(ctx,targets)
        if len(as_filter)==len(targets):
            filters=[*filters,*({'cuisine':t} for t in as_filter)];targets=set()
    # Beside conditions that can be applied, a name with no record (老字号, 撸串) is set aside
    # and said so; alone it cannot silently become a city-wide recommendation.
    if targets and not places and not foods and filters:
        unknown=sorted(targets);targets=set()
    if targets and not places and not foods:
        return {'status':'needs_data','code':'DINING_TARGET_UNKNOWN','items':[],
                'reply':interpretation(filters)+unapplied(missing)+'没有该菜品或商家的资料。','formal_state_changed':False}
    for vendor in ctx.facts.values():
        if not isinstance(vendor,facts.Vendor) or places and vendor.place_id not in places:continue
        if not _supply_readable(ctx,vendor):rejected['来源、有效期或商家状态未满足参考查询条件']+=1;continue
        if foods and any(not any(isinstance(s,facts.Supply) and s.food_id==f.fact_id and s.vendor_id==vendor.fact_id
                                and _supply_readable(ctx,s) for s in ctx.facts.values()) for f in foods):
            rejected['菜品供应关系未确认']+=1;continue
        problems=failures(vendor,filters)
        if problems:rejected.update(problems);continue
        items.append(details(vendor,ctx))
    capped=any('max_fen' in f for f in filters)
    items.sort(key=lambda i:((price_range(i['price_band']) or (0,100000001))[1] if capped else 0,
                             -len(i['signature_dishes']),i['name'],i['vendor_id']))
    items=items[:6];reply=interpretation(filters)+unapplied(missing,unknown)
    if not items:reply+='没有商家满足全部条件。'+('；'.join(f'{reason}（{count}家）' for reason,count in sorted(rejected.items()))+'。' if rejected else '现有商家资料不足。')
    else:
        reply+='以下为商家参考资料，尚未现场核实：'
        for item in items:
            reply+=item['name']+(f"（人均{item['price_band']}）" if item['price_band'] else '')
            if item['signature_dishes']:reply+='，招牌菜：'+'、'.join(d['name'] for d in item['signature_dishes'])
            reply+='。'
        if any(i['signature_dishes'] for i in items):reply+='招牌菜为商家资料，以店内为准。'
        if rejected:reply+='未满足条件或资料不全、未确认的商家未列出。'
    return {'status':'answered' if items else 'needs_data','code':None if items else 'NO_MATCHING_RESTAURANT',
            'items':items,'reply':reply,'excluded':dict(rejected),'formal_state_changed':False}
