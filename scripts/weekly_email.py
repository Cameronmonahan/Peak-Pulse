#!/usr/bin/env python3
"""
Build the Monday-morning Peak Pulse weekly report email (branded HTML + plain text).

    python3 scripts/weekly_email.py [--repo DIR] [--week YYYY-MM-DD] [--out DIR]

- Reads clients.json / daily.json / weekly.json from DIR (default: the repo this script lives in).
- --week is the MONDAY of the week to report (default: the last completed Mon-Sun week, Mountain time).
- Writes DIR_OUT/subject.txt, report.html and report.txt (default OUT: ./email-out) and prints the subject.

Same numbers and rules as the dashboard's "Weekly Report" tab.
"""
import argparse
import html
import json
import os
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo(os.environ.get("PULSE_TZ", "America/Denver"))
DASH_URL = "https://cameronmonahan.github.io/Peak-Pulse/"
LOGO_URL = DASH_URL + "logo-email.png"
BATCH_TRACKING_START = "2026-10-13"

NAVY, NAVY2, GOLD, GOLD_BRIGHT = "#171c2a", "#262c42", "#c9a876", "#e6cd9c"
INK, DIM, LINE, ZEBRA = "#1d2233", "#6b7189", "#e4e0d6", "#f7f5ef"
GREEN, RED = "#2f7d3a", "#c0392b"

IG_BENCHMARKS = {
    "Alcohol": 0.368, "Fashion": 0.147, "Financial Services": 0.261, "Food & Beverage": 0.400,
    "Health & Beauty": 0.144, "Higher Ed": 2.101, "Home Decor": 0.136, "Influencers": 0.576,
    "Media": 0.443, "Nonprofits": 0.561, "Retail": 0.160, "Sports Teams": 1.30,
    "Tech & Software": 0.327, "Travel": 0.341, "All Industries": 0.36,
}
PLAT = {"instagram": "Instagram", "tiktok": "TikTok", "facebook": "Facebook"}


def load(root, name):
    with open(os.path.join(root, name), encoding="utf-8") as f:
        return json.load(f)


def fff_done(e):
    if e.get("fiveFiveFive"):
        return True
    o = e.get("engagement555")
    return isinstance(o, dict) and all(int(o.get(k) or 0) >= 5 for k in ("newAccounts", "comments", "dms"))


def accounts_for(client):
    out = [("instagram", client["handle"])]
    t = client.get("tiktokHandle")
    if t and not t.startswith("REPLACE_"):
        out.append(("tiktok", t))
    if client.get("facebookHandle"):
        out.append(("facebook", client["facebookHandle"]))
    return out


def row_for(client, platform, handle, mon, sun, daily, weekly):
    days = sorted(daily.get(handle, []), key=lambda e: e["date"])
    in_week = [e for e in days if mon <= e["date"] <= sun]
    ig = platform == "instagram"
    with_f = [e for e in days if isinstance(e.get("followers"), (int, float))]
    end_e = [e for e in with_f if e["date"] <= sun]
    end_e = end_e[-1] if end_e and end_e[-1]["date"] >= mon else None
    base = [e for e in with_f if e["date"] < mon]
    base = base[-1] if base else next((e for e in with_f if e["date"] >= mon), None)
    change = (end_e["followers"] - base["followers"]) if (end_e and base and base is not end_e) else None
    wk = next((e for e in weekly.get(handle, []) if e["weekStart"] == mon), None)
    eng = wk["engagementRate"] if wk else None
    bench = IG_BENCHMARKS.get(client.get("industry"), IG_BENCHMARKS["All Industries"])
    return {
        "name": client["name"], "platform": platform, "handle": handle,
        "posted": sum(1 for e in in_week if e.get("posted")),
        "stories": sum(e["stories"] for e in in_week if isinstance(e.get("stories"), (int, float))) if ig else None,
        "fff": sum(1 for e in in_week if fff_done(e)) if ig else None,
        "end": end_e["followers"] if end_e else None,
        "change": change, "eng": eng,
        "grade": ("good" if eng >= bench else "bad") if (ig and eng) else "",
    }


