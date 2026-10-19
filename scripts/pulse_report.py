#!/usr/bin/env python3
"""
Peak Pulse report saver.

The bot only reports what it SAW. This script does everything else, the same
way entry.html does: merges into daily.json / weekly.json / data.json, keeps
the Instagram stories ledger (stories.json) so a story that lasts 24h is never
counted twice, and saves through the GitHub Contents API (re-reading and
retrying on conflicts).

Usage:
    GITHUB_TOKEN=...  python3 scripts/pulse_report.py report.json
    cat report.json | GITHUB_TOKEN=... python3 scripts/pulse_report.py -
    python3 scripts/pulse_report.py report.json --local .      # no GitHub, edits local files (testing)
    python3 scripts/pulse_report.py report.json --local . --dry-run

Env:
    GITHUB_TOKEN   token with Contents: write on the repo (never printed)
    PULSE_REPO     default "Cameronmonahan/Peak-Pulse"
    PULSE_BRANCH   default "main"
    PULSE_TZ       default "America/Denver"  (the time zone dates are keyed in)

Report format (JSON):
{
  "checkedAt": "2026-10-14T19:00:00-06:00",      // optional, default = now
  "accounts": [
    // Instagram / Facebook / TikTok (use the handle keys from clients.json)
    {"handle": "marwooddesign", "posted": true, "followers": 9796,
     "fiveFiveFive": true,                        // 5-5-5 done today? (Instagram only; omit if not done/checked)
     "stories": ["3h", "5h", "40m"],              // ages shown on each story you see right now; [] = none up
     "likes": 120, "comments": 8},                // optional: last-5-posts totals for weekly engagement
    {"handle": "legacyflooringsd_fb", "posted": false, "followers": 410},
    // Finsta batches (key = <igHandle>_finsta), on the batch due date
    {"handle": "marwooddesign_finsta", "date": "2026-10-15", "reels": 6, "photos": 8},
    // the 11pm check on the due date itself: add "endOfDay": true to save the end-of-day count
    {"handle": "marwooddesign_finsta", "date": "2026-10-15", "reels": 6, "photos": 8, "endOfDay": true}
  ]
}
Omit a field you did not check. "posted": false never erases an earlier "yes" today.
"""
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date as _date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("PULSE_TZ", "America/Denver"))
REPO = os.environ.get("PULSE_REPO", "Cameronmonahan/Peak-Pulse")
BRANCH = os.environ.get("PULSE_BRANCH", "main")
MAX_DAILY = 400
MAX_WEEKLY = 60
MATCH_WINDOW_MIN = 75      # a later sighting within this of a logged story = same story
STORY_LIFE_H = 25          # stories older than this can't still be live
PRUNE_DAYS = 3
RECOMPUTE_DAYS = 2         # also recompute this many previous days


