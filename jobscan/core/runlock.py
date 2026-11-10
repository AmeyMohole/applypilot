"""A lock file that keeps drafting out of the way while send.py is sending.

send.py builds its queue from the drafts that exist when it starts and records each send as it
goes. Drafting during a long send used to risk the new draft being unknown to the sender. Now
scan.py --drafts and followup.py --drafts check this lock and stop. A lock left by a crashed run is
detected (its process is gone) and removed.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import os

import config


def _lock_path():
    return config.data_path("send.lock")


def _alive(pid: int) -> bool:
    if os.name == "nt":
        # os.kill(pid, 0) on Windows terminates the process instead of probing it.
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def held() -> dict | None:
    """The live lock's details, or None."""
    path = _lock_path()
    try:
        info = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if info.get("pid") and _alive(int(info["pid"])):
        return info
    with contextlib.suppress(OSError):
        path.unlink()
    return None


@contextlib.contextmanager
def sending(what: str = "send.py"):
    other = held()
    if other:
        raise RuntimeError(f"another send is running: {other.get('what')} since {other.get('since')}")
    path = _lock_path()
    path.write_text(json.dumps({"pid": os.getpid(), "what": what,
                                "since": dt.datetime.now().strftime("%H:%M:%S")}), encoding="utf-8")
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            path.unlink()


def refuse_if_sending(what: str) -> bool:
    """Print why and return True when a send is in progress."""
    info = held()
    if info:
        print(f"{what}: send.py is sending right now ({info.get('what')}, since {info.get('since')}). "
              "Wait for it to finish, then run this again.")
        return True
    return False
