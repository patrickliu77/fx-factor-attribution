"""Windowless entry for login and periodic late-briefing checks."""
import os
import sys
from pathlib import Path


def main():
    repo = Path(__file__).resolve().parents[1]
    os.chdir(repo)
    sys.path.insert(0, str(repo / "src"))
    from fxdash.narrative import catchup as C, morning as M
    if not C.due(M.now_utc()):
        return 0
    destination = repo / "outputs" / "logs" / "catchup.log"
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("a", encoding="utf-8", buffering=1) as stream:
        sys.stdout = sys.stderr = stream
        print(M.now_utc().isoformat(), "catchup_entry_started", flush=True)
        try:
            result = C.main(["--scheduled-task"])
            print(M.now_utc().isoformat(), "catchup_entry_finished", "exit_code=" + str(result), flush=True)
            return result
        except BaseException as exc:
            # Never log provider exceptions containing URLs or credentials.
            print(M.now_utc().isoformat(), "catchup_entry_failed", type(exc).__name__, flush=True)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
