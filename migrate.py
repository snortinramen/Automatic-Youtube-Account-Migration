#!/usr/bin/env python3
"""YouTube Subscription Migration Tool

Migrates YouTube subscriptions between accounts using browser automation.

Usage:
    python migrate.py login-old   # Log in to your OLD account
    python migrate.py login-new   # Log in to your NEW account
    python migrate.py export      # Export subscriptions from old account
    python migrate.py import      # Subscribe on new account
"""

import argparse
import random
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

BASE_DIR = Path(__file__).parent
PROFILES_DIR = BASE_DIR / "profiles"
CHANNELS_FILE = BASE_DIR / "channels.txt"
FAILED_FILE = BASE_DIR / "channels_failed.txt"

# Delay settings (seconds)
SUBSCRIBE_DELAY_MIN = 3.0
SUBSCRIBE_DELAY_MAX = 7.0
LONG_BREAK_EVERY = 20
LONG_BREAK_MIN = 30.0
LONG_BREAK_MAX = 60.0

# Scroll settings
MAX_SCROLL_ITERATIONS = 200
SCROLL_WAIT_SECONDS = 2.0


def launch_profile(playwright, profile_name):
    """Launch a persistent Chromium browser with a saved profile."""
    user_data_dir = PROFILES_DIR / profile_name
    user_data_dir.mkdir(parents=True, exist_ok=True)
    context = playwright.chromium.launch_persistent_context(
        str(user_data_dir),
        headless=False,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )
    return context


def cmd_login(profile_name, label):
    """Open a browser for the user to log in manually."""
    print(f"\nOpening browser for {label} account login...")
    print("Log in to your YouTube account, then close the browser when done.\n")

    with sync_playwright() as p:
        ctx = launch_profile(p, profile_name)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.youtube.com")

        # Wait until the user closes the browser
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass

        try:
            ctx.close()
        except Exception:
            pass

    print(f"{label} account login saved. You can now run export/import.\n")


def cmd_export():
    """Scrape all subscribed channels from the old account."""
    print("\nExporting subscriptions from old account...\n")

    with sync_playwright() as p:
        ctx = launch_profile(p, "old_account")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        page.goto("https://www.youtube.com/feed/channels", wait_until="networkidle")

        # Check if subscriptions loaded (i.e. user is logged in)
        try:
            page.wait_for_selector("ytd-channel-renderer", timeout=15000)
        except PlaywrightTimeout:
            print("ERROR: Could not load subscriptions.")
            print("Make sure you ran 'python migrate.py login-old' first.")
            ctx.close()
            sys.exit(1)

        # Scroll to load all subscriptions
        prev_count = 0
        stale_rounds = 0

        for i in range(MAX_SCROLL_ITERATIONS):
            count = page.evaluate(
                "document.querySelectorAll('ytd-channel-renderer').length"
            )

            if count == prev_count:
                stale_rounds += 1
                if stale_rounds >= 2:
                    break
            else:
                stale_rounds = 0

            prev_count = count
            print(f"  Found {count} channels so far... (scroll {i + 1})")

            page.evaluate(
                "window.scrollTo(0, document.documentElement.scrollHeight)"
            )
            time.sleep(SCROLL_WAIT_SECONDS)

        # Extract channel names and URLs
        channels = page.evaluate("""
            Array.from(document.querySelectorAll('ytd-channel-renderer')).map(el => {
                const nameEl = el.querySelector('#text-container') ||
                               el.querySelector('#channel-title') ||
                               el.querySelector('yt-formatted-string#text');
                const linkEl = el.querySelector('#main-link') ||
                               el.querySelector('a#thumbnail');
                return {
                    name: nameEl ? nameEl.textContent.trim() : 'Unknown',
                    url: linkEl ? linkEl.href : ''
                };
            }).filter(ch => ch.url)
        """)

        # Write to file
        with open(CHANNELS_FILE, "w", encoding="utf-8") as f:
            for ch in channels:
                f.write(f"# {ch['name']}\n")
                f.write(f"{ch['url']}\n")

        print(f"\nExported {len(channels)} channels to {CHANNELS_FILE}")
        print("You can review/edit this file before importing.\n")

        ctx.close()


