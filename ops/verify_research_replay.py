"""Compare saved offline runs exactly, without refitting or changing either run."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from fxdash.research.inputs import digest, protected_hashes


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def verify(reference, candidate, repo):
    reference, candidate = Path(reference), Path(candidate)
    first, second = read(reference/'run.json'), read(candidate/'run.json')
    pairs = second['plan']['pairs']
    if not set(pairs).issubset(first['plan']['pairs']):
        raise ValueError('candidate pairs must be a subset of the reference run')
    a, b = read(reference/'summary.json'), read(candidate/'summary.json')
    left_plan = {k:v for k,v in first['plan'].items() if k != 'pairs'}
    right_plan = {k:v for k,v in second['plan'].items() if k != 'pairs'}
    files = sorted((candidate/'daily').glob('*.parquet'))
    artifacts = {}
    for label, root, manifest in (('reference',reference,first),('candidate',candidate,second)):
        artifacts[label] = all((root/name).is_file() and digest((root/name).read_bytes()) == expected
                               for name,expected in manifest['artifacts'].items())
    checks = {
        'runs_complete':first['state'] == second['state'] == 'complete',
        'plans_equal_except_pair_subset':left_plan == right_plan,
        'source_hashes_equal':first['source_hashes'] == second['source_hashes'],
        'input_panels_equal':all(first['input_panel_hashes'][p] == second['input_panel_hashes'][p] for p in pairs),
        'metrics_equal': [r for r in a['summaries'] if r['pair'] in pairs] == b['summaries'],
        'comparisons_equal': [r for r in a['comparisons'] if r['pair'] in pairs] == b['comparisons'],
        'daily_parquets_equal':bool(files) and all(digest(p.read_bytes()) == digest((reference/'daily'/p.name).read_bytes()) for p in files),
        'reference_artifacts_intact':artifacts['reference'],
        'candidate_artifacts_intact':artifacts['candidate'],
        'production_still_unchanged':read(reference/'protected-before.json') == protected_hashes(repo),
    }
    return {'checked_at_utc':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'passed':all(checks.values()),'pairs':pairs,'daily_files_compared':len(files),'checks':checks}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('reference',type=Path)
    p.add_argument('candidate',type=Path)
    p.add_argument('--out',type=Path,required=True,help='new JSON file within the candidate run')
    args = p.parse_args()
    if not args.out.resolve().is_relative_to(args.candidate.resolve()):
        raise ValueError('verification output must stay inside the candidate run')
    result = verify(args.reference,args.candidate,Path(__file__).resolve().parents[1])
    with args.out.open('x',encoding='utf-8') as handle:
        json.dump(result,handle,ensure_ascii=False,indent=2,allow_nan=False)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
