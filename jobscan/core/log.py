"""Logging for every command: one line per event to the console and to a rotating file
(data/logs/jobscan.log, 5 x 1 MB), so a problem in an unattended run can be read the next day.

    JOBSCAN_LOG_LEVEL=DEBUG python 3-track/mail_sync.py
"""
from __future__ import annotations

import logging
import logging.handlers
import os

import config

_configured = False


def setup(level: str | None = None) -> logging.Logger:
    """Idempotent. Returns the 'jobscan' logger. Never raises: logging must not break a command."""
    global _configured
    logger = logging.getLogger("jobscan")
    if _configured:
        return logger
    _configured = True
    logger.setLevel(getattr(logging, (level or os.environ.get("JOBSCAN_LOG_LEVEL") or "INFO").upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    try:
        folder = config.data_path("logs")
        folder.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(folder / "jobscan.log", maxBytes=1_000_000, backupCount=5,
                                                       encoding="utf-8")
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    except OSError:
        pass                                   # read-only disk: the console still works
    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)          # the commands print their own progress; show only problems
    console.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(console)
    return logger
