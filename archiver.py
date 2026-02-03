import requests
import feedparser
import time
import os
import sys
import re
import random
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urljoin

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
SUBREDDIT = os.environ.get("SUBREDDIT", "boltedontits")
SEEN_FILE = "seen.txt"
FAILED_FILE = "failed.txt"

# 🔄 REDLIB INSTANCES (Updated list - verified working as of 2024-2025)
# Source: https://github.com/redlib-org/redlib-instances/blob/main/instances.md
REDLIB_INSTANCES = [
    "redlib.nohost.network",      # Germany
    "redlib.privacydev.net",      # France
    "redlib.perennialte.ch",      # Australia
    "redlib.drgns.space",         # United States
    "redlib.privacy.com.de",      # Germany
    "redlib.pussthecat.org",      # Germany
    "redlib.nadeko.net",          # Netherlands
    "redlib.baczek.me",           # Poland
    "redlib.hostux.net",          # France
    "rl.bloat.cat",               # Germany
    "redlib.private.coffee",      # Austria
]

# Fallback: Use Reddit's own JSON API with proper headers (no RSS)
REDDIT_JSON_URL = f"https://www.reddit.com/r/{SUBREDDIT}/new.json"

# Real browser headers
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.0.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]

def get_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/rss+xml,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1"
    }

# ⏳ TIMINGS
ARCHIVE_TIMEOUT = 60
SLEEP_BETWEEN = 20
MAX_POSTS_PER_RUN = 5
MAX_SEEN_ENTRIES = 5000
CIRCUIT_BREAKER_THRESHOLD = 5
MAX_RETRIES = 3
RETRY_DELAY = 5

def log(msg):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)

def validate_subreddit():
    if not SUBREDDIT:
        log("❌ ERROR: SUBREDDIT environment variable not set!")
        return False
    if not re.match(r'^[a-zA-Z0-9_]+$', SUBREDDIT):
        log(f"❌ ERROR: Invalid subreddit name: {SUBREDDIT}")
        return False
    return True

def convert_to_redlib(url, instance):
    """Converts a standard URL to the specific Redlib instance we are using."""
    clean = url.replace("www.reddit.com", "reddit.com").replace("old.reddit.com", "reddit.com")
    
    for inst in REDLIB_INSTANCES:
        if inst in clean:
            clean = clean.replace(inst, "reddit.com")
            break
            
    return clean.replace("reddit.com", instance)

def convert_to_standard(url):
    """Converts Redlib/Old URLs back to www.reddit.com for consistent deduplication"""
    clean = url
    for inst in REDLIB_INSTANCES:
        clean = clean.replace(inst, "www.reddit.com")
    
    return clean.replace("old.reddit.com", "www.reddit.com") \
                .replace("reddit.com", "www.reddit.com") \
                .replace("www.www.", "www.")

def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    
    try:
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
            if not line: 
                continue
            if '|' in line:
                parts = line.split('|')
                if len(parts) >= 2: 
                    seen_urls.add(parts[1])
            else:
                seen_urls.add(line)
        return seen_urls
    except Exception as e:
        log(f"⚠️ Error loading seen file: {e}")
        return set()

def append_seen(original_url, archived_url, service):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(SEEN_FILE, "a") as f:
            f.write(f"{timestamp}|{original_url}|{archived_url}|{service}\n")
    except Exception as e:
        log(f"⚠️ Error writing to seen file: {e}")

def log_failed(post_url, status):
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(FAILED_FILE, "a") as f:
            f.write(f"{timestamp}|{post_url}|{status}\n")
    except Exception as e:
        log(f"⚠️ Error writing to failed file: {e}")

