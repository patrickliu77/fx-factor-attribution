"""Refresh narrowly owned Windows user settings at scheduled worker startup.

Only scheduled entries call this module. Interactive commands and cloud workers
continue to use their explicit process environment. No values or registry errors
are returned, logged, expanded, or written back to the registry.
"""
from __future__ import annotations

import os
import re
import sys


SPEECH_NAMES = ("FXDASH_AUDIO", "AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION")
NAMES = ("FRED_API_KEY", "BANXICO_TOKEN", "GEMINI_API_KEY", "BREVO_API_KEY", *SPEECH_NAMES)
ENTRY_NAMES = {
    "live": ("FRED_API_KEY", "BANXICO_TOKEN"),
    "narrative": ("GEMINI_API_KEY",),
    "briefing": ("GEMINI_API_KEY", "BREVO_API_KEY", *SPEECH_NAMES),
    "catchup": ("GEMINI_API_KEY", "BREVO_API_KEY", *SPEECH_NAMES),
}


def _read_user_environment(names):
    if not names or len(set(names)) != len(names) or set(names) - set(NAMES):
        raise ValueError("invalid_scheduled_environment_scope")
    if sys.platform != "win32":
        return {}
    import winreg
    try:
        settings = winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ)
    except FileNotFoundError:
        return {}
    values = {}
    with settings:
        for name in names:
            try:
                value, kind = winreg.QueryValueEx(settings, name)
            except FileNotFoundError:
                continue
            # Accept the setup helper's literal strings only. REG_EXPAND_SZ and
            # unrelated values cannot import other environment variables.
            values[name] = value if kind == winreg.REG_SZ and isinstance(value, str) else None
    return values


def _credential(value, limit=512):
    return (isinstance(value, str) and 0 < len(value) <= limit and bool(value.strip("*"))
            and all(33 <= ord(character) <= 126 for character in value))


def refresh(entry, *, environment=None):
    """Replace or remove only this entry's settings; return safe presence flags."""
    if entry not in ENTRY_NAMES:
        raise ValueError("invalid_scheduled_environment_entry")
    if sys.platform != "win32":
        return {"state": "unsupported_host"}
    environment = os.environ if environment is None else environment
    names = ENTRY_NAMES[entry]
    state = "refreshed"
    try:
        saved = _read_user_environment(names)
    except OSError:
        saved = {}
        state = "user_environment_unavailable"
    # Deleted, non-literal, malformed, and unreadable settings cannot retain an
    # older inherited key. Environment variables outside this scope are untouched.
    for name in names:
        if name in SPEECH_NAMES:
            continue
        value = saved.get(name)
        if _credential(value):
            environment[name] = value
        else:
            environment.pop(name, None)
    result = {"state": state}
    if "FXDASH_AUDIO" in names:
        value = saved.get("FXDASH_AUDIO")
        mode = value.strip().lower() if isinstance(value, str) else "off"
        mode = mode if mode in {"off", "windows", "azure"} else "off"
        key, region = saved.get("AZURE_SPEECH_KEY"), saved.get("AZURE_SPEECH_REGION")
        region = region.strip().lower() if isinstance(region, str) else ""
        if mode == "azure" and (not _credential(key, 256) or not re.fullmatch(r"[a-z][a-z0-9]{2,39}", region)):
            mode = "off"
        environment["FXDASH_AUDIO"] = mode
        if mode == "azure":
            environment["AZURE_SPEECH_KEY"] = key
            environment["AZURE_SPEECH_REGION"] = region
        else:
            for name in SPEECH_NAMES[1:]:
                environment.pop(name, None)
        result["backend"] = mode
    result["credentials"] = {name: name in environment for name in names if name != "FXDASH_AUDIO"}
    return result
