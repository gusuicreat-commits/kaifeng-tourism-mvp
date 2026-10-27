"""Pure tools: initial and remaining plans share prepare -> CP-SAT -> independent validation."""
from .models import SolveResult, Issue, MultiDayRequirements, MultiDayItinerary
from .prepare import prepare
from .solver import solve
from .validation import compare_itineraries, validate_itinerary


def plan_trip(requirements,context):
    if isinstance(requirements,MultiDayRequirements):return unavailable(context)
    return solve(prepare(requirements,context))


def replan_trip(requirements,context,*,completed_prefix=(),prior_itinerary=None,ongoing=None,fixed_order=(),resolved_count=None,history_cost_fen=0,history_cost_unknown=False,history_cost_basis='none'):
    if isinstance(requirements,MultiDayRequirements) or isinstance(prior_itinerary,MultiDayItinerary):return unavailable(context)
    if any(s.progress!='completed' for s in completed_prefix) or any(s.stop_id==g.goal_id for s in completed_prefix for g in requirements.goals):
        return SolveResult(status='invalid',computed_at=context.assessed_at,issues=(Issue(code='HISTORY_REWRITE',message='已完成历史不能再次作为可调整目标'),))
    if completed_prefix:
        if requirements.starts_at<completed_prefix[-1].actual_end:
            return SolveResult(status='invalid',computed_at=context.assessed_at,
                               issues=(Issue(code='HISTORY_OVERLAP',message='新起点时间早于已完成历史'),))
    prior=tuple(s.goal_id for s in prior_itinerary.activities) if prior_itinerary else ()
    problem=prepare(requirements,context,prior,fixed_order)
    problem.max_selected=24-(len(completed_prefix) if resolved_count is None else resolved_count)-(1 if ongoing else 0)
    problem.history_cost_fen=history_cost_fen;problem.history_cost_unknown=history_cost_unknown;problem.history_cost_basis=history_cost_basis
    result=solve(problem)
    if result.status!='feasible':return result
    # The immutable prefix is copied verbatim, not passed as reorderable decision variables.
    return result.model_copy(update={'itineraries':tuple(i.model_copy(update={
        'completed_prefix':tuple(completed_prefix),'ongoing':ongoing}) for i in result.itineraries)})


def unavailable(context):
    return SolveResult(status='unavailable',computed_at=context.assessed_at,
        issues=(Issue(code='MULTI_DAY_UNAVAILABLE',message='多天求解与写入尚未接通，未降为单日规划。'),))
