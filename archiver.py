import requests
import feedparser
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
SEEN_FILE = "seen.txt"
FAILED_FILE = "failed.txt"

# 🔗 REDLIB INSTANCE (Proxy to bypass 403 blocks & Age Gates)
# If this one goes down, try: "redlib.tux.pizza" or "libreddit.freereddit.com"
REDLIB_INSTANCE = "redlib.catsarch.com" 

# 📡 RSS FEED (Using Redlib to avoid Reddit 403 bans)
RSS_URL = f"https://{REDLIB_INSTANCE}/r/{SUBREDDIT}/new.rss"

# Real browser headers
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9"
    }

# ⏳ TIMINGS
ARCHIVE_TIMEOUT = 60
SLEEP_BETWEEN = 20
MAX_POSTS_PER_RUN = 5
MAX_SEEN_ENTRIES = 5000
CIRCUIT_BREAKER_THRESHOLD = 5

def log(msg):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)

def validate_subreddit():
    if not SUBREDDIT:
        log("❌ ERROR: SUBREDDIT environment variable not set!")
        return False
    return True

def convert_to_redlib(url):
    """
    Converts reddit.com -> redlib
    1. Bypasses 429/403 blocks on scraping
    2. Bypasses 'You must be 18+' age gate for Archiving
    """
    clean = url.replace("www.reddit.com", "reddit.com").replace("old.reddit.com", "reddit.com")
    # Handle cases where the input is already a Redlib URL (from the RSS)
    if REDLIB_INSTANCE in clean:
        return clean
    return clean.replace("reddit.com", REDLIB_INSTANCE)

def convert_to_standard(url):
    """
    Converts Redlib/Old URLs back to www.reddit.com for consistent deduplication
    """
    return url.replace(REDLIB_INSTANCE, "www.reddit.com") \
              .replace("old.reddit.com", "www.reddit.com") \
              .replace("reddit.com", "www.reddit.com") \
              .replace("www.www.", "www.") # Cleanup

def load_seen():
    """
    Loads previously seen URLs.
    Checks column #2 (Original URL) to prevent duplicates.
    """
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
            # Check 2nd column (Original URL)
            if len(parts) >= 2: seen_urls.add(parts[1])
        else:
            seen_urls.add(line)
    return seen_urls

def append_seen(original_url, archived_url, service):
    """
    Saves: TIME | ORIGINAL URL | ARCHIVED URL | SERVICE
    """
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with open(SEEN_FILE, "a") as f:
        f.write(f"{timestamp}|{original_url}|{archived_url}|{service}\n")

def log_failed(post_url, status):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with open(FAILED_FILE, "a") as f:
        f.write(f"{timestamp}|{post_url}|{status}\n")

# --- SERVICE 1: WAYBACK MACHINE ---
def archive_wayback(url):
    # Ensure we use Redlib URL for archiving to bypass NSFW gate
    target_url = convert_to_redlib(url)
    wayback_url = f"https://web.archive.org/save/{target_url}"
    
    try:
        log(f"      [Wayback] Attempting via Redlib proxy...")
        r = requests.get(wayback_url, headers=get_headers(), timeout=ARCHIVE_TIMEOUT)
        
        if r.status_code == 200:
            return (200, r.url) 
        return (r.status_code, None)
    except Exception as e:
        return (f"Error: {str(e)[:50]}", None)

# --- SERVICE 2: GHOSTARCHIVE ---
def archive_ghost(url):
    try:
        log(f"      [GhostArchive] Attempting...")
        # GhostArchive often works better with the Real Reddit URL
        real_url = convert_to_standard(url)
        
        r = requests.post(
            "https://ghostarchive.org/archive", 
            data={'url': real_url}, 
            headers=get_headers(), 
            timeout=60
        )
        if r.status_code == 200 and "ghostarchive.org/archive/" in r.url:
            return (200, r.url)
        return (r.status_code, None)
    except Exception as e:
        return (f"Error: {str(e)[:50]}", None)

