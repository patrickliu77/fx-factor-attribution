"""Windowless Windows Task Scheduler entry point, launched with pythonw.exe."""
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_delivery_runtime as R


def main(repo=None):
    repo = Path(repo) if repo is not None else Path(__file__).resolve().parents[1]
    os.chdir(repo)
    sys.path.insert(0, str(repo / "src"))
    health = R.runtime_status(repo)
    if health['state'] != 'ready':
        R.record_task_status(repo/'outputs', 'briefing', 3, {'state':'failed'})
        print('runtime_health_failed', health['state'], flush=True)
        return 3
    R.bootstrap_tools()
    destination = repo / "outputs" / "logs" / "briefing.log"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8", buffering=1) as stream, redirect_stdout(stream), redirect_stderr(stream):
        print(datetime.now(timezone.utc).isoformat(), "scheduled_entry_started", flush=True)
        try:
            from fxdash.narrative.speech_settings import refresh_user_speech_environment
            refresh_user_speech_environment()
            from fxdash.narrative.morning_dispatch import main as dispatch
            from fxdash.narrative import morning as M
            action = M.slot(M.now_utc())
            from fxdash.narrative import automation
            gate = automation.before('morning', repo/'outputs')
            if not gate['proceed']:
                automation.report(gate)
                status = R.record_task_status(repo/'outputs', 'briefing', 0, {'state':gate['state']})
                automation.report(status)
                return status['exit_code']
            result = dispatch(["--scheduled-task"])
            delivery = {'state':'idle'}
            if action != 'idle':
                delivery = automation.after(repo/'outputs')
                automation.report(delivery)
            status = R.record_task_status(repo/'outputs', 'briefing', result, delivery)
            automation.report(status)
            exit_code = status['exit_code']
            print(M.now_utc().isoformat(), "scheduled_entry_finished", "exit_code=" + str(exit_code), flush=True)
            enrollment = M.read_json(repo / "outputs" / "briefing" / "acceptance.json")
            # The optional clock window is no longer the primary acceptance.
            # Catch-up writes its own usage report after an actual attempt.
            if action != "idle" and enrollment.get("start_date"):
                from fxdash.operations import collect_report, save_report
                import uuid
                try:
                    report = collect_report(repo / "outputs")
                    name = M.now_utc().strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".json"
                    M.atomic_json(repo / "outputs" / "operations-acceptance" / ("usage-" + name), report["acceptance"])
                    # Reports read saved artifacts only, after the time-sensitive dispatch.
                    save_report(repo / "outputs", report)
                except Exception as exc:
                    print(M.now_utc().isoformat(), "operations_report_failed", type(exc).__name__, flush=True)
                    R.record_task_status(repo/'outputs', 'briefing', exit_code or 1, delivery)
                    return exit_code or 1
            return exit_code
        except BaseException as exc:
            # Raw provider errors and URLs must never reach task logs.
            print(datetime.now(timezone.utc).isoformat(), 'scheduled_entry_failed', type(exc).__name__, flush=True)
            R.record_task_status(repo/'outputs', 'briefing', 1, {'state':'failed'})
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
