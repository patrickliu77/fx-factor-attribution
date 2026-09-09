"""Run with python -m fxdash.research; strictly offline and opt-in."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.metadata
import json
from pathlib import Path
import platform
import subprocess
import time

import pandas as pd

from .. import config
from .evaluate import evaluate_pair
from .inputs import (digest, load_raw_offline, prepare_panel, protected_hashes,
                     read_snapshot, reserve_output, save_snapshot, write_json)
from .report import render_report


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def source_hashes(repo):
    root = Path(repo)
    paths = list((root / 'src/fxdash').rglob('*.py'))
    paths.append(root / 'docs/RESEARCH_EVALUATION.md')
    return {p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in sorted(paths) if p.is_file()}


def capture_operations(repo):
    """Observe files only. Cached ages are explicitly labelled as historical."""
    repo = Path(repo)
    result = {'observed_at_utc': timestamp(), 'interpretation': 'saved evidence, not continuous-run acceptance',
              'local_timezone': str(datetime.now().astimezone().tzinfo), 'json_files': {}, 'logs': {}, 'briefing_artifacts': []}
    allowed = {
        'outputs/status.json': ('state','mode','generated_at','contract_last_date','rows','provisional_rows','reasons','model_revision'),
        'outputs/heartbeat.json': ('last_live_success','mode'),
        'outputs/narrative/status.json': ('state','generated_at','last_run','last_published','reasons'),
    }
    for rel, keys in allowed.items():
        path = repo / rel
        entry = {'exists': path.is_file()}
        if path.is_file():
            payload = path.read_bytes()
            entry['sha256'] = digest(payload)
            try:
                data = json.loads(payload)
                entry['saved_fields'] = {k: data[k] for k in keys if k in data}
            except (ValueError, TypeError):
                entry['read_error'] = 'invalid JSON'
        result['json_files'][rel] = entry
    for name in ('live','narrative','publish','briefing'):
        rel = f'outputs/logs/{name}.log'
        path = repo / rel
        entry = {'exists': path.is_file()}
        if path.is_file():
            payload = path.read_bytes()
            entry.update(bytes=len(payload), sha256=digest(payload),
                         modified_utc=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat(timespec='seconds'))
        result['logs'][rel] = entry
    for path in sorted((repo / 'outputs/briefing').rglob('*.json')):
        payload = path.read_bytes()
        result['briefing_artifacts'].append({'path':path.relative_to(repo).as_posix(),'sha256':digest(payload),'bytes':len(payload)})
    return result


def strict_date(value):
    try:
        return datetime.strptime(value, '%Y-%m-%d').date().isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError('expected YYYY-MM-DD') from exc


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, required=True, help='new outputs/research/<run> directory, or new external directory')
    p.add_argument('--repo-root', type=Path, default=config.REPO_ROOT)
    p.add_argument('--start', type=strict_date, default='2023-01-01')
    p.add_argument('--end', type=strict_date, default='2025-12-31')
    p.add_argument('--pairs', nargs='+', choices=config.PAIRS, default=list(config.PAIRS))
    p.add_argument('--windows', nargs='+', type=int, choices=config.WINDOWS, default=[126])
    p.add_argument('--snapshot', type=Path, help='replay saved inputs/; no raw files or network needed')
    p.add_argument('--bootstrap-repetitions', type=int, default=1000)
    p.add_argument('--seed', type=int, default=20260907)
    return p


def run(args, *, progress=print):
    if args.start > args.end or args.bootstrap_repetitions < 1 or len(set(args.pairs)) != len(args.pairs) or len(set(args.windows)) != len(args.windows):
        raise ValueError('invalid dates, repetitions or duplicate experiment choices')
    repo = args.repo_root.resolve()
    before = protected_hashes(repo)
    out = reserve_output(args.out, repo)
    started = time.perf_counter()
    plan = {'start':args.start,'end':args.end,'pairs':args.pairs,'windows':args.windows,
            'blocks':[5,21,63],'bootstrap_repetitions':args.bootstrap_repetitions,'seed':args.seed,
            'model_revision':config.MODEL_REVISION,'study_type':'retrospective_exploratory',
            'attribution':'previous-observation beta times realised factor; intercept omitted per production contract',
            'common_sample':'jointly finite union per pair; reconstructed provisional excluded',
            'production_mutation':False,'network':False,'scheduled_tasks_changed':False}
    manifest = {'schema':'attribution-research-run-1','state':'running','created_at':timestamp(),
                'plan':plan,'plan_sha256':digest(json.dumps(plan,sort_keys=True).encode()),
                'source_hashes':source_hashes(repo),
                'versions':{'python':platform.python_version(), **{k:importlib.metadata.version(k) for k in ('numpy','pandas','scikit-learn','pyarrow')}}}
    try:
        manifest['git_head'] = subprocess.run(['git','rev-parse','HEAD'],cwd=repo,capture_output=True,text=True,check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        manifest['git_head'] = None
    write_json(out/'run.json',manifest)
    write_json(out/'protected-before.json',before)
    try:
        operations = capture_operations(repo)
        write_json(out/'operations.json',operations)
        if args.snapshot:
            available, original = read_snapshot(args.snapshot,args.end)
            if not set(args.pairs).issubset(available):
                raise ValueError('snapshot lacks a requested pair')
            panels = {p:available[p] for p in args.pairs}
            metadata = {k:v for k,v in original.items() if k not in ('schema','panel_hashes')}
            metadata['replayed_from_manifest_sha256'] = digest((args.snapshot/'manifest.json').read_bytes())
            old_code = metadata.get('source_hashes',{})
            manifest['snapshot_source_differences'] = sorted(k for k in old_code.keys() | manifest['source_hashes'].keys()
                if old_code.get(k) != manifest['source_hashes'].get(k))
        else:
            progress('Reading local cache and archived HY OAS; network disabled by construction.')
            raw, sources = load_raw_offline(repo,args.end)
            panels, audits = {}, {}
            for pair in args.pairs:
                panels[pair], audits[pair] = prepare_panel(raw,pair)
            metadata = {'end':args.end,'model_revision':config.MODEL_REVISION,
                        'sources':sources,'input_audits':audits,'source_hashes':manifest['source_hashes'],
                        'vintage_note':'Rebuilt from current cache; not a point-in-time archive.'}
        saved = save_snapshot(out/'inputs',panels,metadata)
        manifest['input_panel_hashes'] = saved['panel_hashes']
        write_json(out/'run.json',manifest)
        result = {'summaries':[],'comparisons':[],'audits':{},'input_audits':metadata['input_audits']}
        (out/'daily').mkdir()
        for pair,panel in panels.items():
            evaluated, paths = evaluate_pair(panel,pair,start=args.start,end=args.end,windows=args.windows,
                repetitions=args.bootstrap_repetitions,seed=args.seed,progress=progress)
            result['summaries'].extend(evaluated['summaries'])
            result['comparisons'].extend(evaluated['comparisons'])
            result['audits'][pair] = evaluated['audit']
            for name,path in paths.items():
                path.to_parquet(out/'daily'/f'{pair}_{name}.parquet')
        write_json(out/'summary.json',result)
        pd.json_normalize(result['summaries']).to_csv(out/'metrics.csv',index=False)
        ci_rows = []
        for row in result['comparisons']:
            common = {k:v for k,v in row.items() if k != 'intervals'}
            ci_rows.extend({**common, **interval} for interval in row['intervals'])
        pd.DataFrame(ci_rows).to_csv(out/'comparisons.csv',index=False)
        after = protected_hashes(repo)
        changes = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
        write_json(out/'protected-after.json',after)
        manifest['protected_files'] = {'before_count':len(before),'after_count':len(after),'changed':changes}
        if changes:
            raise RuntimeError('Protected files changed during the run; inspect manifest before accepting results.')
        manifest.update(state='complete',completed_at=timestamp(),elapsed_seconds=round(time.perf_counter()-started,2))
        render_report(out,result,manifest,operations)
        manifest['artifacts'] = {p.relative_to(out).as_posix():digest(p.read_bytes()) for p in sorted(out.rglob('*'))
                                 if p.is_file() and p.name != 'run.json'}
        write_json(out/'run.json',manifest)
        progress(f'Complete: {out / "report.html"}')
        return out
    except Exception as exc:
        manifest.update(state='failed',failed_at=timestamp(),error=f'{type(exc).__name__}: {exc}')
        write_json(out/'run.json',manifest)
        raise


def main(argv=None):
    args = parser().parse_args(argv)
    return run(args,progress=lambda s:print(s,flush=True))


if __name__ == '__main__':
    main()
