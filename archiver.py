import feedparser
import requests
import time
import os
import sys

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
SUBREDDIT = "boltedontits"  # <--- CHANGE THIS TO YOUR SUBREDDIT
RSS_URL = f"https://www.reddit.com/r/{SUBREDDIT}/new/.rss"
SEEN_FILE = "seen.txt"

# 🕵️ USER AGENT (Prevents 429 Errors)
USER_AGENT = "Mozilla/5.0 (compatible; RedditWaybackArchiver/1.0; +https://github.com/)"
HEADERS = {"User-Agent": USER_AGENT}

# ⏳ TIMINGS (Tuned for slow Wayback Machine)
WAYBACK_TIMEOUT = 60    # Wait up to 60 seconds for a response
SLEEP_BETWEEN = 10      # Wait 10 seconds between posts (politeness)

# ==========================================
# 🛠️ HELPER FUNCTIONS
# ==========================================

def log(msg):
    """Prints immediately to GitHub Actions logs."""
    print(msg, flush=True)

def load_seen():
    """Loads the list of already archived post IDs."""
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def append_seen(post_id):
    """Saves a single ID to the file immediately (Safety feature)."""
    with open(SEEN_FILE, "a") as f:
        f.write(post_id + "\n")

def archive(url):
    """Sends URL to Wayback Machine and returns status code."""
    wayback_url = f"https://web.archive.org/save/{url}"
    try:
        r = requests.get(wayback_url, headers=HEADERS, timeout=WAYBACK_TIMEOUT)
        return r.status_code
    except Exception as e:
        return f"Error: {e}"

# ==========================================
# 🚀 MAIN SCRIPT
# ==========================================

def main():
    log(f"--- 🚀 STARTING ARCHIVER FOR r/{SUBREDDIT} ---")
    
    seen = load_seen()
    log(f"📚 Loaded {len(seen)} previously archived posts.")

    log(f"📡 Fetching RSS Feed: {RSS_URL}")
    try:
        # Check if Reddit is blocking us first
        resp = requests.get(RSS_URL, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            log(f"❌ CRITICAL: Reddit returned {resp.status_code}. Possible rate limit.")
            sys.exit(1)
            
        feed = feedparser.parse(resp.text)
    except Exception as e:
        log(f"❌ CRITICAL: Could not fetch RSS. Error: {e}")
        sys.exit(1)

    if not feed.entries:
        log("⚠️ No posts found. (Check subreddit name?)")
    
    new_count = 0
    
    for entry in feed.entries:
        post_id = entry.id
        post_url = entry.link

        # Check if we already did this one
        if post_id in seen:
            continue
        
        log(f"🆕 Processing: {post_url}")
        
        # Attempt Archive
        status = archive(post_url)
        
        # LOGIC: Only mark as 'seen' if it actually worked (200 OK)
        if status == 200:
            log(f"   ✅ SUCCESS (200 OK)")
            append_seen(post_id)  # Save immediately to file
            seen.add(post_id)     # Add to memory
            new_count += 1
        else:
            log(f"   ⚠️ FAILED ({status}) - Will retry next run.")
            # We do NOT add to 'seen', so it tries again in 10 mins

        # Sleep to prevent blocking
        time.sleep(SLEEP_BETWEEN)

    log(f"--- 🏁 FINISHED. Successfully archived {new_count} new posts. ---")

if __name__ == "__main__":
    main()
