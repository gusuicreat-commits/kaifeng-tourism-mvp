"""Fixed workflow: sourced facts → crowd provider → NetworkX matrix → OR-Tools → validation.

The stable /plans contract is assembled here. Language models cannot change it.
"""
import math
from datetime import datetime, timedelta
from fastapi import HTTPException
from .crowds import TZ
from .paths import build_graph, geometry, path_matrix, segment  # build_graph retained for callers
from .schedules import facts_for
from .itinerary import Visit, SolverUnavailable, conservative_fallback, solve

INTERESTS = {'show':'演出', 'photo':'拍照', 'food':'美食', 'culture':'文化'}


def _visits(req, g, start, facts, budget):
    openings = [f for f in facts if f.kind == 'opening']
    site_windows = [f for f in openings if f.node_id is None]
    if site_windows:
        current = [f for f in site_windows if f.starts_at <= start < f.ends_at]
        if not current:
            raise HTTPException(422, detail='入园时间不在已核验的景区开放时间内')
        budget = min(budget, max(int((f.ends_at-start).total_seconds()) for f in current))
    visits = [Visit('gate', 0, 0, 0)]
    required = set(req.must_visit)
    sessions = {f.id:f for f in facts if f.kind == 'show'}
    for sid in req.must_attend:
        if sid not in sessions:
            raise HTTPException(422, detail='必看演出不存在、未核实或不属于目标日期；请以景区当日公告为准')
        required.add(sessions[sid].node_id)
    allowed = {n for n in g if g.nodes[n]['kind'] == 'poi'}
    if set(req.excluded_ids)-allowed:
        raise HTTPException(422, detail='排除点包含未知或非游览点位')
    if required & set(req.excluded_ids):
        raise HTTPException(422, detail='必看场次或必去点与排除点冲突')
    allowed -= set(req.excluded_ids)
    if required-allowed:
        raise HTTPException(422, detail='必去点包含未知或非游览点位')
    for node_id in sorted(allowed):
        node = g.nodes[node_id]
        shows = [f for f in sessions.values() if f.node_id == node_id]
        windows = [f for f in openings if f.node_id == node_id]
        reward = (400 if req.mode == 'shortest' else 1500) + (4000 if req.mode == 'shortest' else 6000) * len(set(node['interests']) & set(req.interests))
        for show in shows or [None]:
            service = math.ceil((show.ends_at-show.starts_at).total_seconds()) if show else math.ceil(node['stay_minutes'] * 60)
            for window in windows or [None]:
                earliest = max(0, math.ceil((window.starts_at-start).total_seconds()) if window else 0)
                latest = min(budget-service, math.floor((window.ends_at-start).total_seconds())-service if window else budget-service)
                if show:
                    t = int((show.starts_at-start).total_seconds())
                    earliest, latest = max(earliest, t), min(latest, t)
                if earliest <= latest:
                    visits.append(Visit(node_id, service, earliest, latest, reward,
                                        show.id if show else None, show.source_id if show else window.source_id if window else None,
                                        show.name if show else None))
    visits.append(Visit('exit', 0, 0, budget))
    return visits, required, budget


def _validate(solution, visits, matrix, required, sessions, budget, g):
    if (len(solution.indices)<2 or len(solution.indices)!=len(solution.starts)
        or any(type(i) is not int or not 0<=i<len(visits) for i in solution.indices)
        or solution.indices[0]!=0 or solution.indices[-1]!=len(visits)-1
        or solution.starts[0]!=0):
        raise HTTPException(503, detail='路线校验失败：起终点或时间结构无效')
    chosen = [visits[i] for i in solution.indices]
    if not required.issubset({v.node_id for v in chosen}) or not sessions.issubset({v.session_id for v in chosen}):
        raise HTTPException(503, detail='路线校验失败：缺少必去点或必看场次')
    if len({v.node_id for v in chosen}) != len(chosen):
        raise HTTPException(503, detail='路线校验失败：重复游览点')
    for k, (v, t) in enumerate(zip(chosen, solution.starts)):
        if not v.earliest <= t <= v.latest or t + v.service > budget:
            raise HTTPException(503, detail='路线校验失败：时间窗或总时长不满足')
        if k:
            previous = chosen[k-1]
            leg = matrix[previous.node_id, v.node_id]
            if leg is None or solution.starts[k-1] + previous.service + leg['walk_seconds'] > t:
                raise HTTPException(503, detail='路线校验失败：道路或行走时间不满足')
            if any(not g.has_edge(a,b) for a,b in zip(leg['node_ids'],leg['node_ids'][1:])):
                raise HTTPException(503, detail='路线校验失败：路段不可通行')
    return chosen


