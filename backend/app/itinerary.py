"""OR-Tools CP-SAT selective path with time windows. Business model, no custom solver."""
from dataclasses import dataclass
from fastapi import HTTPException


@dataclass(frozen=True)
class Visit:
    node_id: str
    service: int  # seconds
    earliest: int
    latest: int  # latest service start, not latest arrival at venue
    reward: int = 0
    session_id: str | None = None
    source_id: str | None = None
    name: str | None = None


class SolverUnavailable(RuntimeError):
    pass


@dataclass
class Solution:
    indices: list[int]
    starts: list[int]
    status: str
    objective: float | None
    bound: float | None


def solve(visits, matrix, required_nodes, required_sessions, budget, mode, time_limit=1.0):
    try:
        from ortools.sat.python import cp_model
    except ImportError as exc:
        raise SolverUnavailable('OR-Tools 未安装') from exc
    n = len(visits)
    if n > 80:
        raise HTTPException(422, detail='候选点或场次超过首版80项上限，请缩小规划范围')
    model = cp_model.CpModel()
    active = [model.NewBoolVar(f'visit_{i}') for i in range(n)]
    times = [model.NewIntVar(0, budget, f'start_{i}') for i in range(n)]
    model.Add(active[0] == 1)
    model.Add(active[-1] == 1)
    model.Add(times[0] == 0)
    arcs = [(n-1, 0, model.NewConstant(1))]  # close circuit artificially; excluded from timing/cost
    arc_vars = {}
    costs = []
    for i, v in enumerate(visits):
        model.Add(times[i] >= v.earliest).OnlyEnforceIf(active[i])
        model.Add(times[i] <= v.latest).OnlyEnforceIf(active[i])
        model.Add(times[i] + v.service <= budget).OnlyEnforceIf(active[i])
        if i not in (0, n-1):
            arcs.append((i, i, active[i].Not()))
            costs.append(-v.reward * active[i])
        for j, dest in enumerate(visits):
            if i == j or i == n-1 or j == 0:
                continue
            leg = matrix[v.node_id, dest.node_id]
            if leg is None:
                continue
            arc = model.NewBoolVar(f'arc_{i}_{j}')
            arcs.append((i, j, arc))
            arc_vars[i, j] = arc
            model.Add(times[j] >= times[i] + v.service + leg['walk_seconds']).OnlyEnforceIf(arc)
            # All soft costs expressed in approximate walking seconds; no seconds/metres mixing.
            cost = round(leg['weighted_cost'] / max(1, leg['distance_m']) * leg['walk_seconds'])
            costs.append(cost * arc)
    model.AddCircuit(arcs)
    groups = {}
    for i, v in enumerate(visits[1:-1], 1):
        groups.setdefault(v.node_id, []).append(i)
    for node_id, indices in groups.items():
        model.Add(sum(active[i] for i in indices) == 1 if node_id in required_nodes
                  else sum(active[i] for i in indices) <= 1)
    if set(required_nodes) - set(groups):
        raise HTTPException(422, detail='必去点无可用时间窗或可通行道路')
    for sid in required_sessions:
        choices = [active[i] for i, v in enumerate(visits) if v.session_id == sid]
        if not choices:
            raise HTTPException(422, detail='必看演出无该日期有效场次，或其时间超过可用时间')
        model.Add(sum(choices) == 1)
    # Finish earlier breaks ties and discourages needless waiting; waiting counts against budget.
    model.Minimize(sum(costs) + times[-1])
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 42
    status = solver.Solve(model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        if status == cp_model.INFEASIBLE:
            raise HTTPException(422, detail='未找到满足必去点、演出时间窗和出园要求的路线；可能超过可用时间，请调整需求。')
        raise SolverUnavailable('求解器未在限时内返回可行解')
    indices = [0]
    while indices[-1] != n-1:
        current = indices[-1]
        indices.append(next(j for (i, j), a in arc_vars.items() if i == current and solver.Value(a)))
    return Solution(indices, [solver.Value(times[i]) for i in indices], solver.StatusName(status),
                    solver.ObjectiveValue(), solver.BestObjectiveBound())


def conservative_fallback(visits, matrix, required_nodes, required_sessions, budget):
    """No replacement optimiser: preserve requested mandatory order, drop all optional stops.

    Every constraint is still checked by the caller. Failure means unavailable, not proven infeasible.
    """
    indices = [0]
    for node_id in required_nodes:
        choices = [i for i, v in enumerate(visits) if v.node_id == node_id
                   and (not any(x.session_id in required_sessions for x in visits if x.node_id == node_id)
                        or v.session_id in required_sessions)]
        if not choices:
            raise HTTPException(503, detail='优化服务暂不可用，必去点无法安全降级；请稍后重试')
        indices.append(min(choices, key=lambda i: visits[i].earliest))
    indices.append(len(visits)-1)
    starts = [0]
    for i, j in zip(indices, indices[1:]):
        leg = matrix[visits[i].node_id, visits[j].node_id]
        if leg is None:
            raise HTTPException(503, detail='优化服务暂不可用，保守路线不连通')
        t = max(visits[j].earliest, starts[-1] + visits[i].service + leg['walk_seconds'])
        if t > visits[j].latest or t + visits[j].service > budget:
            raise HTTPException(503, detail='优化服务暂不可用，保守路线无法满足时间窗；请稍后重试')
        starts.append(t)
    if not required_sessions.issubset({visits[i].session_id for i in indices}):
        raise HTTPException(503, detail='优化服务暂不可用，不能保证必看场次')
    return Solution(indices, starts, 'FALLBACK_VALIDATED', None, None)