# --- FETCH FEED LOGIC ---
def fetch_with_retry(url, headers, timeout=15, max_retries=MAX_RETRIES):
    """Fetch URL with exponential backoff retry logic"""
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            if resp.status_code == 200:
                return resp
            elif resp.status_code in [429, 502, 503, 504]:
                wait_time = RETRY_DELAY * (2 ** attempt) + random.uniform(0, 2)
                log(f"   ⏳ Rate limited/server error ({resp.status_code}), waiting {wait_time:.1f}s...")
                time.sleep(wait_time)
                continue
            else:
                return resp
        except requests.exceptions.Timeout:
            wait_time = RETRY_DELAY * (2 ** attempt)
            log(f"   ⏳ Timeout, retrying in {wait_time:.1f}s... (attempt {attempt+1}/{max_retries})")
            time.sleep(wait_time)
        except requests.exceptions.ConnectionError as e:
            wait_time = RETRY_DELAY * (2 ** attempt)
            log(f"   ⏳ Connection error, retrying in {wait_time:.1f}s... (attempt {attempt+1}/{max_retries})")
            time.sleep(wait_time)
        except Exception as e:
            log(f"   ⚠️ Unexpected error: {e}")
            return None
    
    return None

def fetch_from_reddit_json():
    """Fallback: Fetch directly from Reddit's JSON API"""
    log(f"📡 Trying Reddit JSON API: {REDDIT_JSON_URL}")
    
    headers = get_headers()
    headers['Accept'] = 'application/json'
    
    try:
        resp = fetch_with_retry(REDDIT_JSON_URL, headers, timeout=20)
        if not resp:
            return None, None
            
        if resp.status_code != 200:
            log(f"   ⚠️ Reddit API returned {resp.status_code}")
            return None, None
        
        data = resp.json()
        posts = data.get('data', {}).get('children', [])
        
        if not posts:
            log("   ⚠️ No posts found in JSON response")
            return None, None
        
        # Convert to feedparser-like entries
        entries = []
        for post in posts:
            post_data = post.get('data', {})
            entry = {
                'link': f"https://www.reddit.com{post_data.get('permalink', '')}",
                'title': post_data.get('title', 'No Title'),
                'published': datetime.utcfromtimestamp(post_data.get('created_utc', 0)).strftime('%Y-%m-%dT%H:%M:%SZ')
            }
            entries.append(type('Entry', (), entry)())
        
        log(f"   ✅ Success! Found {len(entries)} posts via JSON API")
        return entries, "reddit.com"
        
    except Exception as e:
        log(f"   ⚠️ Reddit JSON API failed: {e}")
        return None, None

def fetch_feed():
    """
    Tries multiple Redlib instances until one returns posts.
    Falls back to Reddit JSON API if all fail.
    Returns: (entries, working_instance_url)
    """
    # Shuffle instances to distribute load
    instances = REDLIB_INSTANCES.copy()
    random.shuffle(instances)
    
    for instance in instances:
        rss_url = f"https://{instance}/r/{SUBREDDIT}/new.rss"
        log(f"📡 Trying feed: {rss_url}")
        
        resp = fetch_with_retry(rss_url, get_headers(), timeout=15)
        
        if not resp:
            log(f"   ❌ Failed after retries")
            continue
            
        if resp.status_code != 200:
            log(f"   ⚠️ Status {resp.status_code}. Skipping...")
            continue
        
        try:
            feed = feedparser.parse(resp.text)
            
            if not feed.entries:
                log(f"   ⚠️ Feed returned 0 entries. Skipping...")
                continue
                
            log(f"   ✅ Success! Found {len(feed.entries)} posts.")
            return feed.entries, instance
            
        except Exception as e:
            log(f"   ⚠️ Feed parsing failed: {e}")
            continue
    
    # Fallback to Reddit's JSON API
    log("⚠️ All Redlib instances failed. Trying direct Reddit API...")
    return fetch_from_reddit_json()

