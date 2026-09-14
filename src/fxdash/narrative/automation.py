"""Shared local dependency and delivery gates for the existing scheduled entries."""
from __future__ import annotations

from datetime import datetime, time
import json
import os
from pathlib import Path
import subprocess

from . import morning as M, catchup as C, briefing_archive as A, public_delivery as P
from .. import task_runner as T

PAIRS = C.PAIRS


def active(kind, moment):
    if kind not in {'morning','catchup'}:
        raise ValueError('invalid_automation_kind')
    return M.slot(moment) != 'idle' if kind == 'morning' else C.due(moment)


def saved_today(root, day):
    for mode in ('edition','catchup'):
        folder = 'days' if mode == 'edition' else 'catchup'
        path = Path(root)/'briefing'/folder/day/'edition.json'
        if path.exists():
            brief = A.read_edition(path, mode=mode)
            if brief['state'] in {'ready','numbers_only'}:
                return brief
    return None


def input_state(output_dir, day, kind):
    from ..web.store import Snapshot
    try:
        snapshot = Snapshot(output_dir)
        expected = M.previous_session(datetime.fromisoformat(day).date())
        dates = []
        for pair in sorted(PAIRS):
            combo = snapshot.combo(pair,126,'ols')
            if combo is None or not combo.dates:
                return {'state':'missing_inputs'}
            dates.append(combo.dates[-1])
        ready = len(set(dates)) == 1 and (dates[0] == expected if kind == 'morning' else expected <= dates[0] <= day)
        return {'state':'ready' if ready else 'stale_inputs', 'dates':sorted(set(dates)), 'required_date':expected}
    except Exception:
        return {'state':'missing_inputs'}


def request_live():
    """Wake only the already registered FX task; no new process window or task."""
    if os.name != 'nt':
        return {'state':'unsupported_host'}
    result = subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',
                             "$ErrorActionPreference='Stop'; Start-ScheduledTask -TaskName 'fxdash-live'"],
                            capture_output=True, timeout=15, check=False,
                            creationflags=subprocess.CREATE_NO_WINDOW)
    return {'state':'requested' if result.returncode == 0 else 'request_failed'}


def before(kind, output_dir, *, clock=M.now_utc, checker=None, launcher=None):
    """A missing quant dependency never consumes an LLM generation claim."""
    moment = clock()
    if not active(kind,moment):
        return {'state':'idle', 'proceed':True}
    day = M.local_time(moment).date().isoformat()
    root = Path(output_dir)/'automation'/day
    try:
        with M.DayLock(root/'dependency.lock'):
            # Existing text must remain publishable even if a later data update fails.
            if saved_today(output_dir,day):
                return {'state':'saved_edition', 'proceed':True}
            if kind == 'morning' and M.slot(moment) == 'publish':
                packet = M.read_json(Path(output_dir)/'briefing'/'days'/day/'packet.json')
                if M.packet_eligible(packet,moment)[0]:
                    return {'state':'saved_morning_inputs','proceed':True}
            if kind == 'catchup':
                folder = Path(output_dir)/'briefing'/'catchup'/day
                claim, packet = M.read_json(folder/'generation.claim'), M.read_json(folder/'packet.json')
                if claim and not C.packet_checks(packet,moment) and claim.get('packet_hash') == M.digest(packet):
                    return {'state':'claimed_catchup_inputs','proceed':True}
            state = (checker or input_state)(output_dir,day,kind)
            result = {'date':day, 'observed_at':moment.isoformat(), 'inputs':state, 'proceed':False}
            if state['state'] == 'ready':
                result.update(state='inputs_ready',proceed=True)
            elif T.latest_attempt(output_dir,clock=clock)['state'] == 'running':
                result['state'] = 'waiting_for_live_task'
            else:
                path = root/'live_request.json'
                old = M.read_json(path)
                attempts = old.get('attempts',0)
                try:
                    age = (moment-datetime.fromisoformat(old['requested_at'])).total_seconds()
                except (KeyError,ValueError,TypeError):
                    age = 3600
                if type(attempts) is not int or attempts < 0 or attempts >= 2:
                    result['state'] = 'live_request_limit'
                elif age < 3600:
                    result['state'] = 'waiting_for_live_task'
                else:
                    request = {'state':'requesting','requested_at':moment.isoformat(),'attempts':attempts+1}
                    M.atomic_json(path,request)
                    try:
                        request.update((launcher or request_live)())
                    except Exception:
                        request['state'] = 'request_unconfirmed'
                    M.atomic_json(path,request)
                    result['state'] = 'waiting_for_live_task'
                    result['live_request_state'] = request['state']
            M.atomic_json(root/'dependencies.json',result)
            return result
    except M.Busy:
        return {'state':'busy','proceed':False}


def after(output_dir, *, clock=M.now_utc, verifier=None, notifier=None):
    moment = clock()
    local = M.local_time(moment)
    if local.weekday() >= 5 or local.time() < time(9):
        return {'state':'idle'}
    day = local.date().isoformat()
    brief = saved_today(output_dir,day)
    if not brief:
        return {'state':'waiting_for_edition'}
    from .audio_briefing import edition_path
    if A.receipt(edition_path(output_dir,brief['mode'],day).parent,brief)['state'] != 'published':
        return {'state':'waiting_for_push'}
    result = {'date':day,'observed_at':moment.isoformat(),'edition_hash':brief['edition_hash']}
    try:
        result['public'] = (verifier or P.verify)(output_dir,brief,clock=clock)
        if result['public']['state'] == 'verified':
            from .subscriptions import deliver
            result['email'] = (notifier or deliver)(output_dir,brief,result['public'],clock=clock)
        else:
            result['email'] = {'state':'waiting_for_public_audio'}
        result['state'] = 'checked'
    except Exception as exc:
        result.update(state='delivery_check_failed',error_type=type(exc).__name__)
    M.atomic_json(Path(output_dir)/'automation'/day/'delivery.json',result)
    return result


def report(result):
    # Only internal state fields; no keys, subscriber identifiers or raw exceptions.
    print(json.dumps(result,ensure_ascii=True),flush=True)