def batches_in_week(client, mon, daily):
    """Finsta batches due this week with what was delivered."""
    out = []
    key = client["handle"] + "_finsta"
    start = date.fromisoformat(mon)
    for rule in client.get("batches", []) or []:
        anchor = date.fromisoformat(rule["anchor"])
        for i in range(7):
            d = start + timedelta(days=i)
            if (d.weekday() + 1) % 7 != rule["weekday"] or d < anchor:
                continue
            if ((d - anchor).days // 7) % rule.get("everyWeeks", 1) != 0:
                continue
            if d.isoformat() < BATCH_TRACKING_START:
                continue
            e = next((x for x in daily.get(key, []) if x["date"] == d.isoformat()), {})
            got = (e.get("reels") or 0) + (e.get("photos") or 0)
            out.append({"name": client["name"], "label": rule["label"], "due": d, "got": got,
                        "target": rule["target"], "flexible": bool(rule.get("flexible"))})
    return out


def fmt_day(d):
    return f"{d.strftime('%b')} {d.day}"


def esc(x):
    return html.escape(str(x))


def num(v):
    return "–" if v is None else f"{v:,}"


def signed(v):
    if v is None:
        return "–"
    return f"+{v:,}" if v > 0 else f"{v:,}"


def build(root, mon):
    clients = load(root, "clients.json")
    daily, weekly = load(root, "daily.json"), load(root, "weekly.json")
    sun = (date.fromisoformat(mon) + timedelta(days=6)).isoformat()
    d0, d1 = date.fromisoformat(mon), date.fromisoformat(sun)
    rng = f"{fmt_day(d0)} – {fmt_day(d1)}"

    rows, batches = [], []
    by = {c["name"]: c for c in clients}
    for c in clients:
        for p, h in accounts_for(c):
            rows.append(row_for(c, p, h, mon, sun, daily, weekly))
        batches += batches_in_week(c, mon, daily)

    tot_posted = sum(r["posted"] for r in rows)
    tot_stories = sum(r["stories"] or 0 for r in rows)
    tot_fff = sum(r["fff"] or 0 for r in rows)
    tot_change = sum(r["change"] or 0 for r in rows)
    gainers = sorted([r for r in rows if r["change"]], key=lambda r: -r["change"])
    top = gainers[0] if gainers and gainers[0]["change"] > 0 else None
    quiet = [r for r in rows if r["platform"] == "instagram" and r["posted"] == 0]
    short = [b for b in batches if b["got"] < b["target"] and not (b["flexible"] and b["got"] > 0)]

    subject = f"Peak Pulse weekly report: {rng}"

    # ---------- HTML ----------
    def td(content, align="right", color=INK, bold=False, extra=""):
        w = "700" if bold else "400"
        return (f'<td align="{align}" style="padding:10px 8px;border-bottom:1px solid {LINE};'
                f'font-size:13px;color:{color};font-weight:{w};white-space:nowrap;{extra}">{content}</td>')

    def stat(label, value, sub=""):
        return (f'<td align="center" width="25%" style="padding:14px 4px;">'
                f'<div style="font-family:Georgia,serif;font-size:30px;line-height:1;color:{GOLD_BRIGHT};">{value}</div>'
                f'<div style="font-size:10px;letter-spacing:1.2px;text-transform:uppercase;color:#b9bdd0;margin-top:6px;">{label}</div>'
                f'{("<div style=font-size:11px;color:#8d93ac;margin-top:2px;>" + sub + "</div>") if sub else ""}</td>')

    body_rows, last, z = [], None, False
    for r in rows:
        first = r["name"] != last
        if first:
            z = not z
        last = r["name"]
        bg = ZEBRA if z else "#ffffff"
        chg_color = GREEN if (r["change"] or 0) > 0 else (RED if (r["change"] or 0) < 0 else DIM)
        eng_color = GREEN if r["grade"] == "good" else (RED if r["grade"] == "bad" else INK)
        posted_color = RED if r["posted"] == 0 else INK
        cells = [
            f'<td style="padding:10px 8px;border-bottom:1px solid {LINE};font-size:13px;font-weight:700;color:{INK};">{esc(r["name"]) if first else ""}</td>',
            f'<td style="padding:10px 8px;border-bottom:1px solid {LINE};font-size:11px;letter-spacing:.6px;text-transform:uppercase;color:{DIM};">{PLAT[r["platform"]]}</td>',
            td(num(r["end"])),
            td(signed(r["change"]), color=chg_color, bold=bool(r["change"])),
            td(f'{r["posted"]}<span style="color:{DIM};"> /7</span>', color=posted_color, bold=r["posted"] == 0),
            td(num(r["stories"])),
            td("–" if r["fff"] is None else f'{r["fff"]}<span style="color:{DIM};"> /7</span>'),
            td("–" if r["eng"] is None else f'{r["eng"]:.2f}%', color=eng_color, bold=bool(r["grade"])),
        ]
        body_rows.append(f'<tr style="background:{bg};">{"".join(cells)}</tr>')

    heads = ["Client", "Platform", "Followers", "Change", "Posted days", "Stories", "5-5-5", "Engagement"]
    head_html = "".join(
        f'<th align="{"left" if i < 2 else "right"}" style="padding:8px;font-size:9.5px;letter-spacing:1px;'
        f'text-transform:uppercase;color:{DIM};border-bottom:2px solid {GOLD};font-weight:600;white-space:nowrap;">{h}</th>'
        for i, h in enumerate(heads))

    attn = []
    for r in quiet:
        attn.append(f'<b>{esc(r["name"])}</b> had no posts logged on Instagram this week.')
    for b in short:
        attn.append(f'<b>{esc(b["name"])}</b> {esc(b["label"])} ({fmt_day(b["due"])}): {b["got"]} of {b["target"]} posts delivered.')
    attn_html = ""
    if attn:
        items = "".join(f'<li style="margin:0 0 6px 0;">{a}</li>' for a in attn)
        attn_html = (f'<tr><td style="padding:22px 28px 4px 28px;"><div style="font-size:11px;letter-spacing:1.4px;text-transform:uppercase;'
                     f'color:{GOLD};font-weight:700;margin-bottom:10px;">For Monday\'s meeting</div>'
                     f'<ul style="margin:0;padding-left:18px;font-size:13.5px;line-height:1.5;color:{INK};">{items}</ul></td></tr>')

    batch_html = ""
    if batches:
        brow = ""
        for b in sorted(batches, key=lambda b: (b["due"], b["name"])):
            ok = b["got"] >= b["target"]
            col = GREEN if ok else (GOLD if b["flexible"] else RED)
            status = "Complete" if ok else ("Under target" if b["flexible"] else f'{b["target"] - b["got"]} short')
            brow += (f'<tr><td style="padding:8px;border-bottom:1px solid {LINE};font-size:13px;font-weight:700;color:{INK};">{esc(b["name"])}</td>'
                     f'<td style="padding:8px;border-bottom:1px solid {LINE};font-size:12px;color:{DIM};">{esc(b["label"])} · {fmt_day(b["due"])}</td>'
                     f'<td align="right" style="padding:8px;border-bottom:1px solid {LINE};font-size:13px;color:{INK};">{b["got"]} / {b["target"]} posts</td>'
                     f'<td align="right" style="padding:8px;border-bottom:1px solid {LINE};font-size:12px;font-weight:700;color:{col};">{status}</td></tr>')
        batch_html = (f'<tr><td style="padding:22px 28px 4px 28px;"><div style="font-size:11px;letter-spacing:1.4px;text-transform:uppercase;'
                      f'color:{GOLD};font-weight:700;margin-bottom:8px;">Content batches due this week</div>'
                      f'<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">{brow}</table></td></tr>')

    top_sub = f'{esc(top["name"].split(" ")[0])} {signed(top["change"])}' if top else ""
    html_out = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(subject)}</title></head>
