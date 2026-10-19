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
HEADER_URL = DASH_URL + "header-email.png"
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


def collect(root, mon):
    """All the numbers for one Monday-Sunday week (shared by the email and the PDF)."""
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

    return dict(clients=clients, rows=rows, batches=batches, rng=rng, sun=sun, tot_posted=tot_posted,
                tot_stories=tot_stories, tot_fff=tot_fff, tot_change=tot_change, top=top, quiet=quiet, short=short)


def build(root, mon):
    c = collect(root, mon)
    clients, rows, batches, rng = c["clients"], c["rows"], c["batches"], c["rng"]
    tot_posted, tot_stories, tot_fff, tot_change = c["tot_posted"], c["tot_stories"], c["tot_fff"], c["tot_change"]
    top, quiet, short = c["top"], c["quiet"], c["short"]
    subject = f"Peak Pulse weekly report: {rng}"
    pdf_url = DASH_URL + f"reports/peak-pulse-{mon}.pdf"

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

    # Email-safe: tables + inline styles + bgcolor attributes only (no <style>, no classes; Gmail on iPhone drops them).
    def label(t, align="left"):
        return (f'<div style="font-size:10px;letter-spacing:1.1px;text-transform:uppercase;color:{DIM};'
                f'font-weight:700;text-align:{align};">{t}</div>')

    def kpi(value, name, sub):
        return (f'<td width="50%" align="center" bgcolor="#f3eee2" style="background:#f3eee2;padding:16px 6px;border:3px solid #ffffff;border-top:3px solid {GOLD};">'
                f'<div style="font-family:Georgia,serif;font-size:32px;line-height:1.1;color:{NAVY};font-weight:700;">{value}</div>'
                f'<div style="font-size:11px;letter-spacing:1.2px;text-transform:uppercase;color:#3a4160;margin-top:6px;font-weight:700;">{name}</div>'
                f'<div style="font-size:12px;color:{DIM};margin-top:3px;">{sub}</div></td>')

    def metric(name, value, color=INK, sub=""):
        sub_h = f'<span style="color:{DIM};font-weight:400;font-size:12px;"> {sub}</span>' if sub else ""
        return (f'<td width="25%" valign="top" style="padding:0 4px 0 0;">'
                f'<div style="font-size:9.5px;letter-spacing:.8px;text-transform:uppercase;color:{DIM};font-weight:700;">{name}</div>'
                f'<div style="font-size:16px;font-weight:700;color:{color};margin-top:3px;">{value}{sub_h}</div></td>')

    cards, i = [], 0
    for c in clients:
        crows = [r for r in rows if r["name"] == c["name"]]
        if not crows:
            continue
        i += 1
        bg = "#ffffff" if i % 2 else ZEBRA
        block = [f'<tr><td bgcolor="{bg}" style="background:{bg};padding:16px 18px 2px 18px;border-top:1px solid {LINE};">'
                 f'<div style="font-size:17px;font-weight:700;color:{NAVY};">{esc(c["name"])}</div></td></tr>']
        for r in crows:
            chg_color = GREEN if (r["change"] or 0) > 0 else (RED if (r["change"] or 0) < 0 else DIM)
            eng_color = GREEN if r["grade"] == "good" else (RED if r["grade"] == "bad" else INK)
            ms = [metric("Posted", f'{r["posted"]}', RED if r["posted"] == 0 else INK, "of 7 days")]
            if r["platform"] == "instagram":
                ms.append(metric("Stories", num(r["stories"])))
                ms.append(metric("5-5-5", f'{r["fff"]}', INK, "of 7"))
            else:
                ms += ['<td width="25%"></td>', '<td width="25%"></td>']
            ms.append(metric("Engagement", "–" if r["eng"] is None else f'{r["eng"]:.2f}%', eng_color))
            block.append(
                f'<tr><td bgcolor="{bg}" style="background:{bg};padding:8px 18px 12px 18px;">'
                f'<table width="100%" cellpadding="0" cellspacing="0"><tr>'
                f'<td valign="bottom" style="font-size:11px;letter-spacing:1px;text-transform:uppercase;color:{GOLD};font-weight:700;padding-bottom:6px;">{PLAT[r["platform"]]}</td>'
                f'<td align="right" valign="bottom" style="padding-bottom:6px;"><span style="font-size:18px;font-weight:700;color:{INK};">{num(r["end"])}</span>'
                f'<span style="font-size:12px;color:{DIM};"> followers </span>'
                f'<span style="font-size:14px;font-weight:700;color:{chg_color};">{signed(r["change"])}</span></td></tr></table>'
                f'<table width="100%" cellpadding="0" cellspacing="0"><tr>{"".join(ms)}</tr></table></td></tr>')
        cards.append(f'<table width="100%" cellpadding="0" cellspacing="0">{"".join(block)}</table>')

    attn_html = ""
    attn = []
    for r in quiet:
        attn.append(f'<b>{esc(r["name"])}</b> had no Instagram posts logged this week.')
    for b in short:
        attn.append(f'<b>{esc(b["name"])}</b> {esc(b["label"])} ({fmt_day(b["due"])}): {b["got"]} of {b["target"]} posts delivered.')
    if attn:
        items = "".join(f'<tr><td valign="top" style="padding:0 8px 8px 0;color:{GOLD};font-size:16px;line-height:1.3;">•</td>'
                        f'<td style="padding:0 0 8px 0;font-size:15px;line-height:1.4;color:{INK};">{a}</td></tr>' for a in attn)
        attn_html = (f'<tr><td bgcolor="#fbf3e2" style="background:#fbf3e2;padding:18px 18px 10px 18px;border-top:1px solid {LINE};">'
                     f'<div style="font-size:11px;letter-spacing:1.4px;text-transform:uppercase;color:#8a6a2f;font-weight:700;margin-bottom:10px;">For Monday\'s meeting</div>'
                     f'<table cellpadding="0" cellspacing="0" width="100%">{items}</table></td></tr>')

    batch_html = ""
    if batches:
        items = ""
        for b in sorted(batches, key=lambda b: (b["due"], b["name"])):
            ok = b["got"] >= b["target"]
            col = GREEN if ok else (GOLD if b["flexible"] else RED)
            status = "Complete" if ok else ("Under target" if b["flexible"] else f'{b["target"] - b["got"]} short')
            items += (f'<tr><td style="padding:9px 0;border-bottom:1px solid {LINE};">'
                      f'<div style="font-size:15px;font-weight:700;color:{INK};">{esc(b["name"])}</div>'
                      f'<div style="font-size:12px;color:{DIM};margin-top:2px;">{esc(b["label"])} · due {fmt_day(b["due"])}</div></td>'
                      f'<td align="right" valign="middle" style="padding:9px 0;border-bottom:1px solid {LINE};">'
                      f'<div style="font-size:15px;color:{INK};">{b["got"]} / {b["target"]} posts</div>'
                      f'<div style="font-size:12px;font-weight:700;color:{col};margin-top:2px;">{status}</div></td></tr>')
        batch_html = (f'<tr><td bgcolor="#ffffff" style="background:#ffffff;padding:20px 18px 6px 18px;border-top:1px solid {LINE};">'
                      f'<div style="font-size:11px;letter-spacing:1.4px;text-transform:uppercase;color:{GOLD};font-weight:700;margin-bottom:6px;">Content batches due this week</div>'
                      f'<table width="100%" cellpadding="0" cellspacing="0">{items}</table></td></tr>')

    html_out = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light only"><title>{esc(subject)}</title></head>
