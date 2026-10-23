"""NetworkX Dijkstra and road geometry. No itinerary selection or LLM calls here."""
import math
import networkx as nx
from fastapi import HTTPException
from .db import network_get
from .crowd_provider import estimate_crowds


def build_graph(sid, dataset, hour, overrides, quiet, target_time=None):
    from datetime import datetime
    from .crowds import TZ
    network = network_get(sid)
    if not network:
        raise HTTPException(422, detail='该景区尚无内部路网，暂不能生成内部路线')
    if target_time is None:
        now = datetime.now(TZ)
        target_time = now if now.hour//2 == hour//2 else now.replace(hour=hour, minute=0, second=0, microsecond=0)
    crowd = estimate_crowds(sid, dataset, target_time)
    levels = {r['node_id']: r['level'] for r in crowd['nodes']}
    g = nx.DiGraph()  # one-way roads must never be silently made bidirectional
    g.graph.update(network=network)
    for f in network['features']:
        p = f['properties']
        if f['geometry']['type'] == 'Point':
            if p['id'] in g:
                raise HTTPException(422, detail='路网包含重复点位 ID')
            if not math.isfinite(p.get('stay_minutes', 0)) or p.get('stay_minutes', 0) < 0:
                raise HTTPException(422, detail='点位停留时间无效')
            g.add_node(p['id'], **p, coordinates=f['geometry']['coordinates'])
    if set(overrides) - set(g):
        raise HTTPException(422, detail='情景中存在未知点位')
    levels.update(overrides)
    use_crowds = quiet and any(levels.get(n) is not None for n in g if g.nodes[n]['kind'] == 'poi')
    g.graph['crowd_optimization'] = use_crowds
    edge_ids = set()
    for f in network['features']:
        if f['geometry']['type'] != 'LineString':
            continue
        p = f['properties']
        if p['id'] in edge_ids or p['from'] not in g or p['to'] not in g:
            raise HTTPException(422, detail='路网边 ID 重复或端点不存在')
        edge_ids.add(p['id'])
        distance = p['distance_m']
        if not math.isfinite(distance) or distance <= 0:
            raise HTTPException(422, detail='道路长度必须是正有限数')
        if p.get('passable', True) is False or p.get('status') == 'closed':
            continue
        # Unknown is a disclosed conservative cost, never a measured low level.
        risk = max(levels.get(n) if levels.get(n) is not None else .5 for n in (p['from'], p['to']))
        attrs = dict(distance_m=distance, risk=risk, weight=distance * (1 + 4 * risk if use_crowds else 1),
                     edge_id=p['id'], geometry=f['geometry']['coordinates'], reverse=False,
                     source_id=p.get('source_id'), is_simulated=p.get('is_simulated', network.get('is_simulated', True)))
        if g.has_edge(p['from'], p['to']):
            raise HTTPException(422, detail='平行道路需先由 GIS 适配器明确选择，不能静默覆盖')
        g.add_edge(p['from'], p['to'], **attrs)
        if not p.get('oneway', False):
            if g.has_edge(p['to'], p['from']):
                raise HTTPException(422, detail='道路方向重复')
            g.add_edge(p['to'], p['from'], **(attrs | {'reverse': True}))
    if 'gate' not in g or 'exit' not in g:
        raise HTTPException(422, detail='路网缺少入口或出口')
    return g, network, crowd, levels


def route_path(g, start, end):
    try:
        view = nx.subgraph_view(g, filter_node=lambda n: n not in ('gate', 'exit') or n in (start, end))
        return nx.shortest_path(view, start, end, weight='weight', method='dijkstra')
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        raise HTTPException(422, detail='起终点无可用连通道路')


def segment(g, start, end, speed):
    path = route_path(g, start, end)
    edges = [g[a][b] for a, b in zip(path, path[1:])]
    distance = sum(e['distance_m'] for e in edges)
    return dict(start=start, end=end, node_ids=path, edge_ids=[e['edge_id'] for e in edges],
                distance_m=distance, walk_seconds=math.ceil(distance / speed * 60),
                walk_minutes=round(distance / speed, 1), weighted_cost=sum(e['weight'] for e in edges),
                risk_distance=sum(e['distance_m'] * e['risk'] for e in edges))


def path_matrix(g, nodes, speed):
    result = {}
    for a in sorted(set(nodes)):
        for b in sorted(set(nodes)):
            try:
                result[a, b] = segment(g, a, b, speed)
            except HTTPException:
                result[a, b] = None  # no invented straight-line connections
    return result


def geometry(g, full):
    coordinates, edge_ids = [], []
    for a, b in zip(full, full[1:]):
        e = g[a][b]
        coords = e['geometry'][::-1] if e['reverse'] else e['geometry']
        coordinates += coords if not coordinates else coords[1:]
        edge_ids.append(e['edge_id'])
    net = g.graph['network']
    if not coordinates:
        coordinates = [g.nodes[full[0]]['coordinates']]*2
    features = [{'type':'Feature', 'geometry':{'type':'LineString','coordinates':coordinates},
                 'properties':{'edge_ids':edge_ids, 'is_simulated':net.get('is_simulated',True)}}]
    return {'type': 'FeatureCollection', 'is_simulated': net.get('is_simulated', True),
            'coordinate_space': net.get('coordinate_space'), 'features': features}
