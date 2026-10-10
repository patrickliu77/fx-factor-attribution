"""Windowless entry for login and periodic late-briefing checks."""
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_delivery_runtime as R


def main(repo=None):
    repo = Path(repo) if repo is not None else Path(__file__).resolve().parents[1]
    os.chdir(repo)
    sys.path.insert(0, str(repo / "src"))
    health = R.runtime_status(repo)
    if health['state'] != 'ready':
        R.record_task_status(repo/'outputs', 'catchup', 3, {'state':'failed'})
        print('runtime_health_failed', health['state'], flush=True)
        return 3
    R.bootstrap_tools()
    destination = repo / "outputs" / "logs" / "catchup.log"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8", buffering=1) as stream, redirect_stdout(stream), redirect_stderr(stream):
        try:
            from fxdash.scheduled_environment import refresh
            observed = refresh('catchup')
            print('scheduled_environment', observed['state'], flush=True)
            from fxdash.narrative import catchup as C, morning as M
            if not C.due(M.now_utc()):
                status = R.record_task_status(repo/'outputs', 'catchup', 0, {'state':'idle'})
                print('catchup_entry_idle', status['state'], flush=True)
                return 0
            print(M.now_utc().isoformat(), "catchup_entry_started", flush=True)
            from fxdash.narrative import automation
            gate = automation.before('catchup', repo/'outputs')
            if not gate['proceed']:
                automation.report(gate)
                status = R.record_task_status(repo/'outputs', 'catchup', 0, {'state':gate['state']})
                automation.report(status)
                return status['exit_code']
            result = C.main(["--scheduled-task"])
            delivery = automation.after(repo/'outputs')
            automation.report(delivery)
            status = R.record_task_status(repo/'outputs', 'catchup', result, delivery)
            automation.report(status)
            exit_code = status['exit_code']
            print(M.now_utc().isoformat(), "catchup_entry_finished", "exit_code=" + str(exit_code), flush=True)
            day = M.local_time(M.now_utc()).date().isoformat()
            state = M.read_json(repo / "outputs" / "briefing" / "catchup" / day / "status.json").get("state")
            latest = repo / "outputs" / "operations-acceptance" / "reports" / "latest.html"
            if state != "already_available" or not latest.exists():
                try:
                    from fxdash.operations import collect_report, save_report
                    save_report(repo / "outputs", collect_report(repo / "outputs"))
                except Exception as exc:
                    print(M.now_utc().isoformat(), "usage_report_failed", type(exc).__name__, flush=True)
                    R.record_task_status(repo/'outputs', 'catchup', exit_code or 1, delivery)
                    return exit_code or 1
            return exit_code
        except BaseException as exc:
            # Never log provider exceptions containing URLs or credentials.
            print(R.utc_now().isoformat(), "catchup_entry_failed", type(exc).__name__, flush=True)
            R.record_task_status(repo/'outputs', 'catchup', 1, {'state':'failed'})
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
