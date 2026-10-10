"""Refresh only persisted speech environment variables in scheduled workers.

Windows user environment settings can change after the scheduler starts. Reading
these three values at worker startup avoids depending on a new desktop login.
No registry writes, enumeration, subprocesses, network calls or secret logging.
Interactive CLI invocations continue to use their own process environment.
"""
from __future__ import annotations

import os

from .. import scheduled_environment

NAMES = ("FXDASH_AUDIO", "AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION")


def _read_user_environment():
    return scheduled_environment._read_user_environment(NAMES)


def refresh_user_speech_environment():
    try:
        values = _read_user_environment()
    except OSError:
        os.environ["FXDASH_AUDIO"] = "off"
        for name in NAMES[1:]:
            os.environ.pop(name, None)
        return {"state": "user_environment_unavailable", "backend": "off"}
    if "FXDASH_AUDIO" not in values:
        # Saving a key for an audition alone must not enable cloud synthesis.
        return {"state": "unchanged"}
    mode = values["FXDASH_AUDIO"]
    mode = mode.strip().lower() if isinstance(mode, str) else "off"
    mode = mode if mode in {"off", "windows", "azure"} else "off"
    os.environ["FXDASH_AUDIO"] = mode
    for name in NAMES[1:]:
        value = values.get(name)
        if mode == "azure" and isinstance(value, str) and "\0" not in value and len(value) <= 256:
            os.environ[name] = value
        else:
            # Never retain a deleted/replaced key from an older process.
            os.environ.pop(name, None)
    return {"state": "refreshed", "backend": mode}
