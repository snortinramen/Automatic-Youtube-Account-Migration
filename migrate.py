#!/usr/bin/env python3
"""YouTube Account Migration Tool

Migrates YouTube data between accounts using browser automation.
Supports: subscriptions, liked videos, Watch Later, and custom playlists.

Usage:
    python migrate.py login-old               # Log in to your OLD account
    python migrate.py login-new               # Log in to your NEW account
    python migrate.py export subs             # Export subscriptions
    python migrate.py export likes            # Export liked videos
    python migrate.py export watch-later      # Export Watch Later playlist
    python migrate.py export playlists        # Export custom playlists
    python migrate.py export all              # Export everything
    python migrate.py import subs             # Subscribe on new account
    python migrate.py import likes            # Like videos on new account
    python migrate.py import watch-later      # Add to Watch Later on new account
    python migrate.py import playlists        # Create playlists on new account
    python migrate.py import all              # Import everything
"""

import argparse
import random
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

BASE_DIR = Path(__file__).parent
PROFILES_DIR = BASE_DIR / "profiles"

# Data files
CHANNELS_FILE = BASE_DIR / "channels.txt"
CHANNELS_FAILED_FILE = BASE_DIR / "channels_failed.txt"
LIKES_FILE = BASE_DIR / "likes.txt"
LIKES_FAILED_FILE = BASE_DIR / "likes_failed.txt"
WATCH_LATER_FILE = BASE_DIR / "watch_later.txt"
WATCH_LATER_FAILED_FILE = BASE_DIR / "watch_later_failed.txt"
PLAYLISTS_DIR = BASE_DIR / "playlists"
PLAYLISTS_FAILED_DIR = BASE_DIR / "playlists_failed"

# Delay settings (seconds)
ACTION_DELAY_MIN = 3.0
ACTION_DELAY_MAX = 7.0
LONG_BREAK_EVERY = 20
LONG_BREAK_MIN = 30.0
LONG_BREAK_MAX = 60.0

# Scroll settings
MAX_SCROLL_ITERATIONS = 200
SCROLL_WAIT_SECONDS = 2.0

# --- JavaScript snippets ---

EXTRACT_CHANNELS_JS = """
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
"""

PLAYLIST_VIDEO_SELECTOR = (
    "ytd-playlist-video-renderer, "
    "ytd-reel-item-renderer, "
    "ytd-playlist-panel-video-renderer, "
    "ytd-rich-item-renderer"
)

EXTRACT_VIDEO_URLS_JS = """
    (() => {
        const seen = new Set();
        const results = [];
        // Search all video renderers (regular videos + shorts)
        const renderers = document.querySelectorAll(
            'ytd-playlist-video-renderer, ytd-reel-item-renderer, ytd-playlist-panel-video-renderer, ytd-rich-item-renderer'
        );
        for (const el of renderers) {
            // Try the standard title link first, then any link with a video ID
            let link = el.querySelector('a#video-title');
            if (!link) {
                for (const a of el.querySelectorAll('a[href]')) {
                    if (a.href && (a.href.includes('/watch?') || a.href.includes('/shorts/'))) {
                        link = a;
                        break;
                    }
                }
            }
            if (!link) continue;
            const href = link.href;
            // Match both /watch?v=ID and /shorts/ID
            let videoId = '';
            const watchMatch = href.match(/[?&]v=([^&]+)/);
            const shortsMatch = href.match(/\\/shorts\\/([^?&]+)/);
            if (watchMatch) videoId = watchMatch[1];
            else if (shortsMatch) videoId = shortsMatch[1];
            if (!videoId || seen.has(videoId)) continue;
            seen.add(videoId);
            const title = (el.querySelector('a#video-title') || el.querySelector('#video-title') || link).textContent.trim();
            results.push({ url: 'https://www.youtube.com/watch?v=' + videoId, title: title });
        }
        return results;
    })()
"""