# ---------------------------------------------------------------- storage
class Local:
    def __init__(self, root):
        self.root = root

    def get(self, path):
        p = os.path.join(self.root, path)
        if not os.path.exists(p):
            return None, None
        with open(p, encoding="utf-8") as f:
            return json.load(f), None

    def put(self, path, obj, sha, msg):
        with open(os.path.join(self.root, path), "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)
            f.write("\n")


class GitHub:
    def __init__(self, token, repo, branch):
        self.token, self.repo, self.branch = token, repo, branch

    def _req(self, method, path, body=None):
        url = f"https://api.github.com/repos/{self.repo}/contents/{path}"
        if method == "GET":
            url += f"?ref={self.branch}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "peak-pulse-report",
        })
        return urllib.request.urlopen(req, timeout=30)

    def get(self, path):
        try:
            with self._req("GET", path) as r:
                j = json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None, None
            raise SystemExit(f"Could not read {path}: HTTP {e.code}")
        return json.loads(base64.b64decode(j["content"]).decode("utf-8")), j["sha"]

    def put(self, path, obj, sha, msg):
        body = {
            "message": msg,
            "branch": self.branch,
            "content": base64.b64encode(
                (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")).decode(),
        }
        if sha:
            body["sha"] = sha
        try:
            with self._req("PUT", path, body):
                return
        except urllib.error.HTTPError as e:
            if e.code in (409, 422):
                raise Conflict()
            raise SystemExit(f"Could not save {path}: HTTP {e.code}")


class Conflict(Exception):
    pass


def update_file(store, path, fn, msg, default, dry):
    """Read fresh, apply fn, write. Retries on a conflict by re-reading and re-applying."""
    for attempt in range(5):
        content, sha = store.get(path)
        if content is None:
            content = default
        new = fn(content)
        if dry:
            return new
        try:
            store.put(path, new, sha, msg)
            return new
        except Conflict:
            time.sleep(1 + attempt)
    raise SystemExit(f"Gave up saving {path} after repeated conflicts")


# ---------------------------------------------------------------- dates
def local_key(dt):
    return dt.astimezone(TZ).strftime("%Y-%m-%d")


def parse_when(s):
    if not s:
        return datetime.now(timezone.utc)
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=TZ)
    return d.astimezone(timezone.utc)


def week_bounds(check):
    """Monday..Sunday week (local dates) containing the check."""
    d = check.astimezone(TZ).date()
    mon = d - timedelta(days=d.weekday())
    return mon, mon + timedelta(days=6)


def local_midnight_utc_iso(d):
    return datetime(d.year, d.month, d.day, tzinfo=TZ).astimezone(timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%S.000Z")


def parse_age(a):
    """'3h', '45m', '1h 30m', 'just now', or a number (= minutes) -> minutes."""
    if isinstance(a, (int, float)):
        return float(a)
    s = str(a).strip().lower()
    if s in ("now", "just now", "0"):
        return 0.0
    h = re.search(r"(\d+(?:\.\d+)?)\s*h", s)
    m = re.search(r"(\d+(?:\.\d+)?)\s*m", s)
    if not h and not m:
        raise SystemExit(f"Can't read story age: {a!r} (use e.g. '3h', '45m', '1h 30m')")
    return (float(h.group(1)) * 60 if h else 0) + (float(m.group(1)) if m else 0)


# ---------------------------------------------------------------- accounts
def known_accounts(clients):
    out = {}
    for c in clients:
        out[c["handle"]] = {"name": c["name"], "platform": "instagram"}
        t = c.get("tiktokHandle")
        if t and not t.startswith("REPLACE_"):
            out[t] = {"name": c["name"], "platform": "tiktok"}
        if c.get("facebookHandle"):
            out[c["facebookHandle"]] = {"name": c["name"], "platform": "facebook"}
        if c.get("finsta"):
            out[c["handle"] + "_finsta"] = {"name": c["name"], "platform": "finsta"}
    return out


# ---------------------------------------------------------------- stories ledger
def apply_stories(ledger, handle, ages, check):
    """Match what is visible NOW against logged stories; add only the new ones.
    Returns number of newly logged stories."""
    items = ledger.setdefault(handle, [])
    live = [it for it in items
            if check - parse_when(it["postedAt"]) <= timedelta(hours=STORY_LIFE_H)]
    est = sorted(check - timedelta(minutes=parse_age(a)) for a in ages)
    used = set()
    new = 0
    for e in est:
        best, best_d = None, None
        for i, it in enumerate(live):
            if i in used:
                continue
            d = abs((parse_when(it["postedAt"]) - e).total_seconds()) / 60
            if d <= MATCH_WINDOW_MIN and (best_d is None or d < best_d):
                best, best_d = i, d
        if best is not None:
            used.add(best)      # same story seen again: keep the earliest estimate
            live[best]["lastSeen"] = check.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            rec = {"postedAt": e.strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "lastSeen": check.strftime("%Y-%m-%dT%H:%M:%SZ")}
            items.append(rec)
            live.append(rec)
            used.add(len(live) - 1)   # a story seen at this check is never matched twice
            new += 1
    return new


def prune(ledger, check):
    cutoff = check - timedelta(days=PRUNE_DAYS)
    for h in list(ledger):
        ledger[h] = [it for it in ledger[h] if parse_when(it["postedAt"]) >= cutoff]
        if not ledger[h]:
            del ledger[h]


def stories_per_day(ledger, handle):
    counts = {}
    for it in ledger.get(handle, []):
        k = local_key(parse_when(it["postedAt"]))
        counts[k] = counts.get(k, 0) + 1
    return counts


# ---------------------------------------------------------------- daily
def batch_rule(client, d):
    """The batch rule from clients.json that falls due on date d (None if none)."""
    for r in client.get("batches") or []:
        a = _date.fromisoformat(r["anchor"])
        if (d.weekday() + 1) % 7 == r["weekday"] and d >= a and ((d - a).days // 7) % r.get("everyWeeks", 1) == 0:
            return r
    return None


def batch_met(rule, reels, photos):
    return (reels + photos >= rule["target"] and reels >= rule.get("reelsTarget", 0)
            and photos >= rule.get("photosTarget", 0))


def upsert_daily(daily, handle, date, fields):
    arr = list(daily.get(handle, []))
    idx = next((i for i, e in enumerate(arr) if e["date"] == date), None)
    if idx is not None:
        merged = dict(arr[idx])
        merged.update(fields)
        if "posted" in fields:                       # a Yes sticks
            merged["posted"] = bool(arr[idx].get("posted") or fields["posted"])
        arr[idx] = merged
    else:
        arr.append(dict({"date": date}, **fields))
    arr.sort(key=lambda e: e["date"])
    if len(arr) > MAX_DAILY:
        arr = arr[-MAX_DAILY:]
    daily[handle] = arr


def rebuild_data_entry(data, daily, weekly, handle, meta, mon, sun):
    fin = sorted(daily.get(handle, []), key=lambda e: e["date"])
    f_entries = [e for e in fin if isinstance(e.get("followers"), (int, float))]
    if not f_entries:
        return
    latest = f_entries[-1]
    prior = f_entries[:-1]
    prev = prior[-1]["followers"] if prior else latest["followers"]
    a, b = mon.isoformat(), sun.isoformat()
    post_dates = [d["date"] + "T12:00:00.000Z" for d in fin if a <= d["date"] <= b and d.get("posted")]
    wk = sorted(weekly.get(handle, []), key=lambda e: e["weekStart"])
    old = data.get("accounts", {}).get(handle, {})
    rate = wk[-1]["engagementRate"] if wk else old.get("engagementRate", 0)
    data.setdefault("accounts", {})[handle] = {
        "name": meta["name"], "handle": handle, "platform": meta["platform"],
        "followers": latest["followers"], "deltaToday": latest["followers"] - prev,
        "engagementRate": rate, "postDates": post_dates,
    }


# ---------------------------------------------------------------- main
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)
    dry = "--dry-run" in flags
    src = sys.stdin.read() if args[0] == "-" else open(args[0], encoding="utf-8").read()
    report = json.loads(src)

    if "--local" in flags:
        i = sys.argv.index("--local")
        store = Local(sys.argv[i + 1])
        args = [a for a in args if a != sys.argv[i + 1]]
    else:
        tok = os.environ.get("GITHUB_TOKEN")
        if not tok:
            raise SystemExit("GITHUB_TOKEN is not set")
        store = GitHub(tok, REPO, BRANCH)

    check = parse_when(report.get("checkedAt"))
    today = local_key(check)
    mon, sun = week_bounds(check)
    clients, _ = store.get("clients.json")
    known = known_accounts(clients or [])

    entries, notes = [], []
    for a in report.get("accounts", []):
        h = a.get("handle")
        if h not in known:
            notes.append(f"SKIPPED unknown handle: {h}")
            continue
        entries.append((h, known[h], a))

    # 1) stories ledger
    story_counts = {}   # handle -> {date: count}
    new_stories = {}

    def ledger_fn(ledger):
        ledger = dict(ledger or {})
        for h, meta, a in entries:
            if meta["platform"] == "instagram" and isinstance(a.get("stories"), list):
                new_stories[h] = apply_stories(ledger, h, a["stories"], check)
        prune(ledger, check)
        for h, meta, a in entries:
            if meta["platform"] == "instagram" and isinstance(a.get("stories"), list):
                story_counts[h] = stories_per_day(ledger, h)
        return ledger

    if any(isinstance(a.get("stories"), list) for _, _, a in entries):
        update_file(store, "stories.json", ledger_fn, "Update stories.json [report script]", {}, dry)

    # 2) daily.json
    def daily_fn(daily):
        daily = dict(daily or {})
        for h, meta, a in entries:
            if meta["platform"] == "finsta":
                fields = {k: max(0, int(a[k])) for k in ("reels", "photos") if a.get(k) is not None}
                bd = a.get("date") or today
                if fields:
                    cur = next((e for e in daily.get(h, []) if e["date"] == bd), {})
                    reels = fields.get("reels", cur.get("reels") or 0)
                    photos = fields.get("photos", cur.get("photos") or 0)
                    if a.get("endOfDay"):
                        # the 11pm snapshot of the due day: what was done when the day ended
                        if bd == today:
                            fields["closeReels"], fields["closePhotos"] = reels, photos
                        else:
                            notes.append(f"{h}: endOfDay ignored (it only counts on the due date itself, {bd})")
                    client = next((c for c in clients or [] if c["handle"] + "_finsta" == h), {})
                    rule = batch_rule(client, _date.fromisoformat(bd))
                    if rule and not cur.get("completedOn") and batch_met(rule, reels, photos):
                        fields["completedOn"] = today   # first check that showed the batch complete
                    upsert_daily(daily, h, bd, fields)
                continue
            fields = {}
            if a.get("posted") is not None:
                fields["posted"] = bool(a["posted"])
            if isinstance(a.get("followers"), (int, float)):
                fields["followers"] = int(a["followers"])
            if a.get("fiveFiveFive") and meta["platform"] == "instagram":
                fields["fiveFiveFive"] = True      # a Yes sticks for the day; false never erases it
            if fields:
                upsert_daily(daily, h, today, fields)
            if h in story_counts:
                counts = story_counts[h]
                days = [(check.astimezone(TZ).date() - timedelta(days=n)).isoformat()
                        for n in range(RECOMPUTE_DAYS + 1)]
                for d in days:
                    existing = next((e.get("stories") for e in daily.get(h, []) if e["date"] == d), None)
                    c = counts.get(d, 0)
                    if d == today or c > 0:
                        # never lower a number that is already there (e.g. typed in the form)
                        upsert_daily(daily, h, d, {"stories": max(c, existing or 0)})
        return daily

    daily = update_file(store, "daily.json", daily_fn, "Update daily.json [report script]", {}, dry)

    # 3) weekly.json (engagement)
    ws = mon.isoformat()
    wk_changed = []

    def weekly_fn(weekly):
        weekly = dict(weekly or {})
        for h, meta, a in entries:
            if meta["platform"] == "finsta":
                continue
            rate = None
            if a.get("engagementRate") is not None:
                rate = float(a["engagementRate"])
            elif a.get("likes") is not None or a.get("comments") is not None:
                posts = sum(1 for e in daily.get(h, [])
                            if ws <= e["date"] <= sun.isoformat() and e.get("posted"))
                fol = a.get("followers")
                if not isinstance(fol, (int, float)):
                    fol = next((e["followers"] for e in reversed(sorted(daily.get(h, []), key=lambda e: e["date"]))
                                if isinstance(e.get("followers"), (int, float))), None)
                if fol and posts > 0:
                    rate = (float(a.get("likes") or 0) + float(a.get("comments") or 0)) / (posts * fol) * 100
                else:
                    notes.append(f"{h}: engagement skipped (need followers and at least 1 posted day this week)")
            if rate is not None:
                arr = [e for e in weekly.get(h, []) if e["weekStart"] != ws]
                arr.append({"weekStart": ws, "engagementRate": round(rate, 2)})
                arr.sort(key=lambda e: e["weekStart"])
                weekly[h] = arr[-MAX_WEEKLY:]
                wk_changed.append(h)
        return weekly

    weekly = update_file(store, "weekly.json", weekly_fn, "Update weekly.json [report script]", {}, dry)

    # 4) data.json
    def data_fn(data):
        data = dict(data or {})
        for h, meta, a in entries:
            if meta["platform"] == "finsta":
                continue
            rebuild_data_entry(data, daily, weekly, h, meta, mon, sun)
        data["generatedAt"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        data["weekStart"] = local_midnight_utc_iso(mon)
        data["weekEnd"] = local_midnight_utc_iso(sun)
        return data

    update_file(store, "data.json", data_fn, "Update data.json [report script]", {}, dry)

    # summary (no secrets)
    print(("DRY RUN - nothing saved. " if dry else "Saved. ") + f"Date {today}, week {mon}..{sun} ({TZ.key}).")
    for h, meta, a in entries:
        bits = []
        if meta["platform"] == "finsta":
            bits.append(f"batch {a.get('date') or today}: reels={a.get('reels')} photos={a.get('photos')}"
                        + (" (end of day)" if a.get("endOfDay") else ""))
        else:
            if a.get("posted") is not None:
                bits.append("posted=" + ("yes" if a["posted"] else "no"))
            if a.get("followers") is not None:
                bits.append(f"followers={a['followers']}")
            if a.get("fiveFiveFive") and meta["platform"] == "instagram":
                bits.append("5-5-5=yes")
            if h in story_counts:
                bits.append(f"stories seen={len(a['stories'])}, new={new_stories.get(h, 0)}, "
                            f"today total={story_counts[h].get(today, 0)}")
            if h in wk_changed:
                bits.append("engagement updated")
        print(f"  {h}: " + ", ".join(bits))
    for n in notes:
        print("  NOTE:", n)


if __name__ == "__main__":
    main()
