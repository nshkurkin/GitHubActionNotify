# GH Actions Monitor

![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-0078d4?logo=windows&logoColor=white)
![Build](https://github.com/nshkurkin/GitHubActionNotify/actions/workflows/build.yml/badge.svg)
![License](https://img.shields.io/badge/license-Apache%202.0-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-yellow?logo=python&logoColor=white)

**GH Actions Monitor** is a lightweight Windows system tray app that watches your GitHub Actions workflow runs and fires desktop notifications when they start, succeed, fail, or are cancelled — so you never have to keep a browser tab open just to know when a build finishes.

- Real-time toast notifications (Windows 10/11)
- Monitors any mix of public and private repositories
- Configurable poll interval, trigger filter, and lookback window
- Runs quietly in the system tray
- Single-file EXE — no installer, no dependencies to manage

<img width="284" height="106" alt="Notification Example" src="https://github.com/user-attachments/assets/233a328f-5b65-486a-b34d-78accbfc9a3c" />

## Quick Start

1. Download `GH Actions Monitor.exe` from the [latest release](../../releases/latest)
2. Run it — a default config is created and opened in Notepad automatically
3. Right-click GH tray icon > Edit Config > Paste your GitHub PAT and set `watch = owner/repo` (or `all`)
4. Save the config. Right-click GH tray icon > Refresh config

## Requirements

- Windows 10 or 11 (toast notifications use the Windows Runtime API via `winotify`)
- A GitHub Personal Access Token (see below)

## GitHub Personal Access Token (PAT)

The app calls three GitHub REST API endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /user/repos` | Auto-discover repos when `watch = all` |
| `GET /repos/{owner}/{repo}/actions/runs` | Fetch workflow run status |
| `GET /repos/{owner}/{repo}/actions/workflows/{id}` | Resolve workflow name |

### Option A — Fine-grained PAT (recommended) (least-privilege)

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

### Option B — Classic PAT (simpler but less secure)

1. Go to **GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)**
2. Click **Generate new token (classic)**
3. Give it a descriptive name, e.g. `GH Actions Monitor`
4. Set an expiration (90 days recommended; you can regenerate when it expires)
5. Select **exactly one scope**:
   - `repo` — required for **private** repositories (includes Actions read access)
   - `public_repo` — sufficient if you only monitor **public** repositories
6. Click **Generate token** and copy the value immediately

> **Note:** The `workflow` scope is for *writing* workflow YAML files — you do **not** need it here.

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

## Adding to Windows Startup

To have the app launch automatically when you log in:

1. Press `Win+R`, type `shell:startup`, press Enter
2. Create a shortcut to `GH Actions Monitor.exe` in that folder

## Building the EXE yourself

```bash
pip install -r requirements.txt pyinstaller
cd github_actions_monitor
pyinstaller --noconsole --onefile --name "GH Actions Monitor" main.py
# Output: github_actions_monitor/dist/GH Actions Monitor.exe
```

## Logs

Log files (rotating, max 1 MB × 2 backups) are written to:

```
%LOCALAPPDATA%\GitHubActionsMonitor\github_monitor.log
```

Open them via **right-click tray icon → Logs**.

## Dev Setup

```bash
pip install -r requirements.txt
cd github_actions_monitor
python main.py
```