<body style="margin:0;padding:0;background:#ece8de;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#ece8de;"><tr><td align="center" style="padding:24px 10px;">
<table width="680" cellpadding="0" cellspacing="0" style="max-width:680px;width:100%;background:#ffffff;border-radius:14px;overflow:hidden;font-family:Helvetica,Arial,sans-serif;">
  <tr><td style="background:{NAVY};padding:26px 28px 8px 28px;border-bottom:3px solid {GOLD};">
    <img src="{LOGO_URL}" width="200" alt="Peak Exposure Media" style="display:block;border:0;height:auto;max-width:200px;">
    <div style="font-family:Georgia,serif;font-size:28px;color:#f3f1ea;margin-top:18px;">Weekly Client Pulse</div>
    <div style="font-size:12px;letter-spacing:1.6px;text-transform:uppercase;color:{GOLD_BRIGHT};margin:6px 0 16px 0;">{esc(rng)} · Monday to Sunday</div>
    <table width="100%" cellpadding="0" cellspacing="0" style="background:{NAVY2};border-radius:10px;margin-bottom:22px;"><tr>
      {stat("Net followers", signed(tot_change), top_sub)}
      {stat("Posted days", tot_posted, "across all accounts")}
      {stat("Stories", tot_stories, "Instagram")}
      {stat("5-5-5 days", tot_fff, "Instagram")}
    </tr></table>
  </td></tr>
  {attn_html}
  <tr><td style="padding:22px 28px 4px 28px;">
    <div style="font-size:11px;letter-spacing:1.4px;text-transform:uppercase;color:{GOLD};font-weight:700;margin-bottom:8px;">Every account</div>
    <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;"><thead><tr>{head_html}</tr></thead><tbody>{"".join(body_rows)}</tbody></table>
  </td></tr>
  {batch_html}
  <tr><td style="padding:14px 28px 6px 28px;font-size:11.5px;line-height:1.55;color:{DIM};">
    <b>Followers</b> = last count logged by Sunday. <b>Change</b> = vs. the last count before Monday ("–" means not enough counts to compare).
    <b>Posted days</b> = days with at least one post (max 7); red means none. <b>Stories</b> and <b>5-5-5</b> are Instagram only.
    <b>Engagement</b> is the weekly rate logged for the week; on Instagram, <span style="color:{GREEN};font-weight:700;">green</span> beats the industry benchmark and <span style="color:{RED};font-weight:700;">red</span> is below it.
  </td></tr>
  <tr><td align="center" style="padding:18px 28px 28px 28px;">
    <a href="{DASH_URL}" style="display:inline-block;background:{GOLD};color:{NAVY};font-size:12px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;text-decoration:none;padding:12px 26px;border-radius:8px;">Open the live dashboard</a>
  </td></tr>
  <tr><td style="background:{NAVY};padding:14px 28px;font-size:11px;color:#8d93ac;text-align:center;">Peak Exposure Media · internal weekly report</td></tr>