EXTRACT_PLAYLISTS_JS = """
    (() => {
        // Find ALL links on the page that point to a playlist
        const seen = new Set();
        const results = [];
        for (const a of document.querySelectorAll('a[href*="list="]')) {
            const href = a.href || '';
            const match = href.match(/[?&]list=([^&]+)/);
            if (!match) continue;
            const listId = match[1];
            // Skip Liked Videos and Watch Later
            if (listId === 'LL' || listId === 'WL') continue;
            if (seen.has(listId)) continue;
            seen.add(listId);
            // Get the playlist name from the parent card's title element
            let name = '';
            const parent = a.closest(
                'ytd-rich-item-renderer, ytd-grid-playlist-renderer, ytd-playlist-renderer'
            );
            if (parent) {
                const titleEl = parent.querySelector(
                    '#video-title, h3 a, h3, yt-formatted-string.ytd-playlist-renderer'
                );
                if (titleEl) name = titleEl.textContent.trim();
            }
            // Fallback: use the page title from the playlist URL later
            if (!name) name = 'Playlist_' + listId.slice(0, 8);
            // Clean the URL to just the playlist page
            const cleanUrl = 'https://www.youtube.com/playlist?list=' + listId;
            results.push({ name, url: cleanUrl });
        }
        return results;
    })()
"""

CLICK_SUBSCRIBE_JS = """
    (() => {
        const buttons = document.querySelectorAll('button');
        for (const btn of buttons) {
            if (btn.textContent.trim() === 'Subscribe') {
                btn.click();
                return true;
            }
        }
        return false;
    })()
"""

LIKE_BUTTON_JS = """
    (() => {
        const container = document.querySelector('segmented-like-dislike-button-view-model');
        if (container) {
            const btn = container.querySelector('button');
            if (btn) return { found: true, pressed: btn.getAttribute('aria-pressed') };
        }
        const allBtns = document.querySelectorAll('button[aria-label]');
        for (const btn of allBtns) {
            const label = btn.getAttribute('aria-label').toLowerCase();
            if (label.startsWith('like this video')) {
                return { found: true, pressed: btn.getAttribute('aria-pressed') };
            }
        }
        return { found: false };
    })()
"""

CLICK_LIKE_JS = """
    (() => {
        const container = document.querySelector('segmented-like-dislike-button-view-model');
        if (container) {
            const btn = container.querySelector('button');
            if (btn && btn.getAttribute('aria-pressed') !== 'true') {
                btn.click();
                return true;
            }
        }
        const allBtns = document.querySelectorAll('button[aria-label]');
        for (const btn of allBtns) {
            const label = btn.getAttribute('aria-label').toLowerCase();
            if (label.startsWith('like this video') && btn.getAttribute('aria-pressed') !== 'true') {
                btn.click();
                return true;
            }
        }
        return false;
    })()
"""

OPEN_SAVE_JS = """
    (() => {
        const saveBtn = document.querySelector('button[aria-label="Save to playlist"]');
        if (saveBtn) { saveBtn.click(); return 'opened'; }
        const moreBtn = document.querySelector('button[aria-label="More actions"]');
        if (moreBtn) { moreBtn.click(); return 'overflow'; }
        return false;
    })()
"""

# =========================================================================
# Shared helpers
# =========================================================================


def launch_profile(playwright, profile_name):
    """Launch a persistent Chromium browser with a saved profile."""
    user_data_dir = PROFILES_DIR / profile_name
    user_data_dir.mkdir(parents=True, exist_ok=True)
    return playwright.chromium.launch_persistent_context(
        str(user_data_dir),
        headless=False,
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )


def scroll_and_collect(page, item_selector, extract_js, label="items"):
    """Infinite-scroll a page and extract data from all loaded items."""
    prev_count = 0
    stale_rounds = 0

    for i in range(MAX_SCROLL_ITERATIONS):
        count = page.evaluate(
            f"document.querySelectorAll('{item_selector}').length"
        )
        if count == prev_count:
            stale_rounds += 1
            if stale_rounds >= 2:
                break
        else:
            stale_rounds = 0

        prev_count = count
        print(f"  Found {count} {label} so far... (scroll {i + 1})")
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        time.sleep(SCROLL_WAIT_SECONDS)

    return page.evaluate(extract_js)


