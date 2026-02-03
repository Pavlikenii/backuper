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

USER_AGENT = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) MultiArchiveBot/1.0 (github.com/action; r/{SUBREDDIT})"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

# ⏳ TIMINGS
ARCHIVE_TIMEOUT = 45
SLEEP_BETWEEN = 15
MAX_RETRIES = 2  # Reduced since we try multiple services
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

def append_seen(post_url, service="unknown"):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with open(SEEN_FILE, "a") as f:
        f.write(f"{timestamp}|{post_url}|{service}\n")

def log_failed(post_url, status):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with open(FAILED_FILE, "a") as f:
        f.write(f"{timestamp}|{post_url}|{status}\n")

def archive_wayback(url, retries=MAX_RETRIES):
    """Try archiving with Wayback Machine"""
    for attempt in range(retries):
        wayback_url = f"https://web.archive.org/save/{url}"
        
        try:
            log(f"      [Wayback] Attempt {attempt + 1}/{retries}")
            r = requests.get(wayback_url, headers=HEADERS, timeout=ARCHIVE_TIMEOUT, allow_redirects=True)
            
            if r.status_code == 429:
                if attempt < retries - 1:
                    wait = 20 + random.uniform(0, 10)
                    log(f"      ⏳ Rate limited. Waiting {wait:.1f}s...")
                    time.sleep(wait)
                    continue
                return (429, None)
            
            if r.status_code == 200:
                return (200, r.url)
            
            if r.status_code == 523:
                log(f"      ⚠️  523 Origin Unreachable")
                return (523, None)  # Don't retry 523
            
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

def archive_today(url, retries=MAX_RETRIES):
    """Try archiving with Archive.today"""
    for attempt in range(retries):
        try:
            log(f"      [Archive.today] Attempt {attempt + 1}/{retries}")
            
            archive_submit_url = "https://archive.ph/submit/"
            
            payload = {
                'url': url,
                'anyway': '1'
            }
            
            headers = {
                'User-Agent': USER_AGENT,
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': 'https://archive.ph',
                'Referer': 'https://archive.ph/'
            }
            
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
                if attempt < retries - 1:
                    wait = 20 + random.uniform(0, 10)
                    log(f"      ⏳ Rate limited. Waiting {wait:.1f}s...")
                    time.sleep(wait)
                    continue
                return (429, None)
            
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

def archive_multi_service(url):
    """
    Try multiple archive services with fallback
    Returns: (success: bool, service: str, status: any)
    """
    
    # Try Wayback Machine first
    log(f"   🌐 Trying Wayback Machine...")
    status, archive_url = archive_wayback(url)
    
    if status == 200:
        log(f"   ✅ Wayback SUCCESS")
        log(f"   📦 {archive_url}")
        return (True, "wayback", status)
    
    log(f"   ❌ Wayback failed: {status}")
    
    # If Wayback fails, try Archive.today
    log(f"   🌐 Trying Archive.today...")
    time.sleep(3)  # Brief pause between services
    status2, archive_url2 = archive_today(url)
    
    if status2 == 200:
        log(f"   ✅ Archive.today SUCCESS")
        log(f"   📦 {archive_url2}")
        return (True, "archive.today", status2)
    
    log(f"   ❌ Archive.today failed: {status2}")
    
    # Both failed
    return (False, "all_failed", f"wayback:{status}|archive.today:{status2}")

def main():
    log(f"--- 🚀 STARTING MULTI-SERVICE ARCHIVER FOR r/{SUBREDDIT} ---")
    log(f"📍 Will try: Wayback Machine → Archive.today")
    
    if not validate_subreddit():
        sys.exit(1)
    
    seen = load_seen()
    log(f"📋 Loaded {len(seen)} previously seen URLs")
    
    # Fetch RSS feed
    try:
        resp = requests.get(RSS_URL, headers=HEADERS, timeout=20)
        if resp.status_code == 429:
            log("❌ CRITICAL: Reddit 429 Too Many Requests.")
            sys.exit(1)
        if resp.status_code == 403:
            log("❌ CRITICAL: Reddit 403 Forbidden.")
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
    consecutive_failures = 0
    
    for entry in feed.entries:
        post_url = entry.link
        
        if post_url in seen:
            continue
        
        if new_count >= MAX_POSTS_PER_RUN:
            log(f"⏸️ Reached limit of {MAX_POSTS_PER_RUN} posts per run.")
            break
        
        if consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
            log(f"🛑 CIRCUIT BREAKER: {consecutive_failures} consecutive failures.")
            log(f"   All archive services appear down. Will retry next run.")
            break
        
        log(f"")
        log(f"🆕 Processing: {post_url}")
        
        success, service, status = archive_multi_service(post_url)
        new_count += 1
        
        if success:
            log(f"   🎉 ARCHIVED via {service}")
            append_seen(post_url, service)
            seen.add(post_url)
            success_count += 1
            consecutive_failures = 0
            
        else:
            consecutive_failures += 1
            log(f"   ⚠️ ALL SERVICES FAILED ({status})")
            log(f"   Consecutive failures: {consecutive_failures}/{CIRCUIT_BREAKER_THRESHOLD}")
            log_failed(post_url, status)
        
        if new_count < MAX_POSTS_PER_RUN:
            time.sleep(SLEEP_BETWEEN)
    
    # Summary
    log(f"")
    log(f"📊 RUN SUMMARY:")
    log(f"   - Processed: {new_count} new posts")
    log(f"   - Successful: {success_count}")
    log(f"   - Failed: {new_count - success_count}")
    log(f"   - Total archived: {len(seen)}")
    
    if consecutive_failures >= CIRCUIT_BREAKER_THRESHOLD:
        log(f"⚠️  Circuit breaker triggered")
        sys.exit(0)

if __name__ == "__main__":
    main()
