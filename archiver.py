import feedparser
import requests
import time
import os
import sys
import re
import random
from datetime import datetime

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
SUBREDDIT = os.environ.get("SUBREDDIT", "boltedontits") 
RSS_URL = f"https://www.reddit.com/r/{SUBREDDIT}/new/.rss"
SEEN_FILE = "seen.txt"
FAILED_FILE = "failed.txt"

USER_AGENT = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) WaybackArchiver/2.0 (github.com/action; r/{SUBREDDIT})"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/rss+xml, application/xml, text/xml"
}

# ⏳ IMPROVED TIMINGS
WAYBACK_TIMEOUT = 60         # Increased timeout
SLEEP_BETWEEN = 12           # Longer delays between requests
MAX_RETRIES = 3              # More retries
MAX_POSTS_PER_RUN = 5        # REDUCED: Process fewer posts per run to avoid timeout
MAX_SEEN_ENTRIES = 10000
CIRCUIT_BREAKER_THRESHOLD = 5  # Stop after 5 consecutive 523 errors

def log(msg):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)

def validate_subreddit():
    if not SUBREDDIT:
        log("❌ ERROR: SUBREDDIT environment variable not set!")
        return False
    if not re.match(r'^[a-zA-Z0-9_]{3,21}$', SUBREDDIT):
        log(f"❌ ERROR: Invalid subreddit name format: '{SUBREDDIT}'")
        return False
    return True

def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r") as f:
        lines = f.readlines()
    if len(lines) > MAX_SEEN_ENTRIES:
        recent = lines[-MAX_SEEN_ENTRIES:]
        with open(SEEN_FILE, "w") as f:
            f.writelines(recent)
        lines = recent
    seen_urls = set()
    for line in lines:
        line = line.strip()
        if not line: continue
        if '|' in line:
            parts = line.split('|')
            if len(parts) >= 2: seen_urls.add(parts[1])
        else:
            seen_urls.add(line)
    return seen_urls

def append_seen(post_url):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with open(SEEN_FILE, "a") as f:
        f.write(f"{timestamp}|{post_url}\n")

def log_failed(post_url, status, skip_archive=False):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    skip_marker = "|SKIP" if skip_archive else ""
    with open(FAILED_FILE, "a") as f:
        f.write(f"{timestamp}|{post_url}|{status}{skip_marker}\n")

def archive(url, retries=MAX_RETRIES):
    """
    Attempt to archive a URL to the Wayback Machine.
    Returns: status code (int), error message (str), or None
    """
    for attempt in range(retries):
        wayback_url = f"https://web.archive.org/save/{url}"
        
        try:
            log(f"   🔄 Attempt {attempt + 1}/{retries}")
            r = requests.get(wayback_url, headers=HEADERS, timeout=WAYBACK_TIMEOUT, allow_redirects=True)
            
            # Handle rate limiting
            if r.status_code == 429:
                if attempt < retries - 1:
                    wait = 30 + random.uniform(0, 20)
                    log(f"   ⏳ Rate limited (429). Waiting {wait:.1f}s...")
                    time.sleep(wait)
                    continue
                else: 
                    return 429
            
            # Success cases
            if r.status_code == 200:
                return 200
            
            # Origin unreachable - Wayback can't access Reddit
            if r.status_code == 523:
                log(f"   ⚠️  523 Origin Unreachable - Wayback can't reach Reddit")
                if attempt < retries - 1:
                    wait = 20 + random.uniform(0, 15)
                    log(f"   ⏳ Waiting {wait:.1f}s before retry...")
                    time.sleep(wait)
                    continue
                return 523
            
            # Other error codes
            log(f"   ⚠️  Unexpected status: {r.status_code}")
            if attempt < retries - 1:
                time.sleep(10)
                continue
            return r.status_code
            
        except requests.exceptions.Timeout:
            log(f"   ⏰ Request timeout")
            if attempt < retries - 1:
                time.sleep(10)
                continue
            return "Timeout"
            
        except Exception as e:
            error_msg = str(e)[:100]
            log(f"   ❌ Exception: {error_msg}")
            if attempt < retries - 1:
                time.sleep(10)
                continue
            return f"Error: {error_msg}"
    
    return "Max retries exceeded"

