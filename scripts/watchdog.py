"""
MLB-Betting-Watchdog — runs daily at 06:45 ET.

Checks that all four MLB-Betting scheduled tasks still exist in Windows
Task Scheduler. If any are missing (e.g., wiped by a Windows Update
reboot), re-runs setup_scheduler.py to recreate them and writes a loud
WATCHDOG log entry.

The watchdog task itself can be killed by the same update wipe; if that
happens a single survivor re-seeds the rest on the next run. Five tasks
all vanishing without any watchdog log entry is a definitive signal of a
system-level wipe (not a per-task deletion).
"""

import subprocess
import sys
import logging
from datetime import datetime
from pathlib import Path

import pytz

EASTERN      = pytz.timezone('US/Eastern')
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR  = PROJECT_ROOT / 'scripts'
LOGS_DIR     = PROJECT_ROOT / 'logs' / 'scheduled'
VENV_PYTHON  = PROJECT_ROOT / 'venv' / 'Scripts' / 'python.exe'

MONITORED_TASKS = [
    'MLB-Betting-Morning',
    'MLB-Betting-Midday',
    'MLB-Betting-Pregame',
    'MLB-Betting-Closing',
]


def setup_logger() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    now_et   = datetime.now(EASTERN)
    date_str = now_et.strftime('%Y-%m-%d')
    log_path = LOGS_DIR / f'{date_str}_watchdog.log'

    logger = logging.getLogger('watchdog')
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        '%(asctime)s  %(levelname)-8s  %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    for handler in [logging.StreamHandler(), logging.FileHandler(log_path, encoding='utf-8')]:
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    return logger


def task_exists(name: str) -> bool:
    """Return True if the named scheduled task exists in Task Scheduler."""
    result = subprocess.run(
        ['schtasks', '/Query', '/TN', name],
        capture_output=True, text=True
    )
    return result.returncode == 0


def reregister_tasks(log: logging.Logger) -> bool:
    """Re-run setup_scheduler.py to recreate all four tasks. Returns True on success."""
    cmd = [str(VENV_PYTHON), str(SCRIPTS_DIR / 'setup_scheduler.py')]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                cwd=str(PROJECT_ROOT))
        for line in result.stdout.splitlines():
            if line.strip():
                log.info(f'  setup_scheduler: {line}')
        for line in result.stderr.splitlines():
            if line.strip():
                log.warning(f'  setup_scheduler: {line}')
        return result.returncode == 0
    except Exception as e:
        log.error(f'  setup_scheduler raised: {e}')
        return False


def main():
    log = setup_logger()
    now_et = datetime.now(EASTERN)

    log.info('=' * 60)
    log.info(f'WATCHDOG check  |  {now_et.strftime("%Y-%m-%d %H:%M:%S %Z")}')

    missing = [t for t in MONITORED_TASKS if not task_exists(t)]
    present = [t for t in MONITORED_TASKS if t not in missing]

    log.info(f'Tasks present:  {len(present)}/{len(MONITORED_TASKS)}  '
             f'({", ".join(present) if present else "none"})')

    if missing:
        log.warning(
            f'WATCHDOG: tasks missing, re-registering. '
            f'Missing: {", ".join(missing)}'
        )
        ok = reregister_tasks(log)
        if ok:
            log.warning('WATCHDOG: setup_scheduler.py completed successfully — tasks restored.')
        else:
            log.error('WATCHDOG: setup_scheduler.py FAILED — manual intervention required.')
            sys.exit(1)
    else:
        log.info('All four tasks present — no action needed.')

    log.info('=' * 60)
    sys.exit(0)


if __name__ == '__main__':
    main()
