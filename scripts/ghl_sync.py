"""
Peak Exposure Media — GoHighLevel Sync
-----------------------------------------------------
Pulls real Instagram data from GoHighLevel's Social Planner API and writes it
into the same daily.json / weekly.json / data.json files that entry.html
writes by hand — so the dashboard itself needs zero changes.

How it works:
1. Calls GHL's "Get Accounts" to find every connected Instagram profile.
2. Matches each one to a client in clients.json by comparing the Instagram
   username to the "handle" field (they're expected to match exactly).
3. For each matched client, pulls a rolling window of daily statistics
   (posts + follower change per day) and reconstructs absolute follower
   counts by adding those daily changes onto the most recent known baseline
   already sitting in daily.json (GHL's API only ever reports *changes*,
   never a running total).
4. Pulls this week's totals (likes/comments/posts) to auto-calculate the
   weekly engagement rate, using the same formula entry.html uses.
5. Rebuilds data.json's "this week" snapshot from the updated daily/weekly
   data, exactly like entry.html's save function does.

Clients with no matching GHL Instagram account (e.g. TikTok-only, or not
yet connected in GHL) are left untouched — nothing about them gets erased,
they simply aren't updated by this script.

Run via GitHub Actions (see .github/workflows/ghl-sync.yml) or locally with:
    export GHL_PRIVATE_TOKEN="pit-..."
    export GHL_LOCATION_ID="NGAudo89KVk2PSD1a7Ol"
    python3 scripts/ghl_sync.py
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

BASE = "https://services.leadconnectorhq.com"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CLIENTS_PATH = os.path.join(ROOT, "clients.json")
DAILY_PATH = os.path.join(ROOT, "daily.json")
WEEKLY_PATH = os.path.join(ROOT, "weekly.json")
DATA_PATH = os.path.join(ROOT, "data.json")

# How many days back to re-check each run. Wider than 1 day so a missed run
# (workflow failure, GHL outage, etc.) doesn't leave a permanent gap.
SYNC_WINDOW_DAYS = 8

MAX_DAILY_ENTRIES = 400
MAX_WEEKLY_ENTRIES = 60


def load_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def get_env(name):
    val = os.environ.get(name)
    if not val:
        print(f"ERROR: {name} environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    return val


def get_post_week_range(now):
    """Thursday -> Wednesday cycle, matching entry.html's logic exactly."""
    days_since_thursday = (now.weekday() - 3) % 7  # Monday=0 ... Thursday=3
    start = (now - timedelta(days=days_since_thursday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    end = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return start, end


def fetch_instagram_accounts(headers, location_id):
    """Returns {instagram_username: profileId} for every connected IG account."""
    resp = requests.get(
        f"{BASE}/social-media-posting/{location_id}/accounts",
        headers=headers,
    )
    resp.raise_for_status()
    accounts = resp.json().get("results", {}).get("accounts", [])
    return {
        a["name"]: a["profileId"]
        for a in accounts
        if a.get("platform") == "instagram"
    }


def fetch_statistics(headers, location_id, profile_id, start_date, end_date):
    body = {
        "profileIds": [profile_id],
        "currentRange": {
            "startDate": start_date.strftime("%Y-%m-%dT00:00:00.000Z"),
            "endDate": end_date.strftime("%Y-%m-%dT23:59:59.999Z"),
        },
    }
    resp = requests.post(
        f"{BASE}/social-media-posting/statistics",
        headers={**headers, "Content-Type": "application/json"},
        params={"locationId": location_id},
        json=body,
    )
    resp.raise_for_status()
    return resp.json().get("results", {})


def upsert_daily(daily_data, handle, date_key, fields):
    """Same merge logic as entry.html: 'posted' merges as OR, never
    downgrading an existing True to False."""
    arr = daily_data.setdefault(handle, [])
    idx = next((i for i, e in enumerate(arr) if e["date"] == date_key), None)
    if idx is not None:
        merged = {**arr[idx], **fields}
        if "posted" in fields:
            merged["posted"] = bool(arr[idx].get("posted") or fields["posted"])
        arr[idx] = merged
    else:
        arr.append({"date": date_key, **fields})
    arr.sort(key=lambda e: e["date"])
    if len(arr) > MAX_DAILY_ENTRIES:
        del arr[: len(arr) - MAX_DAILY_ENTRIES]


def sync_client(handle, name, profile_id, headers, location_id, daily_data, weekly_data, now):
    print(f"\n--- {name} (@{handle}) ---")

    window_start = now - timedelta(days=SYNC_WINDOW_DAYS)
    stats = fetch_statistics(headers, location_id, profile_id, window_start, now)

    posts_series = stats.get("postPerformance", {}).get("posts", {}).get("instagram", [])
    followers_series = stats.get("platformTotals", {}).get("followers", {}).get("instagram", {}).get("series", [])

    if not posts_series and not followers_series:
        print("  No data returned for this window — skipping.")
        return

    num_days = max(len(posts_series), len(followers_series))
    day_dates = [(window_start + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(num_days)]

    # Find a follower baseline: the most recent existing entry strictly
    # before this sync window, so we can turn GHL's daily deltas into a
    # running absolute count.
    existing_entries = [e for e in daily_data.get(handle, []) if typeof_followers(e)]
    existing_entries = [e for e in existing_entries if e["date"] < day_dates[0]]
    existing_entries.sort(key=lambda e: e["date"])
    baseline = existing_entries[-1]["followers"] if existing_entries else None

    if baseline is None:
        print("  ⚠ No prior follower count found before this window — "
              "will record posts, but can't compute absolute follower counts yet.")

    running_total = baseline

    for i, date_key in enumerate(day_dates):
        posts_today = posts_series[i] if i < len(posts_series) else 0
        delta_today = followers_series[i] if i < len(followers_series) else 0

        fields = {"posted": posts_today > 0}
        if running_total is not None:
            running_total += delta_today
            fields["followers"] = running_total

        upsert_daily(daily_data, handle, date_key, fields)

    print(f"  Synced {num_days} days. "
          f"{'Running follower total: ' + str(running_total) if running_total is not None else 'Follower counts not available yet.'}")

    # Weekly engagement — current Thu-Wed cycle only.
    week_start, week_end = get_post_week_range(now)
    week_stats = fetch_statistics(headers, location_id, profile_id, week_start, week_end)
    week_totals = week_stats.get("totals", {})
    posts_this_week = week_totals.get("posts", 0)
    likes_this_week = week_totals.get("likes", 0)
    comments_this_week = week_totals.get("comments", 0)

    current_followers = running_total if running_total is not None else baseline
    if posts_this_week > 0 and current_followers:
        rate = ((likes_this_week + comments_this_week) / (posts_this_week * current_followers)) * 100
        week_start_key = week_start.strftime("%Y-%m-%d")
        entries = [e for e in weekly_data.get(handle, []) if e["weekStart"] != week_start_key]
        entries.append({"weekStart": week_start_key, "engagementRate": round(rate, 2)})
        entries.sort(key=lambda e: e["weekStart"])
        if len(entries) > MAX_WEEKLY_ENTRIES:
            del entries[: len(entries) - MAX_WEEKLY_ENTRIES]
        weekly_data[handle] = entries
        print(f"  Weekly engagement: {round(rate, 2)}% ({posts_this_week} posts, "
              f"{likes_this_week} likes, {comments_this_week} comments)")
    else:
        print("  Skipping weekly engagement — no posts this week yet, or no follower baseline.")


def typeof_followers(entry):
    return isinstance(entry.get("followers"), (int, float))


def rebuild_data_json(clients, daily_data, weekly_data, data_json, now):
    week_start, week_end = get_post_week_range(now)
    week_start_key = week_start.strftime("%Y-%m-%d")
    week_end_key = week_end.strftime("%Y-%m-%d")

    if "accounts" not in data_json:
        data_json["accounts"] = {}

    for client in clients:
        handle = client["handle"]
        entries = daily_data.get(handle, [])
        follower_entries = sorted(
            [e for e in entries if typeof_followers(e)], key=lambda e: e["date"]
        )
        if not follower_entries:
            continue

        latest = follower_entries[-1]
        prior = [e for e in follower_entries if e["date"] != latest["date"]]
        prev_followers = prior[-1]["followers"] if prior else latest["followers"]
        delta_today = latest["followers"] - prev_followers

        post_dates = [
            e["date"] + "T00:00:00.000Z"
            for e in entries
            if week_start_key <= e["date"] <= week_end_key and e.get("posted")
        ]

        weekly_entries = weekly_data.get(handle, [])
        latest_weekly = max(weekly_entries, key=lambda e: e["weekStart"]) if weekly_entries else None
        existing_rate = data_json["accounts"].get(handle, {}).get("engagementRate", 0)

        data_json["accounts"][handle] = {
            "name": client["name"],
            "handle": handle,
            "followers": latest["followers"],
            "deltaToday": delta_today,
            "engagementRate": latest_weekly["engagementRate"] if latest_weekly else existing_rate,
            "postDates": post_dates,
        }

    data_json["generatedAt"] = now.isoformat()
    data_json["weekStart"] = week_start.isoformat()
    data_json["weekEnd"] = week_end.isoformat()


def main():
    token = get_env("GHL_PRIVATE_TOKEN")
    location_id = get_env("GHL_LOCATION_ID")
    headers = {
        "Authorization": f"Bearer {token}",
        "Version": "v3",
        "Accept": "application/json",
    }

    now = datetime.now(timezone.utc)

    clients = load_json(CLIENTS_PATH, [])
    daily_data = load_json(DAILY_PATH, {})
    weekly_data = load_json(WEEKLY_PATH, {})
    data_json = load_json(DATA_PATH, {"accounts": {}})

    print("Fetching connected Instagram accounts from GoHighLevel...")
    ig_accounts = fetch_instagram_accounts(headers, location_id)
    print(f"Found {len(ig_accounts)} connected Instagram account(s) in GHL.")

    matched = 0
    for client in clients:
        handle = client["handle"]
        if handle in ig_accounts:
            matched += 1
            try:
                sync_client(
                    handle, client["name"], ig_accounts[handle],
                    headers, location_id, daily_data, weekly_data, now,
                )
            except requests.HTTPError as e:
                print(f"  ! API error for {handle}: {e}", file=sys.stderr)
        else:
            print(f"\n--- {client['name']} (@{handle}) ---")
            print("  Not connected in GoHighLevel — skipped (left untouched).")

    print(f"\n{matched} of {len(clients)} clients matched to a connected GHL account.")

    rebuild_data_json(clients, daily_data, weekly_data, data_json, now)

    save_json(DAILY_PATH, daily_data)
    save_json(WEEKLY_PATH, weekly_data)
    save_json(DATA_PATH, data_json)
    print("\nWrote daily.json, weekly.json, and data.json.")


if __name__ == "__main__":
    main()