# --- SERVICE 3: ARCHIVE.TODAY ---
def archive_today(url):
    try:
        log(f"      [Archive.today] Attempting...")
        # Archive.today works with Real URL
        real_url = convert_to_standard(url)
        
        r = requests.post(
            "https://archive.ph/submit/",
            data={'url': real_url, 'anyway': '1'},
            headers=get_headers(),
            timeout=ARCHIVE_TIMEOUT
        )
        if r.status_code == 200 and 'archive.ph' in r.url:
            return (200, r.url)
        return (r.status_code, None)
    except Exception as e:
        return (f"Error: {str(e)[:50]}", None)

def archive_multi_service(url):
    # 1. Wayback
    log(f"   🌐 Trying Wayback Machine...")
    status, result_url = archive_wayback(url)
    if status == 200:
        log(f"   ✅ Wayback SUCCESS: {result_url}")
        return (True, "wayback", result_url)
    
    log(f"   ❌ Wayback failed: {status}")
    time.sleep(3)

    # 2. GhostArchive
    log(f"   👻 Trying GhostArchive...")
    status_ga, result_url_ga = archive_ghost(url)
    if status_ga == 200:
        log(f"   ✅ GhostArchive SUCCESS: {result_url_ga}")
        return (True, "ghostarchive", result_url_ga)
    
    log(f"   ❌ GhostArchive failed: {status_ga}")
    time.sleep(3)

    # 3. Archive.today
    log(f"   💾 Trying Archive.today...")
    status_at, result_url_at = archive_today(url)
    if status_at == 200:
        log(f"   ✅ Archive.today SUCCESS: {result_url_at}")
        return (True, "archive.today", result_url_at)
    
    log(f"   ❌ Archive.today failed: {status_at}")

    return (False, "all_failed", f"WB:{status}|GA:{status_ga}|AT:{status_at}")

def main():
    log(f"--- 🚀 STARTING ARCHIVER FOR r/{SUBREDDIT} ---")
    log(f"📡 Source: {RSS_URL}")
    
    if not validate_subreddit():
        sys.exit(1)
    
    seen = load_seen()
    
    try:
        # Fetch RSS from Redlib
        resp = requests.get(RSS_URL, headers=get_headers(), timeout=30)
        
        # If the main Redlib instance fails, fail gracefully (or you could add logic to try another)
        if resp.status_code != 200:
            log(f"❌ CRITICAL: Redlib RSS returned status {resp.status_code}")
            sys.exit(1)
            
        feed = feedparser.parse(resp.text)
    except Exception as e:
        log(f"❌ CRITICAL: RSS Parse Error: {e}")
        sys.exit(1)
    
    if not feed.entries:
        log("⚠️ No posts found in feed (feed might be empty or restricted).")
        return
    
    log(f"📊 Found {len(feed.entries)} posts.")
    
    new_count = 0
    consecutive_failures = 0
    
    for entry in feed.entries:
        post_url = entry.link
        
        # Convert link to standard reddit format for deduplication checking
        standard_url = convert_to_standard(post_url)
        
        if standard_url in seen:
            continue
            
        if new_count >= MAX_POSTS_PER_RUN:
            log(f"⏸️ Limit reached ({MAX_POSTS_PER_RUN}). Stopping.")
            break
            
        if consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            log(f"🛑 Too many failures. Stopping run.")
            break
            
        log(f"\n🆕 Processing: {standard_url}")
        
        # We pass the standard URL to the archiver, it will handle conversion internally
        success, service, result_link = archive_multi_service(standard_url)
        new_count += 1
        
        if success:
            append_seen(standard_url, result_link, service)
            seen.add(standard_url)
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            log_failed(standard_url, result_link)
        
        if new_count < MAX_POSTS_PER_RUN:
            time.sleep(SLEEP_BETWEEN + random.uniform(1, 5))

if __name__ == "__main__":
    main()
