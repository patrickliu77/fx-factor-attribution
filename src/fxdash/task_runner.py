"""Standard-library supervisor: computational crashes cannot hide behind old green status."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import uuid


CLOCK_DIAGNOSTIC = 'heartbeat time is ahead of observation; check the system clock'


def now_utc():
    return datetime.now(timezone.utc)


def read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=True, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def stamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        # Historical pipeline timestamps used the host's local zone.
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class Busy(RuntimeError):
    pass


class RunLock:
    def __init__(self, path):
        self.path = Path(path)

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open('a+b')
        if self.stream.tell() == 0:
            self.stream.write(b'0')
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise Busy('live_run_in_progress') from None
        return self

    def __exit__(self, *args):
        self.stream.close()


def latest_attempt(output_dir, *, clock=now_utc):
    """Sanitized public projection. Never export raw logs, paths or exceptions."""
    path = Path(output_dir) / 'task_runs' / 'live' / 'latest.json'
    if not path.exists():
        return {'state': 'not_recorded'}
    row = read_json(path)
    states = {'running', 'succeeded', 'failed', 'crashed', 'timed_out', 'launch_failed',
              'completion_unconfirmed', 'interrupted'}
    started = stamp(row.get('started_at'))
    if row.get('state') not in states or not started or not row.get('run_id'):
        return {'state': 'unreadable'}
    result = {k: row[k] for k in ('state', 'run_id', 'started_at', 'finished_at', 'deadline',
              'exit_code', 'exit_hex', 'source', 'contract_last_date') if k in row}
    if row['state'] == 'running':
        end = stamp(row.get('deadline'))
        if end is None or end < started:
            return {'state': 'unreadable'}
        observed = stamp(str(clock()))
        if observed is None:
            return {'state': 'unreadable'}
        if observed > end:
            result['state'] = 'interrupted'
    return result


def status_view(output_dir, stored, *, clock=now_utc):
    observed = stamp(str(clock()))
    attempt = latest_attempt(output_dir, clock=lambda: observed)
    diagnostics = []
    if not isinstance(stored, dict):
        stored = {}
        diagnostics.append('saved calculation status unreadable')
    saved = stored if stored.get('contract_last_date') else read_json(Path(output_dir)/'task_runs/live/last_success.json') or stored
    original_pulse = stored.get('heartbeat')
    valid_pulse = isinstance(original_pulse, dict)
    original_pulse = original_pulse if valid_pulse else {}
    warn, critical = original_pulse.get('warn_hours', 26), original_pulse.get('crit_hours', 72)
    try:
        valid_thresholds = (type(warn) in (int, float) and type(critical) in (int, float)
                            and math.isfinite(warn) and math.isfinite(critical) and 0 < warn <= critical)
    except OverflowError:
        valid_thresholds = False
    if not valid_thresholds:
        warn, critical = 26, 72
        diagnostics.append('heartbeat thresholds unreadable')
    last = stamp(original_pulse.get('last_live_success'))
    age = (observed-last).total_seconds()/3600 if observed and last else None
    if observed is None:
        fresh, note = 'red', 'heartbeat observation time unreadable'
    elif not valid_pulse or last is None:
        fresh, note = 'red', 'heartbeat record unreadable'
    elif age < 0:
        fresh, note = 'red', CLOCK_DIAGNOSTIC
    elif age > critical:
        fresh, note = 'red', f'suspected scheduler stall: {age:.0f} hours without a successful live run'
    elif age > warn:
        fresh, note = 'yellow', f'suspected scheduler stall: {age:.0f} hours without a successful live run'
    else:
        fresh, note = 'green', 'scheduler healthy'
    if not valid_thresholds:
        fresh = 'red'
        if note == 'scheduler healthy':
            note = 'heartbeat thresholds unreadable'
    if fresh == 'red' and note not in diagnostics:
        diagnostics.append(note)
    # Export only the heartbeat schema, never malformed values from the saved
    # record. In particular NaN/Infinity thresholds must not reach JSONResponse.
    pulse = {'last_live_success': last.isoformat() if last else None,
             'age_hours': age, 'state': fresh, 'note': note,
             'warn_hours': warn, 'crit_hours': critical}
    rank = {'green': 0, 'yellow': 1, 'red': 2}
    saved_state = stored.get('state')
    if not isinstance(saved_state, str) or saved_state not in rank:
        saved_state = 'red'
        diagnostics.append('saved calculation state unreadable')
    state = max((saved_state, fresh), key=rank.__getitem__)
    failed = attempt['state'] in {'failed', 'crashed', 'timed_out', 'launch_failed', 'completion_unconfirmed', 'interrupted', 'unreadable'}
    if failed:
        state = 'red'
    elif attempt['state'] == 'running' and state == 'green':
        state = 'yellow'
    reasons = stored.get('reasons')
    reasons = [r for r in reasons if isinstance(r, str)] if isinstance(reasons, list) else []
    reasons.extend(note for note in diagnostics if note not in reasons)
    return {**stored, 'state': state, 'last_success_state': saved_state, 'reasons': reasons,
            'heartbeat': pulse, 'runtime': {'observed_at': observed.isoformat() if observed else None,
             'latest_attempt': attempt, 'last_success_at': last.isoformat() if last else None,
             'attribution_as_of': saved.get('contract_last_date'),
             'provisional_rows': saved.get('provisional_rows'),
             'scope': 'saved_attempt_and_data_observations'}}


def supervise(repo, output_dir, *, executable=None, runner=subprocess.run, clock=now_utc,
              timeout=6900, source='scheduled_task'):
    """One owned child; existing Task Scheduler policy owns subsequent retries."""
    repo, output_dir = Path(repo).resolve(), Path(output_dir).resolve()
    if source not in {'scheduled_task', 'manual'}:
        raise ValueError('invalid_source')
    root = output_dir / 'task_runs' / 'live'
    try:
        with RunLock(root / 'run.lock'):
            started = clock()
            run_id = started.strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:12]
            folder = root / run_id
            row = {'schema_version': 1, 'run_id': run_id, 'source': source, 'state': 'running',
                   'started_at': started.isoformat(), 'deadline': (started+timedelta(seconds=timeout+60)).isoformat()}
            atomic_json(folder / 'start.json', row)
            atomic_json(root / 'latest.json', row)
            previous = read_json(output_dir/'status.json')
            previous_pulse = previous.get('heartbeat')
            previous_success = stamp(previous_pulse.get('last_live_success')) if isinstance(previous_pulse, dict) else None
            if previous.get('mode') == 'live' and previous_success:
                atomic_json(root/'last_success.json', previous)
            env = dict(os.environ, PYTHONPATH=str(repo/'src'), PYTHONUNBUFFERED='1',
                       PYTHONIOENCODING='utf-8', PYTHONFAULTHANDLER='1')
            exe = Path(executable or sys.executable)
            if exe.name.lower() == 'pythonw.exe':
                exe = exe.with_name('python.exe')
            command = [str(exe), '-u', '-X', 'faulthandler', '-W', 'ignore', '-m', 'fxdash.run', '--mode', 'live']
            try:
                with (folder/'worker.log').open('wb') as log:
                    result = runner(command, cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT,
                                    timeout=timeout, check=False,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                    completed = clock()
                code = result.returncode
                row.update(exit_code=code, exit_hex=f'0x{code & 0xffffffff:08x}')
                if code:
                    row['state'] = 'crashed' if code < 0 or code & 0x80000000 else 'failed'
                else:
                    current = read_json(output_dir/'status.json')
                    current_pulse = current.get('heartbeat')
                    last = stamp(current_pulse.get('last_live_success')) if isinstance(current_pulse, dict) else None
                    # A zero exit alone cannot confirm a new commit. An old
                    # heartbeat, including a future stamp after a clock change,
                    # belongs to the previous run even if it passes >= started.
                    committed = (current != previous and last and last != previous_success
                                 and started <= last <= completed and current.get('mode') == 'live')
                    row['state'] = 'succeeded' if committed else 'completion_unconfirmed'
                    row['contract_last_date'] = current.get('contract_last_date')
                    if row['state'] == 'succeeded':
                        atomic_json(root/'last_success.json', current)
            except subprocess.TimeoutExpired:
                row['state'] = 'timed_out'
            except Exception:
                row['state'] = 'launch_failed'
            except BaseException:
                row['state'] = 'interrupted'
                raise
            finally:
                row['finished_at'] = clock().isoformat()
                atomic_json(folder/'finish.json', row)
                atomic_json(root/'latest.json', row)
            return row
    except Busy:
        return {'state': 'busy'}


def main():
    # No numpy/pandas import in the supervising process.
    repo = Path(__file__).resolve().parents[2]
    result = supervise(repo, repo/'outputs')
    return 0 if result['state'] in {'succeeded', 'busy'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