def main():
    log(f"--- 🚀 STARTING ARCHIVER FOR r/{SUBREDDIT} ---")
    
    if not validate_subreddit():
        sys.exit(1)
    
    seen = load_seen()
    log(f"📋 Loaded {len(seen)} previously seen URLs")
    
    # Fetch RSS feed
    try:
        resp = requests.get(RSS_URL, headers=HEADERS, timeout=20)
        if resp.status_code == 429:
            log("❌ CRITICAL: Reddit 429 Too Many Requests. Rate limited.")
            sys.exit(1)
        if resp.status_code == 403:
            log("❌ CRITICAL: Reddit 403 Forbidden. Blocked by Reddit.")
            sys.exit(1)
        if resp.status_code != 200:
            log(f"❌ CRITICAL: Reddit returned status {resp.status_code}")
            sys.exit(1)
            
        feed = feedparser.parse(resp.text)
    except Exception as e:
        log(f"❌ CRITICAL: Could not fetch RSS. Error: {e}")
        sys.exit(1)
    
    if not feed.entries:
        log("⚠️ No posts found in feed.")
        return
    
    log(f"📊 Found {len(feed.entries)} posts in feed")
    
    # Process posts
    new_count = 0
    success_count = 0
    consecutive_523_errors = 0
    
    for entry in feed.entries:
        post_url = entry.link
        
        # Skip if already seen
        if post_url in seen:
            continue
        
        # Check limits
        if new_count >= MAX_POSTS_PER_RUN:
            log(f"⏸️ Reached limit of {MAX_POSTS_PER_RUN} posts per run.")
            break
        
        # Circuit breaker: Stop if too many 523 errors
        if consecutive_523_errors >= CIRCUIT_BREAKER_THRESHOLD:
            log(f"🛑 CIRCUIT BREAKER: {consecutive_523_errors} consecutive 523 errors. Wayback Machine likely down.")
            log(f"   Skipping remaining posts this run. Will retry next run.")
            break
        
        log(f"🆕 Processing: {post_url}")
        status = archive(post_url)
        new_count += 1
        
        if status == 200:
            log(f"   ✅ SUCCESS")
            append_seen(post_url)
            seen.add(post_url)
            success_count += 1
            consecutive_523_errors = 0  # Reset counter on success
            
        elif status == 523:
            consecutive_523_errors += 1
            log(f"   ⚠️ FAILED (523) - Consecutive failures: {consecutive_523_errors}/{CIRCUIT_BREAKER_THRESHOLD}")
            # Don't mark as seen - will retry next run
            log_failed(post_url, status, skip_archive=False)
            
        else:
            log(f"   ⚠️ FAILED ({status})")
            consecutive_523_errors = 0  # Reset on non-523 errors
            # Mark as seen to avoid infinite retries of genuinely broken URLs
            append_seen(post_url)
            seen.add(post_url)
            log_failed(post_url, status, skip_archive=True)
        
        # Sleep between requests
        if new_count < MAX_POSTS_PER_RUN:
            time.sleep(SLEEP_BETWEEN)
    
    # Summary
    log(f"")
    log(f"📊 RUN SUMMARY:")
    log(f"   - Processed: {new_count} new posts")
    log(f"   - Successful: {success_count}")
    log(f"   - Failed: {new_count - success_count}")
    log(f"   - Total archived: {len(seen)}")
    
    if consecutive_523_errors >= CIRCUIT_BREAKER_THRESHOLD:
        log(f"⚠️  Circuit breaker triggered - Wayback Machine appears to be down")
        # Exit with 0 so GitHub doesn't mark as failed - this is expected behavior
        sys.exit(0)

if __name__ == "__main__":
    main()