def plan(req):
    start = req.visit_start or datetime.now(TZ).replace(hour=req.hour, minute=0, second=0, microsecond=0)
    quiet = req.mode == 'quiet' or (req.avoid_crowds and req.mode != 'shortest')
    requested_quiet = quiet
    g, network, crowd, levels = build_graph(req.scenic_id, req.dataset, start.hour, req.crowd_overrides, quiet, start)
    facts = facts_for(req.scenic_id, start.date(), req.dataset)
    visits, required, budget = _visits(req, g, start, facts, req.available_minutes * 60)
    speed = {'relaxed':50, 'normal':70, 'brisk':85}[req.walking]
    matrix = path_matrix(g, [v.node_id for v in visits], speed)
    warnings = [network.get('notice', '请以现场指引为准。'),
                '拥挤参考采用入园时段快照，不代表整段游程的实时客流；未包含排队、临时封路与天气影响。']
    try:
        solution = solve(visits, matrix, required, set(req.must_attend), budget, req.mode)
    except (SolverUnavailable, RuntimeError) as exc:
        import logging
        logging.getLogger(__name__).warning('Itinerary solver fallback: %s', type(exc).__name__)
        solution = conservative_fallback(visits, matrix, sorted(required), set(req.must_attend), budget)
        warnings.append('优化服务暂不可用，已保留必去需求并省略可选点；仅返回通过时间与道路校验的保守路线。')
    chosen = _validate(solution, visits, matrix, required, set(req.must_attend), budget, g)
    quiet = g.graph['crowd_optimization']
    node_path = [chosen[0].node_id]
    legs, stops = [], []
    wait = 0
    for k, (visit, t) in enumerate(zip(chosen, solution.starts)):
        node = g.nodes[visit.node_id]
        leg = matrix[chosen[k-1].node_id, visit.node_id] if k else None
        arrival = solution.starts[k-1] + chosen[k-1].service + leg['walk_seconds'] if k else 0
        waiting = t-arrival
        wait += waiting
        if leg:
            legs.append(leg)
            node_path += leg['node_ids'][1:]
        reasons = []
        if visit.node_id in required:
            reasons.append('你指定的必去点')
        matched = [INTERESTS[i] for i in node['interests'] if i in req.interests]
        if matched:
            reasons.append('符合' + '、'.join(matched) + '兴趣')
        if visit.session_id:
            reasons.append(f'按已核验场次安排：{visit.name}，{(start+timedelta(seconds=t)).strftime("%H:%M")} 开始')
        elif 'show' in node['interests']:
            reasons.append('仅安排场地游览，未确认可观看演出；请以景区当日公告为准')
        if quiet and levels.get(visit.node_id) is not None:
            reasons.append('已纳入客流参考；演示数据仅作算法验证' if req.dataset == 'demo' else '已纳入观测或历史估计的拥挤参考')
        stops.append(dict(id=visit.node_id, name=node['name'], arrival_minutes=round(arrival/60, 1),
                          scheduled_minutes=round(t/60, 1), arrival_time=(start+timedelta(seconds=arrival)).isoformat(),
                          starts_at=(start+timedelta(seconds=t)).isoformat(), departure_time=(start+timedelta(seconds=t+visit.service)).isoformat(),
                          stay_minutes=visit.service/60, wait_minutes=round(waiting/60, 1),
                          crowd_level=levels.get(visit.node_id), reasons=reasons, session_id=visit.session_id,
                          is_simulated=node.get('is_simulated', network.get('is_simulated', True))))
    distance = sum(l['distance_m'] for l in legs)
    walking_seconds = sum(l['walk_seconds'] for l in legs)
    stay_seconds = sum(v.service for v in chosen)
    total_seconds = solution.starts[-1]
    if total_seconds != walking_seconds + stay_seconds + wait:
        raise HTTPException(503, detail='路线校验失败：耗时不一致')
    if not quiet and requested_quiet:
        warnings.append('客流数据不足：本次未使用拥挤优化，未知不代表畅通。')
    if crowd.get('unavailable'):
        warnings.append(crowd['notice'])
    unknown = sorted({n for n in node_path if g.nodes[n]['kind'] == 'poi' and levels.get(n) is None})
    if quiet and unknown:
        warnings.append('部分路段客流未知，按0.5保守代价处理；这是策略参数，不是拥挤观测。')
    if req.dataset == 'demo':
        warnings.append('客流为显式模拟演示，不代表现场监测或已验证预测。')
    if not any(f.kind == 'show' for f in facts):
        warnings.append('目标日期演出时间表未核实，未承诺演出可观看；请以景区当日公告为准。')
    if not any(f.kind == 'opening' and f.node_id is None for f in facts):
        warnings.append('景区开放时间未核实，请出发前核对当日公告。')
    else:
        warnings.append('仅在已核验开放区间内安排；未覆盖的临时运营变化请以现场为准。')
    evidence = [r for r in crowd['nodes'] if r['node_id'] in set(node_path)]
    source_ids = sorted({s for r in evidence for s in r.get('source_ids', [])} |
                        {f.source_id for f in facts if f.kind == 'opening' or f.id in {v.session_id for v in chosen}} |
                        {g[a][b]['source_id'] for a,b in zip(node_path,node_path[1:]) if g[a][b].get('source_id')})
    result = dict(scenic_id=req.scenic_id, request=req.model_dump(mode='json') | {'visit_start':start.isoformat(), 'hour':start.hour},
                  node_path=node_path, legs=legs, route_edges=[e for l in legs for e in l['edge_ids']], stops=stops,
                  distance_m=distance, walk_minutes=round(walking_seconds/60, 1), stay_minutes=stay_seconds/60,
                  wait_minutes=round(wait/60, 1), total_minutes=round(total_seconds/60, 1),
                  weighted_cost=sum(l['weighted_cost'] for l in legs),
                  mean_risk=round(sum(l['risk_distance'] for l in legs)/distance,3) if distance and quiet else None,
                  remaining_minutes=round(req.available_minutes-total_seconds/60,1),
                  route_geojson=geometry(g, node_path), is_simulated=network.get('is_simulated', True),
                  crowd_dataset=req.dataset, crowd_optimization_applied=quiet,
                  crowd_basis='simulation' if req.dataset == 'demo' else 'start_time_snapshot' if quiet else 'unavailable',
                  crowd_evidence=evidence, unknown_crowd_nodes=unknown, source_ids=source_ids,
                  schedule_facts=[f.model_dump(mode='json') for f in facts], warnings=warnings,
                  algorithm='NetworkX Dijkstra + OR-Tools CP-SAT selective time-window path',
                  solver={'status':solution.status, 'objective':solution.objective, 'bound':solution.bound,
                          'optimal_for_candidate_matrix':solution.status == 'OPTIMAL'})
    # The core owns all facts; explanation is assembled from validated results only.
    result['explanation'] = f'本次安排 {len(stops)-2} 个游览点，步行约 {result["walk_minutes"]} 分钟，停留 {result["stay_minutes"]:g} 分钟，候场 {result["wait_minutes"]} 分钟。'
    return result


def point_route(req):
    g, net, crowd, levels = build_graph(req.scenic_id, req.dataset, req.hour, req.crowd_overrides, req.mode == 'quiet')
    leg = segment(g, req.start, req.end, 70)
    return dict(node_path=leg['node_ids'], legs=[leg], route_edges=leg['edge_ids'], distance_m=leg['distance_m'],
                walk_minutes=leg['walk_minutes'], stay_minutes=0, total_minutes=leg['walk_minutes'],
                route_geojson=geometry(g,leg['node_ids']), is_simulated=net.get('is_simulated',True), notice=net.get('notice'),
                crowd_optimization_applied=g.graph['crowd_optimization'])
