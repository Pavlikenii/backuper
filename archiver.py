import feedparser
import requests
import time
import os

SUBREDDIT = "yoursubredditname"
RSS_URL = f"https://www.reddit.com/r/{SUBREDDIT}/new/.rss"
SEEN_FILE = "seen.txt"

HEADERS = {
    "User-Agent": "reddit-wayback-archiver/1.0"
}

def load_seen():
    if not os.path.exists(SEEN_FILE):
        return set()
    with open(SEEN_FILE, "r") as f:
        return set(line.strip() for line in f if line.strip())

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        f.write("\n".join(sorted(seen)))

def archive(url):
    r = requests.get(
        "https://web.archive.org/save/" + url,
        headers=HEADERS,
        timeout=30
    )
    return r.status_code

def main():
    seen = load_seen()
    feed = feedparser.parse(RSS_URL)

    for entry in feed.entries:
        post_id = entry.id
        post_url = entry.link

        if post_id in seen:
            continue

        try:
            status = archive(post_url)
            print(f"Archived {post_url} ({status})")
            seen.add(post_id)
            time.sleep(6)  # be nice to Wayback
        except Exception as e:
            print("Error:", e)

    save_seen(seen)

if __name__ == "__main__":
    main()
