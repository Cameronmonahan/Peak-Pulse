"""
Peak Exposure Media — Client Pulse
Fetches live Instagram data for every client in clients.json and writes data.json,
which the dashboard (index.html) reads directly.

HOW IT WORKS
------------
1. Uses a single Meta access token (stored as the IG_ACCESS_TOKEN secret) that
   belongs to you, the person who manages all the client Instagram accounts.
2. Calls /me/accounts to list every Facebook Page you (or the token's user/app)
   have access to, and reads each Page's connected Instagram Business Account.
3. Matches those accounts to the usernames in clients.json.
4. For each matched account, pulls: followers_count, recent media (for engagement
   rate + this week's posts), and compares today's follower count against the
   last stored snapshot in history.json to compute "new followers today".
5. Writes everything to data.json in the shape the dashboard expects.

RUN LOCALLY (for testing before wiring up GitHub Actions):
    export IG_ACCESS_TOKEN="your-long-lived-token"
    python3 scripts/fetch_instagram_data.py

REQUIREMENTS:
    pip install requests
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

import requests

GRAPH_API_VERSION = "v22.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENTS_PATH = os.path.join(ROOT, "clients.json")
DATA_PATH = os.path.join(ROOT, "data.json")
HISTORY_PATH = os.path.join(ROOT, "history.json")

MAX_HISTORY_DAYS = 21  # how many days of follower snapshots to retain per account


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def get_access_token():
    token = os.environ.get("IG_ACCESS_TOKEN")
    if not token:
        print("ERROR: IG_ACCESS_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    return token


def get_post_week_range(now=None):
    """Returns (start, end) datetimes for the current Thursday->Wednesday cycle, in UTC."""
    now = now or datetime.now(timezone.utc)
    # Monday=0 ... Sunday=6 in Python's weekday(); Thursday=3
    days_since_thursday = (now.weekday() - 3) % 7
    start = (now - timedelta(days=days_since_thursday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(days=7)
    return start, end


def list_connected_ig_accounts(token):
    """Walks /me/accounts (with pagination) and returns a dict of
    {ig_username_lower: ig_user_id} for every Page with a connected IG Business account."""
    accounts = {}
    url = f"{GRAPH_BASE}/me/accounts"
    params = {
        "fields": "name,instagram_business_account{username}",
        "access_token": token,
        "limit": 100,
    }

    while url:
        resp = requests.get(url, params=params)
        params = None  # only needed on first request; pagination URLs are already complete
        if resp.status_code != 200:
            print(f"WARNING: /me/accounts request failed: {resp.status_code} {resp.text}", file=sys.stderr)
            break
        payload = resp.json()
        for page in payload.get("data", []):
            ig = page.get("instagram_business_account")
            if ig and ig.get("username"):
                accounts[ig["username"].lower()] = ig["id"]
        url = payload.get("paging", {}).get("next")

    return accounts


def fetch_ig_account_data(ig_user_id, token, week_start):
    """Fetches follower count, engagement inputs, and this week's post timestamps
    for a single Instagram Business Account."""
    profile_resp = requests.get(
        f"{GRAPH_BASE}/{ig_user_id}",
        params={"fields": "followers_count,media_count", "access_token": token},
    )
    profile_resp.raise_for_status()
    profile = profile_resp.json()
    followers = profile.get("followers_count", 0)

    media_resp = requests.get(
        f"{GRAPH_BASE}/{ig_user_id}/media",
        params={
            "fields": "timestamp,like_count,comments_count",
            "limit": 30,
            "access_token": token,
        },
    )
    media_resp.raise_for_status()
    media_items = media_resp.json().get("data", [])

    post_dates_this_week = []
    engagement_samples = []

    for item in media_items:
        ts_raw = item.get("timestamp")
        if not ts_raw:
            continue
        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))

        if ts >= week_start:
            post_dates_this_week.append(ts.isoformat())

        likes = item.get("like_count", 0) or 0
        comments = item.get("comments_count", 0) or 0
        engagement_samples.append(likes + comments)

    # Engagement rate approximation: avg(likes+comments) on recent posts / followers.
    # NOTE: this is the standard proxy metric agencies use without impressions/reach
    # access. If you want *true* engagement rate (based on reach), request the
    # `reach` field via the Insights API per media item — it requires extra
    # permissions and a slower per-post call, so it's left out of this default script.
    if engagement_samples and followers:
        avg_engagement = sum(engagement_samples) / len(engagement_samples)
        engagement_rate = (avg_engagement / followers) * 100
    else:
        engagement_rate = 0.0

    return {
        "followers": followers,
        "engagementRate": round(engagement_rate, 2),
        "postDates": post_dates_this_week,
    }


def compute_delta(history, handle, today_key, followers_today):
    """Compares today's follower count to the most recent *previous* day's
    snapshot for this handle."""
    entries = history.get(handle, [])
    previous = [e for e in entries if e["date"] != today_key]
    if not previous:
        return 0
    previous.sort(key=lambda e: e["date"])
    return followers_today - previous[-1]["followers"]


def update_history(history, handle, today_key, followers_today):
    entries = history.setdefault(handle, [])
    entries[:] = [e for e in entries if e["date"] != today_key]
    entries.append({"date": today_key, "followers": followers_today})
    entries.sort(key=lambda e: e["date"])
    if len(entries) > MAX_HISTORY_DAYS:
        del entries[: len(entries) - MAX_HISTORY_DAYS]


def main():
    token = get_access_token()
    clients = load_json(CLIENTS_PATH, [])
    history = load_json(HISTORY_PATH, {})

    now = datetime.now(timezone.utc)
    today_key = now.strftime("%Y-%m-%d")
    week_start, week_end = get_post_week_range(now)

    print("Looking up connected Instagram accounts...")
    connected = list_connected_ig_accounts(token)
    print(f"Found {len(connected)} connected Instagram Business accounts.")

    results = {}

    for client in clients:
        handle = client["handle"]
        name = client["name"]
        ig_user_id = connected.get(handle.lower())

        if not ig_user_id:
            print(f"  ! {name} (@{handle}) — not found among connected accounts. Skipping.", file=sys.stderr)
            results[handle] = {
                "name": name,
                "handle": handle,
                "error": "Account not found. Make sure it's connected as an Instagram "
                         "Business/Creator account linked to a Facebook Page this token can access.",
            }
            continue

        try:
            account_data = fetch_ig_account_data(ig_user_id, token, week_start)
        except requests.HTTPError as e:
            print(f"  ! {name} (@{handle}) — API error: {e}", file=sys.stderr)
            results[handle] = {"name": name, "handle": handle, "error": str(e)}
            continue

        delta = compute_delta(history, handle, today_key, account_data["followers"])
        update_history(history, handle, today_key, account_data["followers"])

        results[handle] = {
            "name": name,
            "handle": handle,
            "followers": account_data["followers"],
            "deltaToday": delta,
            "engagementRate": account_data["engagementRate"],
            "postDates": account_data["postDates"],
        }
        print(f"  ok {name} (@{handle}) — {account_data['followers']} followers, "
              f"{delta:+d} today, {len(account_data['postDates'])} posts this week")

    output = {
        "generatedAt": now.isoformat(),
        "weekStart": week_start.isoformat(),
        "weekEnd": week_end.isoformat(),
        "accounts": results,
    }

    save_json(DATA_PATH, output)
    save_json(HISTORY_PATH, history)
    print(f"\nWrote {DATA_PATH} and updated {HISTORY_PATH}.")


if __name__ == "__main__":
    main()
