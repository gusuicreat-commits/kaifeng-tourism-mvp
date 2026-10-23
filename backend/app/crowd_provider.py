"""Replaceable estimator boundary. No dependency from routing to a particular model."""
import logging
import math
from datetime import datetime
from typing import Protocol
from .crowds import crowd_summary, TZ

log = logging.getLogger(__name__)


class CrowdProvider(Protocol):
    def estimate(self, scenic_id: str, dataset: str, target_time: datetime) -> dict: ...


class HistoricalCrowdProvider:
    def estimate(self, scenic_id, dataset, target_time):
        result = crowd_summary(scenic_id, dataset, target_time.hour, target_time=target_time)
        result['method'] = 'source-prioritized equal-day historical mean; minimum 3 dates'
        return result


provider: CrowdProvider = HistoricalCrowdProvider()


def estimate_crowds(sid, dataset, target_time):
    try:
        result = provider.estimate(sid, dataset, target_time)
        if result['dataset'] != dataset or result['is_simulated'] != (dataset == 'demo'):
            raise ValueError('Estimator dataset mismatch')
        for node in result['nodes']:
            if dataset == 'reported' and node['is_simulated']:
                raise ValueError('Simulated estimate in reported dataset')
            value = node['level']
            if value is not None and (not math.isfinite(value) or not 0 <= value <= 1):
                raise ValueError('Invalid normalized crowd level')
        return result
    except Exception:
        log.exception('Crowd estimator unavailable; route planning uses no crowd claims')
        return {'nodes': [], 'dataset': dataset, 'is_simulated': dataset == 'demo',
                'notice': '客流参考暂不可用，已按普通路线规划。', 'unavailable': True}