def try_subscribe(page):
    """Attempt to click the Subscribe button."""
    # Try JavaScript click first — most reliable with YouTube's custom elements
    try:
        clicked = page.evaluate("""
            (() => {
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    const text = btn.textContent.trim();
                    if (text === 'Subscribe') {
                        btn.click();
                        return true;
                    }
                }
                return false;
            })()
        """)
        if clicked:
            return True
    except Exception:
        pass

    # Fallback: Playwright role-based click with force
    try:
        btn = page.get_by_role("button", name="Subscribe", exact=True)
        if btn.is_visible(timeout=2000):
            btn.click(force=True)
            return True
    except Exception:
        pass

    return False


def cmd_import():
    """Read channel URLs and subscribe to each on the new account."""
    if not CHANNELS_FILE.exists():
        print(f"ERROR: {CHANNELS_FILE} not found.")
        print("Run 'python migrate.py export' first.")
        sys.exit(1)

    # Parse channel URLs (skip comments and blanks)
    urls = []
    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)

    if not urls:
        print("No channel URLs found in channels.txt")
        sys.exit(1)

    print(f"\nFound {len(urls)} channels to subscribe to.\n")

    failed = []
    subscribed_count = 0

    with sync_playwright() as p:
        ctx = launch_profile(p, "new_account")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        for i, url in enumerate(urls, 1):
            print(f"[{i}/{len(urls)}] {url}")

            try:
                page.goto(url, wait_until="load", timeout=30000)

                # Give the page time to fully render
                time.sleep(3)

                # Check if already subscribed (button says "Subscribed" instead of "Subscribe")
                try:
                    subscribed_btn = page.get_by_role("button", name="Subscribed", exact=True)
                    if subscribed_btn.is_visible(timeout=500):
                        print("  Already subscribed. Skipping.")
                        subscribed_count += 1
                        continue
                except Exception:
                    pass

                # Click subscribe
                clicked = try_subscribe(page)
                if not clicked:
                    print("  WARNING: Could not find Subscribe button.")
                    failed.append(url)
                    continue

                # Wait for YouTube to process the subscription
                time.sleep(3)

                print("  Subscribed!")
                subscribed_count += 1

            except PlaywrightTimeout:
                print("  ERROR: Page took too long to load. Skipping.")
                failed.append(url)
            except KeyboardInterrupt:
                print("\n\nInterrupted! Saving progress...")
                break
            except Exception as e:
                print(f"  ERROR: {e}")
                failed.append(url)

            # Rate limiting delays
            if i % LONG_BREAK_EVERY == 0 and i < len(urls):
                wait = random.uniform(LONG_BREAK_MIN, LONG_BREAK_MAX)
                print(f"  Taking a {wait:.0f}s break to avoid detection...")
                time.sleep(wait)
            elif i < len(urls):
                time.sleep(random.uniform(SUBSCRIBE_DELAY_MIN, SUBSCRIBE_DELAY_MAX))

        try:
            ctx.close()
        except Exception:
            pass

    # Summary
    print(f"\nDone! Subscribed to {subscribed_count}/{len(urls)} channels.")

    if failed:
        with open(FAILED_FILE, "w", encoding="utf-8") as f:
            for url in failed:
                f.write(url + "\n")
        print(f"{len(failed)} failed — saved to {FAILED_FILE}")
        print("To retry: copy channels_failed.txt to channels.txt and run import again.")


def main():
    parser = argparse.ArgumentParser(
        description="YouTube Subscription Migration Tool"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("login-old", help="Open browser to log in to your OLD account")
    commands.add_parser("login-new", help="Open browser to log in to your NEW account")
    commands.add_parser("export", help="Export subscriptions from old account")
    commands.add_parser("import", help="Import subscriptions to new account")

    args = parser.parse_args()

    if args.command == "login-old":
        cmd_login("old_account", "OLD")
    elif args.command == "login-new":
        cmd_login("new_account", "NEW")
    elif args.command == "export":
        cmd_export()
    elif args.command == "import":
        cmd_import()


if __name__ == "__main__":
    main()
