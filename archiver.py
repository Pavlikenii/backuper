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
RSS_URL = f"https://www.reddit.com/r/{SUBREDDIT}/new/.rss"
SEEN_FILE = "seen.txt"
FAILED_FILE = "failed.txt"

# Random User Agents to avoid simple fingerprinting
USER_AGENTS = [
    f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36",
    f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/92.0.4515.107 Safari/537.36"
]

def get_random_header():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }

# ⏳ TIMINGS
ARCHIVE_TIMEOUT = 60
SLEEP_BETWEEN = 20
MAX_RETRIES = 2
MAX_POSTS_PER_RUN = 5
MAX_SEEN_ENTRIES = 10000
CIRCUIT_BREAKER_THRESHOLD = 5

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

def convert_to_old_reddit(url):
    """
    Wayback Machine often fails on www.reddit.com (Error 523) due to 
    bloat/blocking. old.reddit.com is static HTML and archives much better.
    """
    return url.replace("www.reddit.com", "old.reddit.com").replace("//reddit.com", "//old.reddit.com")

def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r") as f:
        lines = f.readlines()
    # Prune file if too large
    if len(lines) > MAX_SEEN_ENTRIES:
        recent = lines[-MAX_SEEN_ENTRIES:]
        with open(SEEN_FILE, "w") as f:
            f.writelines(recent)
        lines = recent
    
    seen_urls = set()
    for line in lines:
        line = line.strip()
        if not line: continue
        # Handle both formats: "url" OR "date|url|service"
        if '|' in line:
            parts = line.split('|')
            if len(parts) >= 2: seen_urls.add(parts[1])
        else:
            seen_urls.add(line)
    return seen_urls

def append_seen(post_url, service="unknown"):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    # Always store the original URL (www) in seen.txt so we don't re-process
    # even if we archived the 'old.' version
    clean_url = post_url.replace("old.reddit.com", "www.reddit.com")
    with open(SEEN_FILE, "a") as f:
        f.write(f"{timestamp}|{clean_url}|{service}\n")

def log_failed(post_url, status):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with open(FAILED_FILE, "a") as f:
        f.write(f"{timestamp}|{post_url}|{status}\n")

def archive_wayback(url, retries=MAX_RETRIES):
    """Try archiving with Wayback Machine using old.reddit.com"""
    # Force Old Reddit for Wayback
    target_url = convert_to_old_reddit(url)
    
    for attempt in range(retries):
        wayback_url = f"https://web.archive.org/save/{target_url}"
        
        try:
            log(f"      [Wayback] Attempt {attempt + 1}/{retries} (using old.reddit)")
            r = requests.get(wayback_url, headers=get_random_header(), timeout=ARCHIVE_TIMEOUT)
            
            if r.status_code == 429:
                if attempt < retries - 1:
                    wait = 30 + random.uniform(0, 15)
                    log(f"      ⏳ Rate limited. Waiting {wait:.1f}s...")
                    time.sleep(wait)
                    continue
                return (429, None)
            
            if r.status_code == 200:
                return (200, r.url)
            
            # 523 = Origin Unreachable (Reddit blocking Wayback)
            # 520 = Web Server Returned an Unknown Error
            if r.status_code in [523, 520, 522]:
                log(f"      ⚠️  {r.status_code} Error (Wayback can't reach Reddit)")
                return (r.status_code, None) 
            
            if attempt < retries - 1:
                time.sleep(5)
                continue
            return (r.status_code, None)
            
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5)
                continue
            return (f"Error: {str(e)[:50]}", None)
    
    return ("Max retries", None)

def archive_ghost(url):
    """Fallback: GhostArchive.org"""
    try:
        log(f"      [GhostArchive] Attempting...")
        submit_url = "https://ghostarchive.org/archive"
        payload = {'url': url}
        
        # GhostArchive often redirects to the result
        r = requests.post(
            submit_url, 
            data=payload, 
            headers=get_random_header(), 
            timeout=60,
            allow_redirects=True
        )
        
        if r.status_code == 200 and "ghostarchive.org/archive/" in r.url:
            return (200, r.url)
        
        return (r.status_code, None)
    except Exception as e:
        return (f"Error: {str(e)[:50]}", None)

