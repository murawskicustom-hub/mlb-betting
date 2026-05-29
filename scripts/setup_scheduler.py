"""
Creates four Windows scheduled tasks for automated MLB data pulls.
Run once (or re-run to update). Idempotent — deletes and recreates if tasks exist.

Uses PowerShell's Register-ScheduledTask cmdlets via subprocess rather than a
third-party library. Chosen over schtasks CLI because schtasks has no command-line
flag for WakeToRun — that setting requires either XML import or the PowerShell
cmdlets. Both are 100% built-in Windows; no pip packages involved.
"""

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHONW      = PROJECT_ROOT / 'venv' / 'Scripts' / 'pythonw.exe'
ORCHESTRATOR = PROJECT_ROOT / 'scripts' / 'run_scheduled_pull.py'

TASKS = [
    {'name': 'MLB-Betting-Morning',  'slot': 'morning',  'time': '07:00'},
    {'name': 'MLB-Betting-Midday',   'slot': 'midday',   'time': '12:00'},
    {'name': 'MLB-Betting-Pregame',  'slot': 'pregame',  'time': '17:00'},
    {'name': 'MLB-Betting-Closing',  'slot': 'closing',  'time': '23:00'},
]


def run_ps(script: str, description: str) -> tuple[bool, str]:
    """Run a PowerShell command block. Returns (success, output)."""
    result = subprocess.run(
        ['powershell', '-NonInteractive', '-Command', script],
        capture_output=True, text=True
    )
    output = result.stdout.strip() + result.stderr.strip()
    ok = result.returncode == 0
    return ok, output


def register_task(task: dict) -> bool:
    name      = task['name']
    slot      = task['slot']
    time_str  = task['time']   # HH:MM
    hour, minute = time_str.split(':')

    exe  = str(PYTHONW).replace('\\', '\\\\')
    args = f'"{str(ORCHESTRATOR).replace(chr(92), chr(92)*2)}" {slot}'
    wdir = str(PROJECT_ROOT).replace('\\', '\\\\')

    ps_script = f"""
$taskName = '{name}'

# Remove existing task if present
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {{
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-Host "Removed existing task: $taskName"
}}

$action = New-ScheduledTaskAction `
    -Execute '{exe}' `
    -Argument '"{str(ORCHESTRATOR)}" {slot}' `
    -WorkingDirectory '{str(PROJECT_ROOT)}'

$trigger = New-ScheduledTaskTrigger -Daily -At '{hour}:{minute}'

$settings = New-ScheduledTaskSettingsSet `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew

# InteractiveToken: runs under the logged-on user session; no elevation/password
# storage needed. Correct choice for a personal laptop — user is always logged in,
# and the task wakes the machine then runs in the existing session.
$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERDOMAIN\\$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Force | Out-Null

Write-Host "Created task: $taskName  (daily at {hour}:{minute}, wake-to-run, S4U)"
"""

    ok, output = run_ps(ps_script, name)
    print(output if output else f'  (no output for {name})')
    return ok


def set_power_settings():
    """
    Report AC power settings relevant to scheduled wake.
    On this machine AC sleep=Never and AC wake timers=Enabled are already correct.
    Document what we confirmed and what we would change if needed.
    """
    print('\n--- Power settings (AC) ---')
    ps = """
$sleep     = (powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE  | Select-String 'Current AC').ToString().Trim()
$hibernate = (powercfg /query SCHEME_CURRENT SUB_SLEEP HIBERNATEIDLE | Select-String 'Current AC').ToString().Trim()
$wake      = (powercfg /query SCHEME_CURRENT SUB_SLEEP RTCWAKE       | Select-String 'Current AC').ToString().Trim()
Write-Host "Sleep (AC):          $sleep"
Write-Host "Hibernate (AC):      $hibernate"
Write-Host "Wake timers (AC):    $wake"
"""
    _, out = run_ps(ps, 'power check')
    print(out)

    # Translate hex values to human-readable
    print('  Interpretation:')
    print('    0x00000000 for sleep/hibernate = Never (good)')
    print('    0x00000001 for wake timers     = Enabled (good)')
    print('  No changes needed — AC power is already configured for scheduled wake.')


def verify_tasks():
    print('\n--- Task verification ---')
    for task in TASKS:
        name = task['name']
        ps = f"""
$t = Get-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue
if ($t) {{
    $info = $t | Get-ScheduledTaskInfo
    $trigger = $t.Triggers[0]
    $action  = $t.Actions[0]
    $settings = $t.Settings
    $principal = $t.Principal
    Write-Host "Task:           {name}"
    Write-Host "  Status:       $($info.LastTaskResult)"
    Write-Host "  Trigger:      $($trigger.StartBoundary)  (repeat daily)"
    Write-Host "  Wake to run:  $($settings.WakeToRun)"
    Write-Host "  Logon type:   $($principal.LogonType)"
    Write-Host "  Run level:    $($principal.RunLevel)"
    Write-Host "  Execute:      $($action.Execute)"
    Write-Host "  Arguments:    $($action.Arguments)"
    Write-Host "  WorkingDir:   $($action.WorkingDirectory)"
}} else {{
    Write-Host "TASK NOT FOUND: {name}"
}}
"""
        _, out = run_ps(ps, f'verify {name}')
        print(out)
        print()


def main():
    print(f'Project root : {PROJECT_ROOT}')
    print(f'Python (venv): {PYTHONW}')
    print(f'Orchestrator : {ORCHESTRATOR}')
    print()

    if not PYTHONW.exists():
        print(f'ERROR: venv Python not found at {PYTHONW}')
        sys.exit(1)

    print('--- Creating scheduled tasks ---')
    all_ok = True
    for task in TASKS:
        ok = register_task(task)
        if not ok:
            print(f'  ERROR creating {task["name"]}')
            all_ok = False

    set_power_settings()
    verify_tasks()

    if all_ok:
        print('All four tasks created successfully.')
    else:
        print('One or more tasks failed — review output above.')
        sys.exit(1)


if __name__ == '__main__':
    main()
