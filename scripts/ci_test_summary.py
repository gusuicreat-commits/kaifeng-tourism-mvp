"""Print and persist actual execution counts; missing reports are failures, never passes."""
import argparse
import json
import os
from pathlib import Path
import xml.etree.ElementTree as ET

parser = argparse.ArgumentParser()
parser.add_argument('report', type=Path)
parser.add_argument('--no-skips', action='store_true')
parser.add_argument('--expected-skips', type=int)
args = parser.parse_args()
if not args.report.is_file():
    line = f'{args.report.name}: NOT EXECUTED / missing report (no passing result inferred)'
    print(line)
    if summary := os.getenv('GITHUB_STEP_SUMMARY'):
        with open(summary, 'a') as handle:
            handle.write(line + '\n\n')
    raise SystemExit(1)
if args.report.suffix == '.xml':
    root = ET.parse(args.report).getroot()
    suites = [root] if root.tag == 'testsuite' else list(root.iter('testsuite'))
    total = sum(int(s.get('tests', 0)) for s in suites)
    failed = sum(int(s.get('failures', 0)) + int(s.get('errors', 0)) for s in suites)
    skipped = sum(int(s.get('skipped', 0)) for s in suites)
    passed = total - failed - skipped
else:
    stats = json.loads(args.report.read_text())['stats']
    passed, failed, skipped = (stats[k] for k in ('expected', 'unexpected', 'skipped'))
    # Flaky retries are visible failures; CI currently runs without retries.
    failed += stats.get('flaky', 0)
    total = passed + failed + skipped
line = f'{args.report.name}: total={total}, passed={passed}, failed/errors={failed}, skipped={skipped}'
print(line)
if summary := os.getenv('GITHUB_STEP_SUMMARY'):
    with open(summary, 'a') as handle:
        handle.write(line + '\n\n')
if (not total or failed or (args.no_skips and skipped)
        or (args.expected_skips is not None and skipped != args.expected_skips)):
    raise SystemExit(1)
