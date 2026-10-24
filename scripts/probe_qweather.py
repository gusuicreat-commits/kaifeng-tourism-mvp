#!/usr/bin/env python3
"""Read-only QWeather probe; prints status and safe weather fields only."""
import os

from dotenv import load_dotenv

from app.city.weather import QWeatherProvider


def main():
    load_dotenv()
    host = os.getenv('QWEATHER_API_HOST', '').strip()
    if host and not host.startswith('https://'):
        host = 'https://' + host
    provider = QWeatherProvider(os.getenv('QWEATHER_API_KEY', '').strip(), host)
    for name in ('current', 'hourly', 'daily'):
        result = getattr(provider, name)()
        http_status = provider.events[-1]['http_status']
        condition = ''
        temperature = ''
        if result.data:
            row = result.data if name == 'current' else (
                result.data['hours'][0] if name == 'hourly' else
                result.data['days'][0]['periods'][0])
            condition = row['condition']
            temperature = row['temperature_c']
        print(f'{name}: http_status={http_status} status={result.status} '
              f'condition={condition!r} temperature_c={temperature!r}')


if __name__ == '__main__':
    main()
