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

# 🔄 REDLIB INSTANCES (We try these in order)
REDLIB_INSTANCES = [
    "redlib.tux.pizza",          # Often reliable
    "redlib.catsarch.com",       # The one you were using
    "libreddit.freereddit.com",  # Backup
    "redlib.vsls.cz",            # Backup
    "redlib.ducks.party"         # Backup
]

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

def convert_to_redlib(url, instance):
    """
    Converts a standard URL to the specific Redlib instance we are using.
    """
    # Clean up input URL to base format
    clean = url.replace("www.reddit.com", "reddit.com") \
               .replace("old.reddit.com", "reddit.com")
    
    # If the URL is already from another Redlib instance, strip that domain
    for inst in REDLIB_INSTANCES:
        if inst in clean:
            clean = clean.replace(inst, "reddit.com")
            break
            
    # Apply new instance
    return clean.replace("reddit.com", instance)

def convert_to_standard(url):
    """
    Converts Redlib/Old URLs back to www.reddit.com for consistent deduplication
    """
    clean = url
    for inst in REDLIB_INSTANCES:
        clean = clean.replace(inst, "www.reddit.com")
        
    return clean.replace("old.reddit.com", "www.reddit.com") \
                .replace("reddit.com", "www.reddit.com") \
                .replace("www.www.", "www.")

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

def append_seen(original_url, archived_url, service):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with open(SEEN_FILE, "a") as f:
        f.write(f"{timestamp}|{original_url}|{archived_url}|{service}\n")

def log_failed(post_url, status):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with open(FAILED_FILE, "a") as f:
        f.write(f"{timestamp}|{post_url}|{status}\n")

# --- FETCH FEED LOGIC ---
def fetch_feed():
    """
    Tries multiple Redlib instances until one returns posts.
    Returns: (entries, working_instance_url)
    """
    for instance in REDLIB_INSTANCES:
        rss_url = f"https://{instance}/r/{SUBREDDIT}/new.rss"
        log(f"📡 Trying feed: {rss_url}")
        
        try:
            resp = requests.get(rss_url, headers=get_headers(), timeout=15)
            if resp.status_code != 200:
                log(f"   ⚠️ Status {resp.status_code}. Skipping...")
                continue
                
            feed = feedparser.parse(resp.text)
            
            if not feed.entries:
                log(f"   ⚠️ Feed returned 0 entries. Skipping...")
                continue
                
            log(f"   ✅ Success! Found {len(feed.entries)} posts.")
            return feed.entries, instance
            
        except Exception as e:
            log(f"   ⚠️ Connection failed: {e}")
            continue
            
    return None, None

# --- ARCHIVERS ---
def archive_wayback(url, instance):
    # Use the specific Redlib instance that we know is working
    target_url = convert_to_redlib(url, instance)
    wayback_url = f"https://web.archive.org/save/{target_url}"
    
    try:
        log(f"      [Wayback] Attempting via {instance}...")
        r = requests.get(wayback_url, headers=get_headers(), timeout=ARCHIVE_TIMEOUT)
        
        if r.status_code == 200:
            return (200, r.url) 
        return (r.status_code, None)
    except Exception as e:
        return (f"Error: {str(e)[:50]}", None)

def archive_ghost(url):
    try:
        log(f"      [GhostArchive] Attempting (Standard URL)...")
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

def archive_today(url):
    try:
        log(f"      [Archive.today] Attempting (Standard URL)...")
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

def archive_multi_service(url, instance):
    # 1. Wayback (using Redlib proxy)
    log(f"   🌐 Trying Wayback Machine...")
    status, result_url = archive_wayback(url, instance)
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
    
    if not validate_subreddit():
        sys.exit(1)
    
    seen = load_seen()
    
    # FETCH FEED with Failover
    entries, working_instance = fetch_feed()
    
    if not entries:
        log("❌ CRITICAL: No posts found on ANY Redlib instance.")
        sys.exit(1)
        
    log(f"⚙️ Using working instance: {working_instance}")
    
    new_count = 0
    consecutive_failures = 0
    
    for entry in entries:
        post_url = entry.link
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
        
        success, service, result_link = archive_multi_service(standard_url, working_instance)
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