<body bgcolor="#ece8de" style="margin:0;padding:0;background:#ece8de;">
<table width="100%" cellpadding="0" cellspacing="0" bgcolor="#ece8de" style="background:#ece8de;"><tr><td align="center" style="padding:12px 8px;">
<table width="100%" cellpadding="0" cellspacing="0" bgcolor="#ffffff" style="max-width:640px;width:100%;background:#ffffff;font-family:Helvetica,Arial,sans-serif;">
  <tr><td bgcolor="{NAVY}" style="background:{NAVY};padding:0;line-height:0;font-size:0;"><img src="{HEADER_URL}" width="640" alt="Peak Exposure Media: Weekly Client Pulse" style="display:block;border:0;width:100%;height:auto;max-width:640px;"></td></tr>
  <tr><td bgcolor="#ffffff" style="background:#ffffff;padding:20px 18px 6px 18px;">
    <div style="font-size:11px;letter-spacing:1.4px;text-transform:uppercase;color:{GOLD};font-weight:700;">Week of</div>
    <div style="font-family:Georgia,serif;font-size:28px;color:{NAVY};margin:4px 0 14px 0;line-height:1.15;font-weight:700;">{esc(rng)}</div>
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:8px;">
      <tr>{kpi(signed(tot_change), "Net followers", top_sub or "all accounts")}{kpi(tot_posted, "Posted days", "all accounts")}</tr>
      <tr>{kpi(tot_stories, "Stories", "Instagram")}{kpi(tot_fff, "5-5-5 days", "Instagram")}</tr>
    </table>
  </td></tr>
  {attn_html}
  <tr><td bgcolor="#ffffff" style="background:#ffffff;padding:20px 18px 12px 18px;border-top:1px solid {LINE};">
    <div style="font-size:11px;letter-spacing:1.4px;text-transform:uppercase;color:{GOLD};font-weight:700;">Every account</div>
  </td></tr>
  <tr><td>{"".join(cards)}</td></tr>
  {batch_html}
  <tr><td bgcolor="#ffffff" style="background:#ffffff;padding:16px 18px 4px 18px;font-size:12px;line-height:1.55;color:{DIM};border-top:1px solid {LINE};">
    Followers = last count logged by Sunday; the number beside it is the change vs. the last count before Monday. "Posted" = days with at least one post (red means none). Stories and 5-5-5 are Instagram only.&nbsp;
    Engagement is the weekly rate for the week; on Instagram, <b style="color:{GREEN};">green</b> beats the industry benchmark and <b style="color:{RED};">red</b> is below it.
  </td></tr>
  <tr><td align="center" bgcolor="#ffffff" style="background:#ffffff;padding:16px 18px 26px 18px;">
    <a href="{pdf_url}" style="display:inline-block;background:{GOLD};color:{NAVY};font-size:14px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;text-decoration:none;padding:14px 28px;border-radius:8px;margin:4px;">Download the PDF</a>
    <a href="{DASH_URL}" style="display:inline-block;border:2px solid {GOLD};color:{NAVY};font-size:14px;font-weight:700;letter-spacing:1.2px;text-transform:uppercase;text-decoration:none;padding:12px 26px;border-radius:8px;margin:4px;">Live dashboard</a>
  </td></tr>
  <tr><td bgcolor="#f3eee2" style="background:#f3eee2;padding:14px 18px;font-size:11px;color:{DIM};text-align:center;border-top:3px solid {GOLD};">Peak Exposure Media · internal weekly report</td></tr>
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
    lines += ["", "PDF report: " + pdf_url, "Live dashboard: " + DASH_URL]
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