def read_urls_from_file(filepath):
    """Read URLs from a text file, skipping comments and blank lines."""
    urls = []
    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    return urls


def write_items_to_file(filepath, items):
    """Write a list of URLs to a text file, one per line."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        for item in items:
            f.write(item + "\n")


def rate_limited_loop(items, action_fn, label, failed_file):
    """Process items one at a time with rate limiting and failure tracking."""
    failed = []
    success_count = 0

    for i, url in enumerate(items, 1):
        print(f"[{i}/{len(items)}] {url}")
        try:
            result = action_fn(url)
            if result == "skipped":
                success_count += 1
            elif result:
                success_count += 1
            else:
                failed.append(url)
        except PlaywrightTimeout:
            print("  ERROR: Page took too long to load. Skipping.")
            failed.append(url)
        except KeyboardInterrupt:
            print("\n\nInterrupted! Saving progress...")
            break
        except Exception as e:
            print(f"  ERROR: {e}")
            failed.append(url)

        # Rate limiting
        if i % LONG_BREAK_EVERY == 0 and i < len(items):
            wait = random.uniform(LONG_BREAK_MIN, LONG_BREAK_MAX)
            print(f"  Taking a {wait:.0f}s break to avoid detection...")
            time.sleep(wait)
        elif i < len(items):
            time.sleep(random.uniform(ACTION_DELAY_MIN, ACTION_DELAY_MAX))

    print(f"\nDone! {label}: {success_count}/{len(items)}")

    if failed:
        write_items_to_file(failed_file, failed)
        print(f"{len(failed)} failed — saved to {failed_file}")


def wait_for_video_page(page):
    """Wait for a YouTube video page to render its action buttons."""
    try:
        page.wait_for_selector(
            "segmented-like-dislike-button-view-model, #top-level-buttons-computed",
            timeout=15000,
        )
    except PlaywrightTimeout:
        pass
    time.sleep(1)


def open_save_dialog(page):
    """Click the Save to playlist button, handling overflow menu if needed."""
    result = page.evaluate(OPEN_SAVE_JS)

    if result == "opened":
        return True

    if result == "overflow":
        time.sleep(0.5)
        clicked = page.evaluate("""
            (() => {
                const items = document.querySelectorAll(
                    'tp-yt-paper-listbox ytd-menu-service-item-renderer, ytd-menu-popup-renderer ytd-menu-service-item-renderer'
                );
                for (const item of items) {
                    if (item.textContent.includes('Save')) {
                        item.click();
                        return true;
                    }
                }
                return false;
            })()
        """)
        return clicked

    return False


def sanitize_filename(name):
    """Remove characters that are invalid in Windows filenames."""
    invalid = '<>:"/\\|?*'
    for ch in invalid:
        name = name.replace(ch, "_")
    return name.strip().rstrip(".")


# =========================================================================
# Login
# =========================================================================


def cmd_login(profile_name, label):
    """Open a browser for the user to log in manually."""
    print(f"\nOpening browser for {label} account login...")
    print("Log in to your YouTube account, then close the browser when done.\n")

    with sync_playwright() as p:
        ctx = launch_profile(p, profile_name)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.youtube.com")

        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass

        try:
            ctx.close()
        except Exception:
            pass

    print(f"{label} account login saved. You can now run export/import.\n")


# =========================================================================
# Subscriptions
# =========================================================================


def export_subs(page):
    """Export subscribed channels from the old account."""
    print("\nExporting subscriptions...\n")

    page.goto("https://www.youtube.com/feed/channels", wait_until="networkidle")

    try:
        page.wait_for_selector("ytd-channel-renderer", timeout=15000)
    except PlaywrightTimeout:
        print("ERROR: Could not load subscriptions. Are you logged in?")
        return

    channels = scroll_and_collect(
        page, "ytd-channel-renderer", EXTRACT_CHANNELS_JS, "channels"
    )

    with open(CHANNELS_FILE, "w", encoding="utf-8") as f:
        for ch in channels:
            f.write(f"# {ch['name']}\n")
            f.write(f"{ch['url']}\n")

    print(f"\nExported {len(channels)} channels to {CHANNELS_FILE}")


def import_subs(page):
    """Subscribe to channels on the new account."""
    if not CHANNELS_FILE.exists():
        print(f"ERROR: {CHANNELS_FILE} not found. Run 'export subs' first.")
        return

    urls = read_urls_from_file(CHANNELS_FILE)
    if not urls:
        print("No channel URLs found.")
        return

    print(f"\nFound {len(urls)} channels to subscribe to.\n")

    def subscribe(url):
        page.goto(url, wait_until="load", timeout=30000)
        time.sleep(3)

        # Check if already subscribed
        try:
            btn = page.get_by_role("button", name="Subscribed", exact=True)
            if btn.is_visible(timeout=500):
                print("  Already subscribed. Skipping.")
                return "skipped"
        except Exception:
            pass

        # Click subscribe
        clicked = page.evaluate(CLICK_SUBSCRIBE_JS)
        if not clicked:
            try:
                btn = page.get_by_role("button", name="Subscribe", exact=True)
                if btn.is_visible(timeout=2000):
                    btn.click(force=True)
                    clicked = True
            except Exception:
                pass

        if not clicked:
            print("  WARNING: Could not find Subscribe button.")
            return False

        time.sleep(3)
        print("  Subscribed!")
        return True

    rate_limited_loop(urls, subscribe, "Subscribed", CHANNELS_FAILED_FILE)


# =========================================================================
# Liked Videos
# =========================================================================


def export_likes(page):
    """Export liked videos from the old account."""
    print("\nExporting liked videos...\n")

    page.goto("https://www.youtube.com/playlist?list=LL", wait_until="networkidle")

    try:
        page.wait_for_selector(PLAYLIST_VIDEO_SELECTOR, timeout=15000)
    except PlaywrightTimeout:
        print("ERROR: Could not load liked videos. Are you logged in?")
        return

    videos = scroll_and_collect(
        page, PLAYLIST_VIDEO_SELECTOR, EXTRACT_VIDEO_URLS_JS, "liked videos"
    )

    with open(LIKES_FILE, "w", encoding="utf-8") as f:
        f.write("# Liked Videos\n")
        for v in videos:
            f.write(f"# {v['title']}\n")
            f.write(f"{v['url']}\n")

    print(f"\nExported {len(videos)} liked videos to {LIKES_FILE}")


def import_likes(page):
    """Like videos on the new account."""
    if not LIKES_FILE.exists():
        print(f"ERROR: {LIKES_FILE} not found. Run 'export likes' first.")
        return

    urls = read_urls_from_file(LIKES_FILE)
    if not urls:
        print("No video URLs found.")
        return

    # Reverse so oldest gets liked first, preserving the original order
    urls.reverse()

    print(f"\nFound {len(urls)} videos to like.\n")

    def like_video(url):
        page.goto(url, wait_until="load", timeout=30000)
        wait_for_video_page(page)

        result = page.evaluate(LIKE_BUTTON_JS)
        if not result.get("found"):
            print("  WARNING: Like button not found.")
            return False

        if result.get("pressed") == "true":
            print("  Already liked. Skipping.")
            return "skipped"

        clicked = page.evaluate(CLICK_LIKE_JS)
        if clicked:
            time.sleep(1)
            print("  Liked!")
            return True

        print("  WARNING: Failed to click like button.")
        return False

    rate_limited_loop(urls, like_video, "Liked", LIKES_FAILED_FILE)


# =========================================================================
# Watch Later
# =========================================================================


def export_watch_later(page):
    """Export Watch Later playlist from the old account."""
    print("\nExporting Watch Later...\n")

    page.goto("https://www.youtube.com/playlist?list=WL", wait_until="networkidle")

    try:
        page.wait_for_selector(PLAYLIST_VIDEO_SELECTOR, timeout=15000)
    except PlaywrightTimeout:
        print("ERROR: Could not load Watch Later. Are you logged in?")
        return

    videos = scroll_and_collect(
        page, PLAYLIST_VIDEO_SELECTOR, EXTRACT_VIDEO_URLS_JS, "Watch Later videos"
    )

    with open(WATCH_LATER_FILE, "w", encoding="utf-8") as f:
        f.write("# Watch Later\n")
        for v in videos:
            f.write(f"# {v['title']}\n")
            f.write(f"{v['url']}\n")

    print(f"\nExported {len(videos)} Watch Later videos to {WATCH_LATER_FILE}")


def import_watch_later(page):
    """Add videos to Watch Later on the new account."""
    if not WATCH_LATER_FILE.exists():
        print(f"ERROR: {WATCH_LATER_FILE} not found. Run 'export watch-later' first.")
        return

    urls = read_urls_from_file(WATCH_LATER_FILE)
    if not urls:
        print("No video URLs found.")
        return

    # Reverse so oldest gets added first, preserving the original order
    urls.reverse()

    print(f"\nFound {len(urls)} videos to add to Watch Later.\n")

    def add_to_watch_later(url):
        page.goto(url, wait_until="load", timeout=30000)
        wait_for_video_page(page)

        if not open_save_dialog(page):
            print("  WARNING: Could not find Save button.")
            return False

        time.sleep(1)

        # Click the "Watch later" row in the "Save to..." dialog
        clicked = page.evaluate("""
            (() => {
                // Try clicking any element containing "Watch later" text
                const allEls = document.querySelectorAll(
                    'ytd-playlist-add-to-option-renderer, ytd-compact-link-renderer, a, button, div[role="option"], div[role="button"]'
                );
                for (const el of allEls) {
                    const text = el.textContent.trim();
                    if (text.includes('Watch later') || text.includes('Watch Later')) {
                        el.click();
                        return true;
                    }
                }
                return false;
            })()
        """)

        if not clicked:
            # Fallback: use Playwright role-based click
            try:
                wl = page.get_by_text("Watch later", exact=False).first
                if wl.is_visible(timeout=2000):
                    wl.click()
                    clicked = True
            except Exception:
                pass

        time.sleep(0.5)
        page.keyboard.press("Escape")
        time.sleep(0.3)

        if not clicked:
            print("  WARNING: Watch Later option not found in dialog.")
            return False

        print("  Added to Watch Later!")
        return True

    rate_limited_loop(urls, add_to_watch_later, "Added to Watch Later", WATCH_LATER_FAILED_FILE)


# =========================================================================
# Custom Playlists
# =========================================================================


def export_playlists(page):
    """Export all custom playlists from the old account."""
    print("\nExporting playlists...\n")

    page.goto("https://www.youtube.com/feed/playlists", wait_until="networkidle")

    try:
        page.wait_for_selector(
            "ytd-grid-playlist-renderer, ytd-playlist-renderer, ytd-rich-item-renderer",
            timeout=15000,
        )
    except PlaywrightTimeout:
        print("ERROR: Could not load playlists page. Are you logged in?")
        return

    playlists = scroll_and_collect(
        page,
        "ytd-grid-playlist-renderer, ytd-playlist-renderer, ytd-rich-item-renderer",
        EXTRACT_PLAYLISTS_JS,
        "playlists",
    )

    if not playlists:
        print("No playlists found.")
        return

    print(f"\nFound {len(playlists)} playlists. Scraping videos from each...\n")
    PLAYLISTS_DIR.mkdir(parents=True, exist_ok=True)

    for idx, pl in enumerate(playlists, 1):
        name = pl["name"]
        print(f"[{idx}/{len(playlists)}] {name}")

        page.goto(pl["url"], wait_until="networkidle")

        # Try to get the real playlist name from the page header
        try:
            title_el = page.wait_for_selector(
                "yt-dynamic-sizing-formatted-string#title, "
                "h1.ytd-playlist-header-renderer, "
                "yt-formatted-string.ytd-playlist-sidebar-primary-info-renderer",
                timeout=5000,
            )
            page_title = title_el.text_content().strip() if title_el else ""
            if page_title and page_title != name:
                name = page_title
                print(f"  -> Playlist name: {name}")
        except PlaywrightTimeout:
            pass

        try:
            page.wait_for_selector(PLAYLIST_VIDEO_SELECTOR, timeout=15000)
        except PlaywrightTimeout:
            print(f"  No videos found. Skipping.")
            continue

        # Check if this is a regular playlist or a shorts playlist
        has_standard = page.evaluate(
            "document.querySelectorAll('ytd-playlist-video-list-renderer').length > 0"
        )

        if has_standard:
            # Scope to the playlist container only — excludes "Recommended videos"
            scoped_selector = "ytd-playlist-video-list-renderer ytd-playlist-video-renderer"
            scoped_extract = """
                (() => {
                    const container = document.querySelector('ytd-playlist-video-list-renderer');
                    if (!container) return [];
                    const seen = new Set();
                    const results = [];
                    for (const el of container.querySelectorAll('ytd-playlist-video-renderer')) {
                        let link = el.querySelector('a#video-title');
                        if (!link) continue;
                        const href = link.href;
                        const watchMatch = href.match(/[?&]v=([^&]+)/);
                        const shortsMatch = href.match(/\\/shorts\\/([^?&]+)/);
                        let videoId = '';
                        if (watchMatch) videoId = watchMatch[1];
                        else if (shortsMatch) videoId = shortsMatch[1];
                        if (!videoId || seen.has(videoId)) continue;
                        seen.add(videoId);
                        const title = link.textContent.trim();
                        results.push({ url: 'https://www.youtube.com/watch?v=' + videoId, title });
                    }
                    return results;
                })()
            """
            videos = scroll_and_collect(
                page, scoped_selector, scoped_extract, "videos"
            )
        else:
            # Shorts playlists use ytd-rich-item-renderer
            videos = scroll_and_collect(
                page, PLAYLIST_VIDEO_SELECTOR, EXTRACT_VIDEO_URLS_JS, "videos"
            )

        safe_name = sanitize_filename(name)
        filepath = PLAYLISTS_DIR / f"{safe_name}.txt"

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# {name}\n")
            for v in videos:
                f.write(f"# {v['title']}\n")
                f.write(f"{v['url']}\n")

        print(f"  Exported {len(videos)} videos to {filepath}")

    print(f"\nExported {len(playlists)} playlists to {PLAYLISTS_DIR}/")


def import_playlists(page):
    """Create playlists and add videos on the new account."""
    if not PLAYLISTS_DIR.exists() or not any(PLAYLISTS_DIR.glob("*.txt")):
        print("ERROR: No playlist files found in playlists/ directory.")
        print("Run 'export playlists' first.")
        return

    playlist_files = sorted(PLAYLISTS_DIR.glob("*.txt"))
    print(f"\nFound {len(playlist_files)} playlists to import.\n")

    created_playlists = set()

    for pf in playlist_files:
        # Read playlist name from header comment
        playlist_name = pf.stem
        with open(pf, "r", encoding="utf-8") as f:
            first_line = f.readline().strip()
            if first_line.startswith("# "):
                playlist_name = first_line[2:]

        urls = read_urls_from_file(pf)
        if not urls:
            print(f"Skipping empty playlist: {playlist_name}")
            continue

        # Playlists are exported in display order (top to bottom), so import
        # in the same order — no reversal needed unlike likes/watch later

        print(f"\n{'='*50}")
        print(f"Playlist: {playlist_name} ({len(urls)} videos)")
        print(f"{'='*50}\n")

        def add_to_playlist(url, _name=playlist_name):
            return _add_video_to_playlist(page, url, _name, created_playlists)

        failed_file = PLAYLISTS_FAILED_DIR / pf.name
        rate_limited_loop(urls, add_to_playlist, f"Added to '{playlist_name}'", failed_file)


def _add_video_to_playlist(page, url, playlist_name, created_playlists):
    """Navigate to a video and add it to the specified playlist."""
    page.goto(url, wait_until="load", timeout=30000)
    wait_for_video_page(page)

    if not open_save_dialog(page):
        print("  WARNING: Could not open Save dialog.")
        return False

    # Wait for the popup dialog to fully render
    time.sleep(3)

    # Use the popup container — this is where YouTube renders ALL dialogs
    popup = page.locator("ytd-popup-container")

    # Look for the playlist name inside the popup
    found = False
    try:
        row = popup.get_by_text(playlist_name, exact=False).first
        found = row.is_visible(timeout=2000)
    except Exception:
        found = False

    if found:
        # Click to add (trusted Playwright click)
        row.click()
        time.sleep(1)
        page.keyboard.press("Escape")
        time.sleep(0.3)
        print(f"  Added to '{playlist_name}'!")
        return True

    # Playlist not in dialog — create it
    # Click "+ New playlist"
    try:
        new_pl_btn = popup.get_by_text("New playlist").first
        new_pl_btn.click(timeout=3000)
    except Exception:
        page.keyboard.press("Escape")
        print("  WARNING: Could not find 'New playlist' button.")
        return False

    time.sleep(1.5)

    # Type the playlist name into "Choose a title"
    try:
        title_area = popup.get_by_text("Choose a title").first
        title_area.click(timeout=2000)
        time.sleep(0.3)
        page.keyboard.type(playlist_name, delay=30)
    except Exception:
        page.keyboard.press("Escape")
        print("  WARNING: Could not fill playlist name.")
        return False

    time.sleep(0.5)

    # Click Create
    try:
        create_btn = popup.locator("button").filter(has_text="Create").last
        create_btn.click(timeout=3000)
    except Exception:
        page.keyboard.press("Escape")
        print("  WARNING: Could not click 'Create' button.")
        return False

    time.sleep(2)
    created_playlists.add(playlist_name)
    page.keyboard.press("Escape")
    time.sleep(0.3)
    print(f"  Created playlist '{playlist_name}' and added video!")
    return True


# =========================================================================
# Dispatchers
# =========================================================================

EXPORT_DISPATCH = {
    "subs": export_subs,
    "likes": export_likes,
    "watch-later": export_watch_later,
    "playlists": export_playlists,
}

IMPORT_DISPATCH = {
    "subs": import_subs,
    "likes": import_likes,
    "watch-later": import_watch_later,
    "playlists": import_playlists,
}


def run_export(target):
    """Open old account browser and run the requested export(s)."""
    with sync_playwright() as p:
        ctx = launch_profile(p, "old_account")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            if target == "all":
                for name, fn in EXPORT_DISPATCH.items():
                    print(f"\n{'='*50}")
                    print(f"Exporting {name}...")
                    print(f"{'='*50}")
                    fn(page)
            else:
                EXPORT_DISPATCH[target](page)
        finally:
            try:
                ctx.close()
            except Exception:
                pass


def run_import(target):
    """Open new account browser and run the requested import(s)."""
    with sync_playwright() as p:
        ctx = launch_profile(p, "new_account")
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        try:
            if target == "all":
                for name, fn in IMPORT_DISPATCH.items():
                    print(f"\n{'='*50}")
                    print(f"Importing {name}...")
                    print(f"{'='*50}")
                    fn(page)
            else:
                IMPORT_DISPATCH[target](page)
        finally:
            try:
                ctx.close()
            except Exception:
                pass


# =========================================================================
# CLI
# =========================================================================


def main():
    parser = argparse.ArgumentParser(description="YouTube Account Migration Tool")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("login-old", help="Log in to your OLD account")
    commands.add_parser("login-new", help="Log in to your NEW account")

    export_cmd = commands.add_parser("export", help="Export data from old account")
    export_cmd.add_argument(
        "target",
        choices=["subs", "likes", "watch-later", "playlists", "all"],
        help="What to export",
    )

    import_cmd = commands.add_parser("import", help="Import data to new account")
    import_cmd.add_argument(
        "target",
        choices=["subs", "likes", "watch-later", "playlists", "all"],
        help="What to import",
    )

    args = parser.parse_args()

    if args.command == "login-old":
        cmd_login("old_account", "OLD")
    elif args.command == "login-new":
        cmd_login("new_account", "NEW")
    elif args.command == "export":
        run_export(args.target)
    elif args.command == "import":
        run_import(args.target)


if __name__ == "__main__":
    main()
