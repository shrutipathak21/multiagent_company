# Running this as an always-on service (Windows)

By default, `webapp.py` uses Flask's dev server — it stops the moment
you close that PowerShell window, and it isn't built to run
unattended for long periods. This doc gets you to something that:

- keeps running after you close the terminal
- restarts automatically if it crashes
- starts automatically when you log in (optional)

## 1. Switch to a production server

```powershell
pip install waitress
```

Use `serve.py` (included) instead of `webapp.py` directly — same app,
served by `waitress`, a stable production WSGI server that runs
indefinitely (Flask's dev server explicitly warns against this).

Test it once manually first:

```powershell
cd C:\Users\shrpa\Downloads\company\company
$env:GROQ_API_KEY="your_key_here"
python3 serve.py
```

Confirm http://localhost:5000 still works, then Ctrl+C to stop it —
that just proved the production server itself is fine. Now make it
persistent.

## 2. Register it as a Windows Scheduled Task

This runs `serve.py` in the background, restarts it if it dies, and
(optionally) starts it automatically at login — without needing to
install anything extra like NSSM.

Open PowerShell **as Administrator** and run:

```powershell
$action = New-ScheduledTaskAction -Execute "pythonw.exe" `
    -Argument "serve.py" `
    -WorkingDirectory "C:\Users\shrpa\Downloads\company\company"

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -AllowStartIfOnBattery -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName "AICompanySimulator" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "AI Software Company Simulator dashboard"
```

Notes:
- `pythonw.exe` (not `python3.exe`) runs with no visible console window.
  It's normally next to your `python3.exe` — find it with
  `where.exe pythonw` if the task fails to start.
- The `GROQ_API_KEY` environment variable won't carry over to a
  scheduled task automatically. Set it as a **permanent** user
  environment variable instead, one time:
  ```powershell
  [System.Environment]::SetEnvironmentVariable("GROQ_API_KEY", "your_key_here", "User")
  ```
  Then restart your terminal (or just log out/in) so it takes effect,
  and re-register the task.
- `ExecutionTimeLimit 0` means "never time out" — without this, Windows
  kills scheduled tasks after 72 hours by default.

## 3. Start it right now (without waiting for next login)

```powershell
Start-ScheduledTask -TaskName "AICompanySimulator"
```

Check it's actually running:

```powershell
Get-ScheduledTask -TaskName "AICompanySimulator" | Get-ScheduledTaskInfo
```

`LastTaskResult` should be `0`. Then open http://localhost:5000 —
it should load exactly like before, just without a terminal window
attached to it.

## 4. Stopping / removing it

```powershell
Stop-ScheduledTask -TaskName "AICompanySimulator"
Unregister-ScheduledTask -TaskName "AICompanySimulator" -Confirm:$false
```

## Honest limitations of this setup

- This makes it *persistent on your machine*, not *publicly reachable*.
  It's still bound to `localhost:5000` — only reachable from your own
  computer (or your local network, via the `192.168.x.x` address Flask
  printed earlier). Anyone outside your network still can't hit it.
- If your machine sleeps, restarts, or you're not logged in, it's not
  running — "always on" here means "survives you closing the terminal
  and reboots," not "runs on a server somewhere 24/7."
- If you actually want it reachable from anywhere (not just your own
  machine), that's a real hosting step — e.g. deploying to a small
  cloud VM or a platform like Render/Railway — and is a materially
  different, separate task from what's set up here.
