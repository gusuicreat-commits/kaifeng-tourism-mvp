#!/usr/bin/env python3
"""Export architecture contracts without starting the app or touching the database."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.city.schema import CityContracts, CityTripV2, Strict
from app.city.state_schema import CityDraftV2, CityStateV2
from app.city.versions import CreateBody, MutationBody


class CityContractsV2(Strict):
    trip: CityTripV2
    draft: CityDraftV2
    state: CityStateV2
    create: CreateBody
    mutation: MutationBody


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='fail when the committed contract differs')
    args = parser.parse_args()
    for name,model in (('city-trip-v1.schema.json',CityContracts),('city-trip-v2.schema.json',CityContractsV2)):
        path = ROOT / 'docs' / 'contracts' / name
        content = json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + '\n'
        if args.check:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(name+' differs; regenerate with scripts/export_city_contracts.py')
            print(name+' matches source')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            print(path)


if __name__ == '__main__':
    main()
