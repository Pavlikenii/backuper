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

# 🕵️ USER AGENT (Prevents 429 Errors)
USER_AGENT = "Mozilla/5.0 (compatible; RedditWaybackArchiver/1.0; +https://github.com/)"
HEADERS = {"User-Agent": USER_AGENT}

# ⏳ OPTIMIZED TIMINGS
WAYBACK_TIMEOUT = 45        # Reduced to 45s (was 90s) - fail faster
SLEEP_BETWEEN = 8           # Reduced to 8s (was 12s) - archive faster
MAX_RETRIES = 2             # Only 2 retries (was 3) - fail faster
MAX_POSTS_PER_RUN = 10      # Limit posts per run to avoid going over 5 minutes
MAX_SEEN_ENTRIES = 10000    # Maximum entries in seen.txt before trimming

# ==========================================
# 🛠️ HELPER FUNCTIONS
# ==========================================
def log(msg):
    """Prints immediately to GitHub Actions logs with timestamp."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}", flush=True)

def validate_subreddit():
    """Validates subreddit name format."""
    if not SUBREDDIT:
        log("❌ ERROR: SUBREDDIT environment variable not set!")
        return False
    if not re.match(r'^[a-zA-Z0-9_]{3,21}$', SUBREDDIT):
        log(f"❌ ERROR: Invalid subreddit name format: '{SUBREDDIT}'")
        return False
    return True

def load_seen():
    """Loads the set of already archived URLs."""
    if not os.path.exists(SEEN_FILE):
        return set()
    
    with open(SEEN_FILE, "r") as f:
        lines = f.readlines()
    
    # Trim file if it's getting too large
    if len(lines) > MAX_SEEN_ENTRIES:
        recent = lines[-MAX_SEEN_ENTRIES:]
        log(f"📝 Trimming seen.txt from {len(lines)} to {MAX_SEEN_ENTRIES} entries")
        with open(SEEN_FILE, "w") as f:
            f.writelines(recent)
        lines = recent
    
    # Extract URLs from lines (handle both formats: just URL or "timestamp|url")
    seen_urls = set()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # If line contains |, split and take the URL part
        if '|' in line:
            parts = line.split('|')
            if len(parts) >= 2:
                seen_urls.add(parts[1])
        else:
            # Old format - just the URL
            seen_urls.add(line)
    
    return seen_urls

def append_seen(post_url):
    """Saves URL with timestamp to the file immediately (Safety feature)."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with open(SEEN_FILE, "a") as f:
        f.write(f"{timestamp}|{post_url}\n")

def log_failed(post_url, status):
    """Logs failed archives to a separate file for review."""
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with open(FAILED_FILE, "a") as f:
        f.write(f"{timestamp}|{post_url}|{status}\n")

def archive(url, retries=MAX_RETRIES):
    """Sends URL to Wayback Machine with quick retry logic."""
    for attempt in range(retries):
        wayback_url = f"https://web.archive.org/save/{url}"
        try:
            r = requests.get(wayback_url, headers=HEADERS, timeout=WAYBACK_TIMEOUT)
            
            # Handle rate limiting with shorter backoff
            if r.status_code == 429:
                if attempt < retries - 1:
                    wait = 15 + random.uniform(0, 10)
                    log(f"   ⏳ Rate limited. Waiting {wait:.1f}s...")
                    time.sleep(wait)
                    continue
                else:
                    return 429
            
            # Success!
            if r.status_code == 200:
                return 200
            
            # Other status codes
            return r.status_code
            
        except requests.exceptions.Timeout:
            if attempt < retries - 1:
                log(f"   ⏱️ Timeout. Retry {attempt + 2}/{retries}...")
                time.sleep(5)
                continue
            else:
                return "Timeout"
                
        except requests.exceptions.ConnectionError:
            if attempt < retries - 1:
                log(f"   🔌 Connection error. Retry {attempt + 2}/{retries}...")
                time.sleep(5)
                continue
            else:
                return "Connection Error"
                
        except Exception as e:
            if attempt < retries - 1:
                log(f"   ⚠️ Error. Retry {attempt + 2}/{retries}...")
                time.sleep(5)
                continue
            return f"Error: {str(e)[:50]}"
    
    return "Max retries exceeded"

# ==========================================
# 🚀 MAIN SCRIPT
# ==========================================
def main():
    log(f"--- 🚀 STARTING ARCHIVER FOR r/{SUBREDDIT} ---")
    
    # Validate configuration
    if not validate_subreddit():
        sys.exit(1)
    
    seen = load_seen()
    log(f"📚 Loaded {len(seen)} previously archived URLs.")
    log(f"📡 Fetching RSS Feed: {RSS_URL}")
    
    try:
        # Check if Reddit is blocking us first
        resp = requests.get(RSS_URL, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            log(f"❌ CRITICAL: Reddit returned {resp.status_code}. Possible rate limit or invalid subreddit.")
            sys.exit(1)
            
        feed = feedparser.parse(resp.text)
    except Exception as e:
        log(f"❌ CRITICAL: Could not fetch RSS. Error: {e}")
        sys.exit(1)
    
    if not feed.entries:
        log("⚠️ No posts found in feed. (Check subreddit name?)")
        log("--- 🏁 FINISHED. No new posts to archive. ---")
        return
    
    log(f"📊 Found {len(feed.entries)} posts in feed")
    new_count = 0
    failed_count = 0
    skipped_count = 0
    
    for entry in feed.entries:
        post_url = entry.link
        
        # Check if we already archived this URL
        if post_url in seen:
            skipped_count += 1
            continue
        
        # LIMIT: Stop if we've already processed MAX_POSTS_PER_RUN
        if new_count + failed_count >= MAX_POSTS_PER_RUN:
            log(f"⏸️ Reached limit of {MAX_POSTS_PER_RUN} posts per run. Remaining posts will be processed next time.")
            break
        
        log(f"🆕 Processing: {post_url}")
        
        # Attempt Archive
        status = archive(post_url)
        
        # LOGIC: Only mark as 'seen' if it actually worked (200 OK)
        if status == 200:
            log(f"   ✅ SUCCESS (200 OK)")
            append_seen(post_url)  # Save immediately to file with timestamp
            seen.add(post_url)     # Add to memory
            new_count += 1
        else:
            log(f"   ⚠️ FAILED ({status})")
            log_failed(post_url, status)
            failed_count += 1
            # We do NOT add to 'seen', so it tries again in next run
        
        # Sleep to prevent blocking
        time.sleep(SLEEP_BETWEEN)
    
    log(f"--- 🏁 FINISHED ---")
    log(f"   ✅ Successfully archived: {new_count}")
    log(f"   ⚠️ Failed attempts: {failed_count}")
    log(f"   ⏭️ Already archived (skipped): {skipped_count}")
    log(f"   📊 Total in seen.txt: {len(seen) + new_count}")
    
    # Always log completion time for visibility
    if new_count == 0 and failed_count == 0:
        log(f"   ℹ️ No new posts found - next check in 5 minutes")

if __name__ == "__main__":
    main()