# --- ARCHIVERS ---
def archive_wayback(url, instance):
    """Archive via Wayback Machine"""
    # For Reddit direct API fallback, use the URL as-is
    if instance == "reddit.com":
        target_url = url
    else:
        target_url = convert_to_redlib(url, instance)
    
    wayback_url = f"https://web.archive.org/save/{target_url}"
    
    try:
        log(f"      [Wayback] Attempting...")
        r = requests.get(wayback_url, headers=get_headers(), timeout=ARCHIVE_TIMEOUT, allow_redirects=True)
        
        if r.status_code == 200:
            # Check if we got a valid archive URL back
            if "web.archive.org/web/" in r.url:
                return (200, r.url)
            # Sometimes it redirects to the original if already archived
            return (200, r.url)
        elif r.status_code == 429:
            log(f"      ⚠️ Wayback rate limited")
            return (429, None)
        elif r.status_code == 523:
            log(f"      ⚠️ Wayback origin unreachable (523)")
            return (523, None)
        return (r.status_code, None)
    except requests.exceptions.Timeout:
        return ("Timeout", None)
    except Exception as e:
        return (f"Error: {str(e)[:50]}", None)

def archive_ghost(url):
    try:
        log(f"      [GhostArchive] Attempting...")
        real_url = convert_to_standard(url)
        # GhostArchive works better with POST
        r = requests.post(
            "https://ghostarchive.org/archive",
            data={'url': real_url},
            headers=get_headers(),
            timeout=60,
            allow_redirects=True
        )
        if r.status_code == 200 and "ghostarchive.org/archive/" in r.url:
            return (200, r.url)
        return (r.status_code, None)
    except Exception as e:
        return (f"Error: {str(e)[:50]}", None)

def archive_today(url):
    try:
        log(f"      [Archive.today] Attempting...")
        real_url = convert_to_standard(url)
        r = requests.post(
            "https://archive.ph/submit/",
            data={'url': real_url, 'anyway': '1'},
            headers=get_headers(),
            timeout=ARCHIVE_TIMEOUT,
            allow_redirects=True
        )
        if r.status_code == 200:
            if 'archive.ph' in r.url or 'archive.today' in r.url:
                return (200, r.url)
            # Sometimes returns 200 but needs to check for refresh meta
            if 'refresh' in r.text.lower():
                # Extract URL from meta refresh if present
                match = re.search(r'url=[\'"]?([^\'" >]+)', r.text)
                if match:
                    return (200, match.group(1))
        return (r.status_code, None)
    except Exception as e:
        return (f"Error: {str(e)[:50]}", None)

def archive_multi_service(url, instance):
    """Try multiple archive services"""
    results = []
    
    # 1. Wayback (using Redlib proxy or direct)
    log(f"   🌐 Trying Wayback Machine...")
    status, result_url = archive_wayback(url, instance)
    if status == 200:
        log(f"   ✅ Wayback SUCCESS: {result_url}")
        return (True, "wayback", result_url)
    results.append(f"WB:{status}")
    log(f"   ❌ Wayback failed: {status}")
    time.sleep(2)

    # 2. GhostArchive
    log(f"   👻 Trying GhostArchive...")
    status_ga, result_url_ga = archive_ghost(url)
    if status_ga == 200:
        log(f"   ✅ GhostArchive SUCCESS: {result_url_ga}")
        return (True, "ghostarchive", result_url_ga)
    results.append(f"GA:{status_ga}")
    log(f"   ❌ GhostArchive failed: {status_ga}")
    time.sleep(2)

    # 3. Archive.today
    log(f"   💾 Trying Archive.today...")
    status_at, result_url_at = archive_today(url)
    if status_at == 200:
        log(f"   ✅ Archive.today SUCCESS: {result_url_at}")
        return (True, "archive.today", result_url_at)
    results.append(f"AT:{status_at}")
    log(f"   ❌ Archive.today failed: {status_at}")

    return (False, "all_failed", "|".join(results))

def main():
    log(f"--- 🚀 STARTING ARCHIVER FOR r/{SUBREDDIT} ---")
    
    if not validate_subreddit():
        sys.exit(1)
    
    seen = load_seen()
    
    # FETCH FEED with Failover
    entries, working_instance = fetch_feed()
    
    if not entries:
        log("❌ CRITICAL: No posts found on ANY Redlib instance or Reddit API.")
        sys.exit(1)
    
    log(f"⚙️ Using source: {working_instance}")
    
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
    
    log(f"\n--- ✅ COMPLETED: Archived {new_count - consecutive_failures} new posts ---")

if __name__ == "__main__":
    main()
