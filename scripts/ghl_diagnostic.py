"""
Peak Exposure Media — GoHighLevel Diagnostic Script
-----------------------------------------------------
This is NOT the final data-fetching script. Its only job is to call GHL's
API once and print out exactly what comes back, so we can confirm what each
field actually means before wiring this into the real dashboard pipeline.

Run this via the GitHub Actions workflow (see .github/workflows/ghl-diagnostic.yml)
or locally with:
    export GHL_PRIVATE_TOKEN="pit-..."
    export GHL_LOCATION_ID="NGAudo89KVk2PSD1a7Ol"
    python3 scripts/ghl_diagnostic.py
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import requests

BASE = "https://services.leadconnectorhq.com"


def get_env(name):
    val = os.environ.get(name)
    if not val:
        print(f"ERROR: {name} environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    return val


def main():
    token = get_env("GHL_PRIVATE_TOKEN")
    location_id = get_env("GHL_LOCATION_ID")

    headers = {
        "Authorization": f"Bearer {token}",
        "Version": "v3",
        "Accept": "application/json",
    }

    print("=" * 70)
    print("STEP 1: Fetching connected accounts")
    print("=" * 70)

    accounts_resp = requests.get(
        f"{BASE}/social-media-posting/{location_id}/accounts",
        headers=headers,
    )
    print(f"Status: {accounts_resp.status_code}")

    if accounts_resp.status_code != 200:
        print("Response body:", accounts_resp.text)
        print("\nStopping here — fix the error above before continuing.")
        sys.exit(1)

    accounts_data = accounts_resp.json()
    print(json.dumps(accounts_data, indent=2))

    accounts = accounts_data.get("results", {}).get("accounts", [])
    if not accounts:
        print("\nNo connected accounts found. Nothing to test statistics against.")
        sys.exit(0)

    print(f"\nFound {len(accounts)} connected account(s):")
    for acc in accounts:
        print(f"  - id={acc.get('id')}  name={acc.get('name')}  platform={acc.get('platform')}")

    # Pick the first account as a test subject
    test_account = accounts[0]
    test_id = test_account["id"]

    print("\n" + "=" * 70)
    print(f"STEP 2: Fetching statistics for ONE account (test subject)")
    print(f"  name={test_account.get('name')}  platform={test_account.get('platform')}")
    print("=" * 70)

    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    body = {
        "profileIds": [test_id],
        "currentRange": {
            "startDate": week_ago.strftime("%Y-%m-%dT00:00:00.000Z"),
            "endDate": now.strftime("%Y-%m-%dT23:59:59.999Z"),
        },
    }

    stats_resp = requests.post(
        f"{BASE}/social-media-posting/statistics",
        headers={**headers, "Content-Type": "application/json"},
        params={"locationId": location_id},
        json=body,
    )
    print(f"Status: {stats_resp.status_code}")
    print("Request body sent:", json.dumps(body, indent=2))

    if stats_resp.status_code != 200:
        print("Response body:", stats_resp.text)
        sys.exit(1)

    stats_data = stats_resp.json()
    print("\nFull response:")
    print(json.dumps(stats_data, indent=2))

    print("\n" + "=" * 70)
    print("DONE — copy everything above and send it back for review.")
    print("=" * 70)


if __name__ == "__main__":
    main()
