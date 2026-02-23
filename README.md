# YouTube Account Migration Tool

Moves your YouTube data from one account to another using browser automation (Playwright). No API keys or quotas needed.

Supports:
- **Subscriptions** — channels you're subscribed to
- **Liked Videos** — videos you've liked
- **Watch Later** — your Watch Later playlist
- **Custom Playlists** — all your playlists and their videos

## Requirements

- Python 3.10+
- Playwright

## Setup

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

## Usage

### 1. Log in to both accounts

```bash
python migrate.py login-old
```

A browser opens — log in to your **old** YouTube account, then close the browser.

```bash
python migrate.py login-new
```

Same thing, but log in to your **new** account.

Your logins are saved in the `profiles/` folder so you only need to do this once.

### 2. Export data

```bash
python migrate.py export subs          # Subscriptions
python migrate.py export likes         # Liked videos
python migrate.py export watch-later   # Watch Later playlist
python migrate.py export playlists     # All custom playlists
python migrate.py export all           # Everything at once
```

Exported data is saved to text files you can review and edit before importing.

### 3. Import data

```bash
python migrate.py import subs          # Subscribe to channels
python migrate.py import likes         # Like videos
python migrate.py import watch-later   # Add to Watch Later
python migrate.py import playlists     # Create playlists and add videos
python migrate.py import all           # Everything at once
```

Random delays are added between actions to avoid bot detection. Already-done items are automatically skipped, so re-running is safe.

### 4. Handle failures

If any actions fail, they're saved to `*_failed.txt` files. To retry, copy the failed file over the original and re-run import.

## Data files

| File | Contents |
|---|---|
| `channels.txt` | Subscribed channel URLs |
| `likes.txt` | Liked video URLs |
| `watch_later.txt` | Watch Later video URLs |
| `playlists/<name>.txt` | One file per playlist with video URLs |

## Switching accounts again later

Delete the `profiles/` folder to clear saved logins, then start from step 1.

## How it works

- Uses Playwright to automate a real Chromium browser
- Persistent browser profiles store your login sessions
- Export scrolls through YouTube pages and scrapes all URLs
- Import visits each URL and clicks the relevant button (Subscribe, Like, Save)
- Random delays (3-7s between actions, 30-60s break every 20) to avoid detection
- Ctrl+C during import saves progress — re-running skips already-done items
