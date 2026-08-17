# Peak Exposure Media — Client Pulse Dashboard

A live dashboard for the office TV (and your phone) showing, per client Instagram account:
- New followers today
- Engagement rate
- Posts this week (Thursday → Wednesday cycle)

## Files

| File | Purpose |
|---|---|
| `index.html` | The dashboard itself. Static — no build step. |
| `clients.json` | The list of clients and their Instagram handles. Edit this to add/remove clients. |
| `data.json` | The live metrics, written automatically by the fetch script. Don't edit by hand. |
| `history.json` | Daily follower snapshots, used to calculate "new followers today". Don't edit by hand. |
| `scripts/fetch_instagram_data.py` | Pulls data from the Instagram Graph API and writes `data.json`. |
| `.github/workflows/update-data.yml` | Runs the script every 30 minutes via GitHub Actions. |

## One-time setup

### 1. Get an Instagram access token
Each client's Instagram account needs to be a **Business or Creator account linked to a Facebook Page** (see the setup guide from earlier — Meta Developer app, Graph API Explorer, `instagram_basic` + `pages_read_engagement` permissions). Since you personally manage/admin all the client accounts, **one long-lived token tied to your own login** can see all of them — you don't need a separate token per client.

Generate the token directly on Meta's site (developers.facebook.com → Graph API Explorer). **Never paste this token into a chat, commit it to the repo, or put it in `index.html`.**

### 2. Add the token as a GitHub secret
In your repo: **Settings → Secrets and variables → Actions → New repository secret**
- Name: `IG_ACCESS_TOKEN`
- Value: (paste your token here — this is encrypted and never shown in logs)

### 3. Push this repo to GitHub
Upload all these files (keeping the folder structure — `.github/workflows/` must stay nested exactly like that).

### 4. Turn on GitHub Pages
**Settings → Pages → Source: Deploy from a branch → main → / (root) → Save.**
You'll get a URL like `https://yourusername.github.io/peak-client-pulse/`.

### 5. Run the workflow once manually
Go to the **Actions** tab → "Update Instagram Data" → **Run workflow**. Check the logs — it'll print which accounts it found and any it couldn't match (usually means that account isn't a Business/Creator account yet, or isn't linked to a Page your token can see).

After that, it runs automatically every 30 minutes and commits the updated `data.json`.

## How "new followers today" is calculated

Instagram's API only ever gives a current follower total, not a daily change. The script solves this itself: every time it runs, it compares today's count to the most recent count from a *previous* day (stored in `history.json`) and saves the difference. The first day it runs for a new account, delta will show `0` since there's no prior day to compare against yet — that's expected.

## How engagement rate is calculated

This uses the standard proxy most agencies use without deeper API access: `(avg likes + comments on recent posts) / followers × 100`. It is **not** based on reach/impressions, which Instagram only exposes to the account's actual owner via extra permissions. If you want true reach-based engagement rate later, that's a script upgrade, not a dashboard change.

## Adjusting the polling frequency

Instagram's Graph API rate limits are generous for an account this size, but if you want less frequent updates, change the `cron` line in `.github/workflows/update-data.yml` (e.g. `*/60 * * * *` for hourly).

## Adding or removing a client

Edit `clients.json` only — both the dashboard and the fetch script read from it, so nothing else needs to change.

## If a card shows "not connected"

That client's `handle` in `clients.json` doesn't match a Page + Instagram Business Account your token can see. Double check:
- The Instagram account is Professional (Business or Creator), not Personal
- It's linked to a Facebook Page
- Your Meta account/token has admin access to that Page
- The `handle` in `clients.json` matches the Instagram username exactly (no `@`, no URL)
