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
# Using .json often bypasses some RSS strictness, but .rss is standard
RSS_URL = f"https://www.reddit.com/r/{SUBREDDIT}/new/.rss"
SEEN_FILE = "seen.txt"
FAILED_FILE = "failed.txt"

# 🕵️ UPDATED USER AGENT (More unique to avoid blocks)
USER_AGENT = f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) WaybackArchiver/2.0 (github.com/action; r/{SUBREDDIT})"
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/rss+xml, application/xml, text/xml"
}

# ⏳ TIMINGS
WAYBACK_TIMEOUT = 45        
SLEEP_BETWEEN = 8           
MAX_RETRIES = 2             
MAX_POSTS_PER_RUN = 10      
MAX_SEEN_ENTRIES = 10000    

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

def log_failed(post_url, status):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with open(FAILED_FILE, "a") as f:
        f.write(f"{timestamp}|{post_url}|{status}\n")

def archive(url, retries=MAX_RETRIES):
    for attempt in range(retries):
        wayback_url = f"https://web.archive.org/save/{url}"
        try:
            r = requests.get(wayback_url, headers=HEADERS, timeout=WAYBACK_TIMEOUT)
            if r.status_code == 429:
                if attempt < retries - 1:
                    wait = 15 + random.uniform(0, 10)
                    log(f"   ⏳ Rate limited by Wayback. Waiting {wait:.1f}s...")
                    time.sleep(wait)
                    continue
                else: return 429
            if r.status_code == 200: return 200
            return r.status_code
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(5)
                continue
            return f"Error: {str(e)[:50]}"
    return "Max retries exceeded"

def main():
    log(f"--- 🚀 STARTING ARCHIVER FOR r/{SUBREDDIT} ---")
    
    if not validate_subreddit():
        sys.exit(1)
    
    seen = load_seen()
    
    try:
        # Check Reddit connection specifically
        resp = requests.get(RSS_URL, headers=HEADERS, timeout=20)
        if resp.status_code == 429:
            log("❌ CRITICAL: Reddit 429 Too Many Requests. You are being rate limited.")
            sys.exit(1)
        if resp.status_code == 403:
            log("❌ CRITICAL: Reddit 403 Forbidden. Reddit is blocking this User-Agent/IP.")
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
    new_count = 0
    
    for entry in feed.entries:
        post_url = entry.link
        if post_url in seen: continue
        
        if new_count >= MAX_POSTS_PER_RUN:
            log(f"⏸️ Reached limit of {MAX_POSTS_PER_RUN} posts.")
            break
        
        log(f"🆕 Processing: {post_url}")
        status = archive(post_url)
        
        if status == 200:
            log(f"   ✅ SUCCESS")
            append_seen(post_url)
            seen.add(post_url)
            new_count += 1
        else:
            log(f"   ⚠️ FAILED ({status})")
            log_failed(post_url, status)
        
        time.sleep(SLEEP_BETWEEN)

if __name__ == "__main__":
    main()