</table>
</td></tr></table></body></html>"""

    # ---------- plain text ----------
    lines = [subject, "", f"Net followers {signed(tot_change)} | Posted days {tot_posted} | Stories {tot_stories} | 5-5-5 days {tot_fff}", ""]
    if attn:
        lines += ["FOR MONDAY'S MEETING"] + ["- " + html.unescape(a.replace("<b>", "").replace("</b>", "")) for a in attn] + [""]
    lines.append("Client | Platform | Followers | Change | Posted | Stories | 5-5-5 | Engagement")
    for r in rows:
        lines.append(" | ".join([r["name"], PLAT[r["platform"]], num(r["end"]), signed(r["change"]), f'{r["posted"]}/7',
                                 num(r["stories"]), "–" if r["fff"] is None else f'{r["fff"]}/7',
                                 "–" if r["eng"] is None else f'{r["eng"]:.2f}%']))
    if batches:
        lines += ["", "CONTENT BATCHES DUE"] + [f'{b["name"]} {b["label"]} ({fmt_day(b["due"])}): {b["got"]}/{b["target"]} posts' for b in batches]
    lines += ["", "Live dashboard: " + DASH_URL]
    return subject, html_out, "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    ap.add_argument("--week")
    ap.add_argument("--out", default="email-out")
    a = ap.parse_args()
    if a.week:
        mon = a.week
    else:
        today = datetime.now(TZ).date()
        mon = (today - timedelta(days=today.weekday() + 7)).isoformat()   # last completed Mon-Sun week
    if date.fromisoformat(mon).weekday() != 0:
        raise SystemExit("--week must be a Monday")
    subject, h, t = build(a.repo, mon)
    os.makedirs(a.out, exist_ok=True)
    for name, content in (("subject.txt", subject), ("report.html", h), ("report.txt", t)):
        with open(os.path.join(a.out, name), "w", encoding="utf-8") as f:
            f.write(content)
    print(subject)


if __name__ == "__main__":
    main()
