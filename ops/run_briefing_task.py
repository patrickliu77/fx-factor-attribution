"""Windowless Windows Task Scheduler entry point, launched with pythonw.exe."""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def main():
    repo = Path(__file__).resolve().parents[1]
    os.chdir(repo)
    sys.path.insert(0, str(repo / "src"))
    destination = repo / "outputs" / "logs" / "briefing.log"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8", buffering=1) as stream:
        sys.stdout = sys.stderr = stream
        print(datetime.now(timezone.utc).isoformat(), "scheduled_entry_started", flush=True)
        try:
            from fxdash.narrative.morning_dispatch import main as dispatch
            from fxdash.narrative import morning as M
            action = M.slot(M.now_utc())
            result = dispatch(["--scheduled-task"])
            print(M.now_utc().isoformat(), "scheduled_entry_finished", "exit_code=" + str(result), flush=True)
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
                    return result or 1
            return result
        except BaseException:
            import traceback
            traceback.print_exc()
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
