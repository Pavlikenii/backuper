import feedparser
import requests
import time
import os
import sys

# --- CONFIGURATION ---
SUBREDDIT = "boltedontits"  # <--- CHANGE THIS
RSS_URL = f"https://www.reddit.com/r/{SUBREDDIT}/new/.rss"
SEEN_FILE = "seen.txt"

# 🚀 BETTER USER AGENT
# This looks like a real browser to Wayback, but identifies politely to Reddit.
USER_AGENT = "Mozilla/5.0 (compatible; RedditWaybackArchiver/1.0; +https://github.com/)"
HEADERS = {"User-Agent": USER_AGENT}

# --- HELPER FUNCTIONS ---

def log(msg):
    """Prints immediately to the console (fixes the 'stuck yellow' issue)"""
    print(msg, flush=True)

def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        f.write("\n".join(sorted(seen)))

def archive(url):
    """Sends the URL to the Wayback Machine"""
    wayback_url = f"https://web.archive.org/save/{url}"
    try:
        r = requests.get(wayback_url, headers=HEADERS, timeout=30)
        return r.status_code
    except Exception as e:
        return f"Error: {e}"

# --- MAIN SCRIPT ---

def main():
    log(f"--- STARTING ARCHIVE JOB FOR r/{SUBREDDIT} ---")
    
    seen = load_seen()
    log(f"Loaded {len(seen)} previously archived posts.")

    log(f"Fetching RSS: {RSS_URL}")
    try:
        # We use requests first to check if Reddit is blocking us
        response = requests.get(RSS_URL, headers=HEADERS, timeout=20)
        if response.status_code != 200:
            log(f"❌ CRITICAL ERROR: Reddit returned status {response.status_code}")
            log("If this is 429, you are being rate limited. If 403, you are blocked.")
            sys.exit(1)
            
        feed = feedparser.parse(response.text)
    except Exception as e:
        log(f"❌ Failed to fetch RSS: {e}")
        sys.exit(1)

    if not feed.entries:
        log("⚠️ No entries found. Is the subreddit name correct?")
    
    new_count = 0
    
    for entry in feed.entries:
        post_id = entry.id
        post_url = entry.link

        if post_id in seen:
            continue
        
        log(f"🆕 Found new post: {post_url}")
        
        status = archive(post_url)
        
        if status == 200:
            log(f"   ✅ Archived successfully (200)")
            seen.add(post_id)
            new_count += 1
        else:
            log(f"   ⚠️ Wayback status: {status}")
            # We add it to 'seen' anyway so we don't retry a broken link forever
            seen.add(post_id) 

        # IMPORTANT: Sleep to avoid looking like a DDOS attack
        time.sleep(5)

    save_seen(seen)
    log(f"--- JOB FINISHED. Archived {new_count} new posts. ---")

if __name__ == "__main__":
    main()
