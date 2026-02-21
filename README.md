# YouTube Subscription Migration Tool

Moves your YouTube subscriptions from one account to another using browser automation (Playwright). No API keys or quotas needed.

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

### 2. Export subscriptions

```bash
python migrate.py export
```

Opens the old account's browser, scrolls through your subscriptions page, and saves every channel URL to `channels.txt`.

### 3. (Optional) Review channels.txt

Open `channels.txt` and remove any channels you don't want to bring over.

### 4. Import subscriptions

```bash
python migrate.py import
```

Opens the new account's browser, visits each channel one by one, and clicks Subscribe. Random delays are added between subscribes to avoid bot detection.

### 5. Handle failures

If any subscribes fail, they're saved to `channels_failed.txt`. To retry:

```bash
copy channels_failed.txt channels.txt
python migrate.py import
```

Already-subscribed channels are automatically skipped.

## Switching accounts again later

Delete the `profiles/` folder to clear saved logins, then start from step 1.

## How it works

- Uses Playwright to automate a real Chromium browser
- Persistent browser profiles store your login sessions
- Export scrolls through youtube.com/feed/channels and scrapes all channel URLs
- Import visits each channel page and clicks the Subscribe button via JavaScript
- Random delays (3-7s between subscribes, 30-60s break every 20) to avoid detection
- Ctrl+C during import saves progress — re-running skips already-subscribed channels
