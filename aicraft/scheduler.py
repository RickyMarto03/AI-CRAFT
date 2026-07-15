"""Utility per automatizzare job locali ricorrenti.

Per ora usiamo launchd (macOS) per il sync settimanale della libreria:
non tiene un processo sempre acceso e lancia lo stesso CLI che useremmo a
mano, quindi resta facile da debuggare.
"""

from __future__ import annotations

import plistlib
import sys
from pathlib import Path

from . import config


DEFAULT_LABEL = "com.aicraft.weekly-reference-sync"
TRACKING_LABEL = "com.aicraft.daily-profile-tracking"


def weekly_sync_command(*, project_dir: Path | None = None, python_bin: str | None = None) -> list[str]:
    project_dir = project_dir or config.BASE_DIR
    python = python_bin or sys.executable
    return [python, "-m", "aicraft.cli", "references", "sync-policy"]


def daily_tracking_command(*, project_dir: Path | None = None, python_bin: str | None = None) -> list[str]:
    project_dir = project_dir or config.BASE_DIR
    python = python_bin or sys.executable
    return [python, "-m", "aicraft.cli", "tracking", "sync"]


def launchd_plist(
    *,
    label: str = DEFAULT_LABEL,
    weekday: int = 2,
    hour: int = 9,
    minute: int = 0,
    project_dir: Path | None = None,
    python_bin: str | None = None,
) -> dict:
    """Ritorna il plist launchd per il sync settimanale.

    launchd usa Weekday 1=domenica, 2=lunedi', ... 7=sabato. Il default e'
    lunedi' alle 09:00, cioe' weekday=2.
    """
    project_dir = project_dir or config.BASE_DIR
    log_dir = project_dir / "data" / "logs"
    return {
        "Label": label,
        "ProgramArguments": weekly_sync_command(project_dir=project_dir, python_bin=python_bin),
        "WorkingDirectory": str(project_dir),
        "StartCalendarInterval": {"Weekday": weekday, "Hour": hour, "Minute": minute},
        "StandardOutPath": str(log_dir / "weekly-reference-sync.out.log"),
        "StandardErrorPath": str(log_dir / "weekly-reference-sync.err.log"),
        "RunAtLoad": False,
    }


def daily_tracking_plist(
    *,
    label: str = TRACKING_LABEL,
    hour: int = 8,
    minute: int = 30,
    project_dir: Path | None = None,
    python_bin: str | None = None,
) -> dict:
    project_dir = project_dir or config.BASE_DIR
    log_dir = project_dir / "data" / "logs"
    return {
        "Label": label,
        "ProgramArguments": daily_tracking_command(project_dir=project_dir, python_bin=python_bin),
        "WorkingDirectory": str(project_dir),
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "StandardOutPath": str(log_dir / "daily-profile-tracking.out.log"),
        "StandardErrorPath": str(log_dir / "daily-profile-tracking.err.log"),
        "RunAtLoad": False,
    }


def plist_bytes(plist: dict) -> bytes:
    return plistlib.dumps(plist, sort_keys=False)


def install_weekly_sync(
    *,
    label: str = DEFAULT_LABEL,
    weekday: int = 2,
    hour: int = 9,
    minute: int = 0,
    project_dir: Path | None = None,
    python_bin: str | None = None,
    launch_agents_dir: Path | None = None,
) -> Path:
    project_dir = project_dir or config.BASE_DIR
    (project_dir / "data" / "logs").mkdir(parents=True, exist_ok=True)
    target_dir = launch_agents_dir or (Path.home() / "Library" / "LaunchAgents")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{label}.plist"
    data = plist_bytes(
        launchd_plist(
            label=label,
            weekday=weekday,
            hour=hour,
            minute=minute,
            project_dir=project_dir,
            python_bin=python_bin,
        )
    )
    target.write_bytes(data)
    return target


def install_daily_tracking(
    *,
    label: str = TRACKING_LABEL,
    hour: int = 8,
    minute: int = 30,
    project_dir: Path | None = None,
    python_bin: str | None = None,
    launch_agents_dir: Path | None = None,
) -> Path:
    project_dir = project_dir or config.BASE_DIR
    (project_dir / "data" / "logs").mkdir(parents=True, exist_ok=True)
    target_dir = launch_agents_dir or (Path.home() / "Library" / "LaunchAgents")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{label}.plist"
    target.write_bytes(plist_bytes(daily_tracking_plist(label=label, hour=hour, minute=minute, project_dir=project_dir, python_bin=python_bin)))
    return target
