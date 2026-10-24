"""Adapters for the providers already present in the Kaifeng MVP.

The adapters deliberately import legacy modules at call time. This keeps the
existing unit tests and their provider fakes working while moving routing into
the registry boundary.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

from .contracts import CapabilityRequest, ProviderResult, ProviderStatus, now_iso
from ..schema import TZ


def _configured(name: str) -> bool:
    return os.getenv(name, '').strip().lower() in ('1', 'true', 'yes')


class FlyAIHotelAdapter:
    provider_id = 'flyai'
    capabilities = frozenset({'search_hotels'})
    priority = 10

    def availability(self, capability: str) -> ProviderStatus:
        # A patched search function is an explicit test adapter; production
        # calls require both the feature switch and server-side key.
        from .. import hotels
        patched = getattr(hotels.search, '__module__', hotels.__name__) != hotels.__name__
        if patched or (_configured('FLYAI_ENABLED') and os.getenv('FLYAI_API_KEY', '').strip()):
            return ProviderStatus.ENABLED
        return ProviderStatus.UNAUTHORIZED

    def execute(self, request: CapabilityRequest) -> ProviderResult:
        from .. import hotels
        query = hotels.Search.model_validate({
            key: request.params[key]
            for key in ('check_in', 'check_out', 'max_price', 'rooms', 'guests')
            if key in request.params
        })
        raw = hotels.search(query)
        status = str(raw.get('status', 'provider_error'))
        return ProviderResult(
            status=status,
            data=raw,
            provider_id=self.provider_id,
            capability=request.capability,
            source='FlyAI search-hotel',
            checked_at=raw.get('queried_at') or now_iso(),
            valid_until=(raw.get('items') or [{}])[0].get('expires_at') if raw.get('items') else None,
            scope='hotel_search_result_not_selected_room_or_rate_plan',
            evidence_status='provider_candidate' if status in ('ok', 'empty') else 'unknown',
            limitations=['room_rate_and_occupancy_not_verified', 'provider_starting_price_only'],
            error_code=None if status in ('ok', 'empty') else status.upper(),
        )


class AmapPoiAdapter:
    provider_id = 'amap'
    capabilities = frozenset({'search_parking', 'search_merchants'})
    priority = 10

    def availability(self, capability: str) -> ProviderStatus:
        from .. import location_resolution
        patched = getattr(location_resolution.provider_from_environment, '__module__', location_resolution.__name__) != location_resolution.__name__
        if patched or (_configured('AMAP_LOCATION_SEARCH_ENABLED') and _configured('AMAP_TERMS_REVIEWED') and os.getenv('AMAP_WEB_SERVICE_KEY', '').strip()):
            return ProviderStatus.ENABLED
        return ProviderStatus.UNAUTHORIZED

    def execute(self, request: CapabilityRequest) -> ProviderResult:
        from .. import location_resolution
        category = str(request.params.get('category', 'parking'))
        provider = location_resolution.provider_from_environment()
        query = str(request.params.get('query_text', ''))[:80]
        types = '150900' if category == 'parking' else '050000'
        try:
            answer = provider.search_pois(query, version=provider.api_version, types=types)
            moment = datetime.now(TZ)
            items = [{
                'id': f'amap:{p.poi_id}', 'name': p.name, 'address': p.address,
                'category': p.category, 'source': 'AMap POI', 'checked_at': moment.isoformat(),
                'expires_at': (moment + timedelta(minutes=3)).isoformat(), 'price_display': None,
                'image': None, 'detail_url': None, 'attributes': {}, 'fee_status': 'unknown',
                'availability_status': 'unknown', 'supplies_status': 'unknown',
                'walking_route_status': 'needs_explicit_destination_and_verified_entrance',
                'hotel_free_parking': False,
            } for p in answer.pois]
            status = 'ok' if answer.status == 'found' else answer.status
            return ProviderResult(
                status=status, data={'status': status, 'items': items}, provider_id=self.provider_id,
                capability=request.capability, source='AMap POI', checked_at=moment.isoformat(),
                valid_until=(moment + timedelta(minutes=3)).isoformat(), scope='provider_poi_candidate',
                evidence_status='provider_candidate' if status == 'ok' else 'unknown',
                limitations=['poi_is_not_verified_entrance', 'route_and_fee_not_verified'],
                error_code=None if status == 'ok' else status.upper(),
            )
        finally:
            close = getattr(provider, 'close', None)
            if callable(close):
                close()


class FutureWeatherAdapter:
    provider_id = 'qweather'
    capabilities = frozenset({'get_weather'})
    priority = 10

    def availability(self, capability: str) -> ProviderStatus:
        # The existing weather service requires a trip snapshot and has its
        # own workflow. Keep this registry slot visible but unavailable until
        # that workflow is explicitly bound; a key alone must not advertise a
        # generic weather capability.
        return ProviderStatus.UNAVAILABLE

    def execute(self, request: CapabilityRequest) -> ProviderResult:
        return ProviderResult(status='provider_unavailable', provider_id=self.provider_id,
                              capability=request.capability, error_code='WEATHER_WORKFLOW_NOT_BOUND')
