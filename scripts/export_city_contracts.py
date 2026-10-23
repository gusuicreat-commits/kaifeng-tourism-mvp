#!/usr/bin/env python3
"""Export architecture contracts without starting the app or touching the database."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.city.schema import CityContracts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='fail when the committed contract differs')
    args = parser.parse_args()
    path = ROOT / 'docs' / 'contracts' / 'city-trip-v1.schema.json'
    content = json.dumps(CityContracts.model_json_schema(), ensure_ascii=False, indent=2) + '\n'
    if args.check:
        if not path.exists() or path.read_text() != content:
            raise SystemExit('City contract differs; regenerate with scripts/export_city_contracts.py')
        print('City contract matches schema.py')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        print(path)


if __name__ == '__main__':
    main()