def archive_today(url):
    """Try archiving with Archive.today"""
    # Archive.today is very strict with GitHub IPs. We only try once to avoid 
    # extended IP bans.
    try:
        log(f"      [Archive.today] Attempting (Single attempt)...")
        
        archive_submit_url = "https://archive.ph/submit/"
        payload = {'url': url, 'anyway': '1'}
        headers = get_random_header()
        headers['Content-Type'] = 'application/x-www-form-urlencoded'
        headers['Origin'] = 'https://archive.ph'
        headers['Referer'] = 'https://archive.ph/'
        
        r = requests.post(
            archive_submit_url,
            data=payload,
            headers=headers,
            timeout=ARCHIVE_TIMEOUT,
            allow_redirects=True
        )
        
        if r.status_code == 200 and 'archive.ph' in r.url:
            return (200, r.url)
        
        if r.status_code == 429:
            log(f"      ⛔ Archive.today 429 (IP Rate Limited)")
            return (429, None)
            
        return (r.status_code, None)
        
    except Exception as e:
        return (f"Error: {str(e)[:50]}", None)

def archive_multi_service(url):
    """
    Strategy: 
    1. Wayback (using old.reddit.com to bypass 523)
    2. GhostArchive (Backup)
    3. Archive.today (Last resort, high fail rate on GitHub)
    """
    
    # 1. Try Wayback Machine
    log(f"   🌐 Trying Wayback Machine...")
    status, archive_url = archive_wayback(url)
    if status == 200:
        log(f"   ✅ Wayback SUCCESS")
        log(f"   📦 {archive_url}")
        return (True, "wayback", status)
    
    log(f"   ❌ Wayback failed: {status}")
    time.sleep(3) 

    # 2. Try GhostArchive (New addition)
    log(f"   👻 Trying GhostArchive...")
    status_ghost, ghost_url = archive_ghost(url)
    if status_ghost == 200:
        log(f"   ✅ GhostArchive SUCCESS")
        log(f"   📦 {ghost_url}")
        return (True, "ghostarchive", status_ghost)
    
    log(f"   ❌ GhostArchive failed: {status_ghost}")
    time.sleep(3)

    # 3. Try Archive.today
    log(f"   🌐 Trying Archive.today...")
    status2, archive_url2 = archive_today(url)
    if status2 == 200:
        log(f"   ✅ Archive.today SUCCESS")
        log(f"   📦 {archive_url2}")
        return (True, "archive.today", status2)
    
    log(f"   ❌ Archive.today failed: {status2}")
    
    # All failed
    return (False, "all_failed", f"WB:{status}|GA:{status_ghost}|AT:{status2}")

def main():
    log(f"--- 🚀 STARTING MULTI-SERVICE ARCHIVER FOR r/{SUBREDDIT} ---")
    log(f"📍 Strategy: Old.Reddit via Wayback -> GhostArchive -> Archive.today")
    
    if not validate_subreddit():
        sys.exit(1)
    
    seen = load_seen()
    log(f"📋 Loaded {len(seen)} previously seen URLs")
    
    # Fetch RSS feed
    try:
        # Use headers to avoid generic python-requests user agent
        resp = requests.get(RSS_URL, headers=get_random_header(), timeout=20)
        if resp.status_code != 200:
            log(f"❌ CRITICAL: Reddit RSS returned status {resp.status_code}")
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
    consecutive_failures = 0
    
    for entry in feed.entries:
        post_url = entry.link
        
        # Normalize URL for comparison (remove old. if present, ensure www.)
        compare_url = post_url.replace("old.reddit.com", "www.reddit.com")
        
        if compare_url in seen:
            continue
        
        if new_count >= MAX_POSTS_PER_RUN:
            log(f"⏸️ Reached limit of {MAX_POSTS_PER_RUN} posts per run.")
            break
        
        # Circuit breaker
        if consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            log(f"🛑 CIRCUIT BREAKER: {consecutive_failures} consecutive failures.")
            log(f"   All archive services appear down/blocking. Stopping run.")
            break
        
        log(f"")
        log(f"🆕 Processing: {compare_url}")
        
        success, service, status = archive_multi_service(compare_url)
        new_count += 1
        
        if success:
            log(f"   🎉 ARCHIVED via {service}")
            append_seen(compare_url, service)
            seen.add(compare_url)
            success_count += 1
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            log(f"   ⚠️ ALL SERVICES FAILED ({status})")
            log_failed(compare_url, status)
        
        if new_count < MAX_POSTS_PER_RUN:
            # Random sleep to look more human
            sleep_time = SLEEP_BETWEEN + random.uniform(2, 10)
            log(f"   💤 Sleeping {sleep_time:.1f}s...")
            time.sleep(sleep_time)
    
    log(f"")
    log(f"📊 RUN SUMMARY: {success_count}/{new_count} successful.")
    
    if consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
        sys.exit(1) # Fail the job so we see the red X in GitHub

if __name__ == "__main__":
    main()
