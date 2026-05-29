"""
Orchestrator for scheduled MLB data pulls.
Usage: python run_scheduled_pull.py <slot>
Slots: morning | midday | pregame | closing

This script is called directly by the venv's Python interpreter (the scheduled
task points to venv/Scripts/pythonw.exe), so venv activation is not needed —
the right interpreter IS the activated environment.
"""

import sys
import os
import subprocess
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytz

EASTERN     = pytz.timezone('US/Eastern')
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR  = PROJECT_ROOT / 'scripts'
LOGS_DIR     = PROJECT_ROOT / 'logs' / 'scheduled'
VENV_PYTHON  = PROJECT_ROOT / 'venv' / 'Scripts' / 'python.exe'

# Each slot: list of (script_filename, extra_args_list)
# Empty extra_args means "use the script's own default"
def build_slots():
    now_et    = datetime.now(EASTERN)
    today     = now_et.strftime('%Y-%m-%d')
    yesterday = (now_et - timedelta(days=1)).strftime('%Y-%m-%d')

    return {
        'morning': [
            ('pull_schedule.py', [today]),
            ('pull_results.py',  [yesterday]),
            ('pull_pitchers.py', [today]),
            ('pull_odds.py',     []),
        ],
        'midday': [
            ('pull_pitchers.py', [today]),
            ('pull_odds.py',     []),
        ],
        'pregame': [
            ('pull_pitchers.py', [today]),
            ('pull_odds.py',     []),
        ],
        'closing': [
            ('pull_odds.py',     []),
            ('pull_results.py',  [today]),
        ],
    }


def setup_slot_logger(slot: str, date_str: str) -> logging.Logger:
    """Return a logger that writes to both console and the slot-specific log file."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    log_path = LOGS_DIR / f'{date_str}_{slot}.log'
    logger   = logging.getLogger(f'orchestrator.{slot}')
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    fmt = logging.Formatter(
        '%(asctime)s  %(levelname)-8s  %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    fh = logging.FileHandler(log_path, encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    return logger


def run_script(script: str, args: list, log: logging.Logger) -> bool:
    """
    Run a sub-script with the venv Python. Returns True on success, False on failure.
    stdout/stderr are captured and written to our log.
    """
    cmd = [str(VENV_PYTHON), str(SCRIPTS_DIR / script)] + args
    log.info(f'  → Running: {script} {" ".join(args) if args else "(default args)"}')

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(PROJECT_ROOT),   # working dir = project root so .env / data/ resolve
        )

        # Echo captured output into our log
        for line in result.stdout.splitlines():
            if line.strip():
                log.info(f'    {line}')
        for line in result.stderr.splitlines():
            if line.strip():
                log.info(f'    {line}')   # sub-script logs go to stderr via logging

        if result.returncode == 0:
            log.info(f'  ✓ {script} succeeded')
            return True
        else:
            log.error(f'  ✗ {script} exited with code {result.returncode}')
            return False

    except Exception as e:
        log.error(f'  ✗ {script} raised an exception: {e}')
        return False


def main():
    valid_slots = ('morning', 'midday', 'pregame', 'closing')

    if len(sys.argv) != 2 or sys.argv[1] not in valid_slots:
        print(f'Usage: python run_scheduled_pull.py <slot>')
        print(f'Valid slots: {", ".join(valid_slots)}')
        sys.exit(1)

    slot     = sys.argv[1]
    now_et   = datetime.now(EASTERN)
    date_str = now_et.strftime('%Y-%m-%d')
    log      = setup_slot_logger(slot, date_str)

    slots  = build_slots()
    tasks  = slots[slot]

    log.info('=' * 60)
    log.info(f'MLB pull — slot: {slot.upper()}  |  {now_et.strftime("%Y-%m-%d %H:%M:%S %Z")}')
    log.info(f'Running {len(tasks)} sub-script(s): {", ".join(t[0] for t in tasks)}')
    log.info('=' * 60)

    results = {}
    for script, args in tasks:
        success = run_script(script, args, log)
        results[script] = success

    # Summary
    log.info('-' * 60)
    all_ok = all(results.values())
    for script, ok in results.items():
        log.info(f'  {"OK  " if ok else "FAIL"} {script}')
    elapsed = (datetime.now(EASTERN) - now_et).total_seconds()
    log.info(f'Slot {slot.upper()} finished in {elapsed:.1f}s — {"ALL OK" if all_ok else "SOME FAILURES"}')
    log.info('=' * 60)

    sys.exit(0 if all_ok else 1)


if __name__ == '__main__':
    main()
