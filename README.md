# GH Actions Monitor

A lightweight Windows system tray app that watches your GitHub Actions workflow
runs and fires desktop notifications when they start, succeed, fail, or are
cancelled.

---

## Requirements

- Windows 10 or 11 (the notification system relies on Windows toast notifications)
- A GitHub Personal Access Token (see below)

---

## GitHub Personal Access Token (PAT)

The app calls three GitHub REST API endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /user/repos` | Auto-discover repos when `watch = all` |
| `GET /repos/{owner}/{repo}/actions/runs` | Fetch workflow run status |
| `GET /repos/{owner}/{repo}/actions/workflows/{id}` | Resolve workflow name |

### Option A — Classic PAT (simpler)

1. Go to **GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)**
2. Click **Generate new token (classic)**
3. Give it a descriptive name, e.g. `GH Actions Monitor`
4. Set an expiration (90 days recommended; you can regenerate when it expires)
5. Select **exactly one scope**:
   - `repo` — required for **private** repositories (includes Actions read access)
   - `public_repo` — sufficient if you only monitor **public** repositories
6. Click **Generate token** and copy the value immediately

> **Note:** The `workflow` scope is for *writing* workflow YAML files — you do **not** need it here.

### Option B — Fine-grained PAT (least-privilege)

1. Go to **GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens**
2. Click **Generate new token**
3. Give it a name and expiration
4. Under **Repository access**, choose either:
   - **All repositories**, or
   - **Only select repositories** — pick exactly the repos you want to monitor
5. Under **Repository permissions**, grant:
   - `Actions` → **Read-only**
   - `Metadata` → **Read-only** (auto-selected; required for all repo access)
6. No account permissions are needed
7. Click **Generate token** and copy the value

---

## Setup

### Running the pre-built EXE

1. Download `GH Actions Monitor.exe` from the [latest GitHub Release](../../releases/latest)
   (or from the **Artifacts** section of a recent Actions run)
2. Run it once — it will create a default config file and open it in Notepad
3. Edit the config with your token and repos (see [Configuration](#configuration))
4. The tray icon appears in the system tray (bottom-right of taskbar)

### Running from source

```bash
pip install -r requirements.txt
cd github_actions_monitor
python main.py
```

---

## Configuration

The config file is created automatically on first run at:

```
%LOCALAPPDATA%\GitHubActionsMonitor\config.ini
```

You can also open it any time via **right-click tray icon → Edit Config**.

```ini
[github]
; Your Personal Access Token (classic: repo scope; fine-grained: Actions=Read + Metadata=Read)
token = ghp_xxxxxxxxxxxxxxxxxxxx
; Your GitHub username (used for repo auto-discovery)
username = your-github-username

[repos]
; Comma-separated list of owner/repo pairs, or "all" to watch all your repos
watch = owner/repo1, owner/repo2

[settings]
; How often to poll GitHub, in seconds (minimum 10)
poll_interval_seconds = 30
; Only notify for workflows triggered by a specific event, or "all"
; Valid values: push, pull_request, workflow_dispatch, schedule, all
trigger_filter = all
; On startup, ignore runs older than this many minutes (avoids a flood of old notifications)
lookback_minutes = 60
```

---

## Adding to Windows Startup

To have the app launch automatically when you log in:

1. Press `Win+R`, type `shell:startup`, press Enter
2. Create a shortcut to `GH Actions Monitor.exe` in that folder

---

## Building the EXE yourself

### Locally

```bash
pip install -r requirements.txt pyinstaller
cd github_actions_monitor
pyinstaller --noconsole --onefile --name "GH Actions Monitor" main.py
# Output: github_actions_monitor/dist/GH Actions Monitor.exe
```

### Via GitHub Actions (CI)

Every push to `master`/`main` triggers the **Build Windows EXE** workflow which:

1. Runs PyInstaller on a `windows-latest` runner
2. Saves the result as a downloadable artifact (30-day retention)

To download:
- Go to **Actions** → click the latest run → scroll to **Artifacts** → download `GH-Actions-Monitor-<sha>`

To publish a release, push a version tag:

```bash
git tag v1.0.0
git push origin v1.0.0
```

This triggers the same build and additionally creates a **GitHub Release** with the EXE attached.

---

## Logs

Log files (rotating, max 1 MB × 2 backups) are written to:

```
%LOCALAPPDATA%\GitHubActionsMonitor\github_monitor.log
```

Open them via **right-click tray icon → Logs**.
