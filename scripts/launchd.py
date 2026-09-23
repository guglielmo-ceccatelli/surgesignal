"""Run SurgeSignal as macOS background services (launchd user agents).

    python -m scripts.launchd install      # write plists to ~/Library/LaunchAgents and start them
    python -m scripts.launchd uninstall    # stop and remove them
    python -m scripts.launchd status       # loaded? running? last exit code

    com.surgesignal.logger    KeepAlive: restarted on crash; starts at login   (eng review 1A)
    com.surgesignal.alerter   KeepAlive: restarted on crash; starts at login
    com.surgesignal.watchdog  every 5 min: restarts hung processes, pings the builder

launchd can't run anything while the Mac is asleep. To stay awake on the charger, run
`caffeinate -s` in a terminal, or use System Settings → Battery → Options.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
JOBS = {
    "logger": {"module": "workers.logger", "keep_alive": True},
    "alerter": {"module": "workers.alerter", "keep_alive": True},
    "watchdog": {"module": "workers.watchdog", "keep_alive": False, "interval_s": 300},
}


def label(name: str) -> str:
    return f"com.surgesignal.{name}"


def plist_for(name: str, repo: Path = REPO) -> dict[str, Any]:
    job = JOBS[name]
    logs = repo / "data" / "logs"
    plist: dict[str, Any] = {
        "Label": label(name),
        "ProgramArguments": [str(repo / ".venv" / "bin" / "python"), "-m", job["module"]],
        "WorkingDirectory": str(repo),
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
        "StandardOutPath": str(logs / f"{name}.log"),
        "StandardErrorPath": str(logs / f"{name}.log"),
        "RunAtLoad": True,
        "ProcessType": "Background",
    }
    if job["keep_alive"]:
        plist["KeepAlive"] = True
        plist["ThrottleInterval"] = 30  # a crash loop restarts at most every 30 s
    else:
        plist["StartInterval"] = job["interval_s"]
    return plist


def _launchctl(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def install() -> int:
    python = REPO / ".venv" / "bin" / "python"
    if not python.exists():
        print(f"missing {python}: create the venv first (see README)", file=sys.stderr)
        return 1
    (REPO / "data" / "logs").mkdir(parents=True, exist_ok=True)
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    for name in JOBS:
        path = AGENTS_DIR / f"{label(name)}.plist"
        _launchctl("bootout", f"{domain}/{label(name)}")  # replace any older version; fine if absent
        path.write_bytes(plistlib.dumps(plist_for(name)))
        result = _launchctl("bootstrap", domain, str(path))
        ok = result.returncode == 0
        print(f"{'✓' if ok else '✗'} {label(name)}" + ("" if ok else f": {result.stderr.strip()}"))
        if not ok:
            return 1
    print(f"logs: {REPO / 'data' / 'logs'}")
    return 0


def uninstall() -> int:
    domain = f"gui/{os.getuid()}"
    for name in JOBS:
        _launchctl("bootout", f"{domain}/{label(name)}")
        (AGENTS_DIR / f"{label(name)}.plist").unlink(missing_ok=True)
        print(f"removed {label(name)}")
    return 0


def status() -> int:
    for name in JOBS:
        result = _launchctl("print", f"gui/{os.getuid()}/{label(name)}")
        if result.returncode != 0:
            print(f"{label(name)}: not loaded")
            continue
        fields = dict(
            line.strip().split(" = ", 1) for line in result.stdout.splitlines()
            if " = " in line and line.strip().split(" = ", 1)[0] in ("state", "pid", "last exit code", "runs")
        )
        print(f"{label(name)}: " + ", ".join(f"{k} {v}" for k, v in fields.items()))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("install", "uninstall", "status"))
    cmd = parser.parse_args(argv).command
    return {"install": install, "uninstall": uninstall, "status": status}[cmd]()


if __name__ == "__main__":
    sys.exit(main())
