#!/usr/bin/env python3
"""Upsert QWeather credentials into the local gitignored .env file.

Reads the key and host from QWEATHER_API_KEY/QWEATHER_API_HOST environment
variables (or stdin fallback) and writes only confirmation, never the secret.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / '.env'


def value(name):
    from_env = os.getenv(name, '').strip()
    if from_env:
        return from_env
    return input(f'{name}: ').strip()


def main():
    key = value('QWEATHER_API_KEY')
    host = value('QWEATHER_API_HOST')
    if not key or not host:
        raise SystemExit('QWEATHER_API_KEY and QWEATHER_API_HOST are required')
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    updates = {
        'QWEATHER_ENABLED': '1',
        'QWEATHER_API_KEY': key,
        'QWEATHER_API_HOST': host,
    }
    seen = set()
    out = []
    for line in lines:
        name = line.split('=', 1)[0].strip()
        if name in updates:
            out.append(f'{name}={updates[name]}')
            seen.add(name)
        else:
            out.append(line)
    for name in ('QWEATHER_ENABLED', 'QWEATHER_API_KEY', 'QWEATHER_API_HOST'):
        if name not in seen:
            out.append(f'{name}={updates[name]}')
    ENV.write_text('\n'.join(out).rstrip() + '\n')
    print('QWeather configuration written to .env (secret not echoed).')


if __name__ == '__main__':
    main()
