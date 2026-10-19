#!/usr/bin/env python3
"""
Build the printable Peak Pulse weekly report PDF.

    python3 scripts/weekly_pdf.py [--repo DIR] [--week YYYY-MM-DD] [--out reports]

Writes reports/peak-pulse-<monday>.pdf (the file the Monday email links to).
Default week = last completed Mon-Sun week (Mountain time), same as weekly_email.py.
Needs a Chrome/Chromium for the PDF step (GitHub's ubuntu runners have google-chrome).
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import weekly_email as we  # noqa: E402

esc, num, signed = we.esc, we.num, we.signed


def build_html(root, mon, header_src):
    c = we.collect(root, mon)
    rows, batches, rng = c["rows"], c["batches"], c["rng"]
    top = c["top"]
    top_sub = f'{esc(top["name"].split(" ")[0])} {signed(top["change"])}' if top else "all accounts"

    attn = [f'<b>{esc(r["name"])}</b> had no Instagram posts logged this week.' for r in c["quiet"]]
    attn += [f'<b>{esc(b["name"])}</b> {esc(b["label"])} ({we.fmt_day(b["due"])}): {b["got"]} of {b["target"]} posts delivered.'
             for b in c["short"]]

    trs, last, z = [], None, False
    for r in rows:
        first = r["name"] != last
        if first:
            z = not z
        last = r["name"]
        chg = "pos" if (r["change"] or 0) > 0 else ("neg" if (r["change"] or 0) < 0 else "dim")
        trs.append(
            f'<tr class="{"z" if z else ""} {"top" if first else ""}">'
            f'<td class="cl">{esc(r["name"]) if first else ""}</td><td class="pl">{we.PLAT[r["platform"]]}</td>'
            f'<td class="n">{num(r["end"])}</td><td class="n {chg}">{signed(r["change"])}</td>'
            f'<td class="n {"neg b" if r["posted"] == 0 else ""}">{r["posted"]}<span class="dim"> /7</span></td>'
            f'<td class="n">{num(r["stories"])}</td>'
            f'<td class="n">{"–" if r["fff"] is None else str(r["fff"]) + "<span class=dim> /7</span>"}</td>'
            f'<td class="n {"pos b" if r["grade"] == "good" else ("neg b" if r["grade"] == "bad" else "")}">'
            f'{"–" if r["eng"] is None else format(r["eng"], ".2f") + "%"}</td></tr>')

    brows = ""
    for b in sorted(batches, key=lambda b: (b["due"], b["name"])):
        ok = b["got"] >= b["target"]
        cls = "pos" if ok else ("dim" if b["flexible"] else "neg")
        status = "Complete" if ok else ("Under target" if b["flexible"] else f'{b["target"] - b["got"]} short')
        brows += (f'<tr><td class="cl">{esc(b["name"])}</td><td class="pl">{esc(b["label"])} · due {we.fmt_day(b["due"])}</td>'
                  f'<td class="n">{b["got"]} / {b["target"]} posts</td><td class="n {cls} b">{status}</td></tr>')

    kpi = lambda v, n, s: f'<div class="kpi"><div class="kv">{v}</div><div class="kn">{n}</div><div class="ks">{s}</div></div>'

    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Peak Pulse weekly report {esc(rng)}</title>
<style>
@page {{ size: Letter; margin: 0; }}
* {{ box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
body {{ margin:0; font-family: Helvetica, Arial, sans-serif; color:{we.INK}; font-size:11px; }}
.hdr {{ width:100%; display:block; }}
.wrap {{ padding: 16px 34px 0 34px; }}
.week {{ display:flex; align-items:baseline; gap:12px; }}
.week .l {{ font-size:10px; letter-spacing:1.4px; text-transform:uppercase; color:{we.GOLD}; font-weight:700; }}
.week .r {{ font-family: Georgia, 'DejaVu Serif', serif; font-size:24px; font-weight:700; color:{we.NAVY}; }}
.kpis {{ display:flex; gap:10px; margin:12px 0 14px; }}
.kpi {{ flex:1; background:#f3eee2; border-top:3px solid {we.GOLD}; text-align:center; padding:10px 4px; }}
.kv {{ font-family: Georgia, 'DejaVu Serif', serif; font-size:26px; font-weight:700; color:{we.NAVY}; }}
.kn {{ font-size:9px; letter-spacing:1.2px; text-transform:uppercase; color:#3a4160; font-weight:700; margin-top:3px; }}
.ks {{ font-size:9.5px; color:{we.DIM}; margin-top:2px; }}
.attn {{ background:#fbf3e2; padding:10px 14px 4px; margin-bottom:12px; }}
.attn h4, h4.sec {{ margin:0 0 7px; font-size:9.5px; letter-spacing:1.4px; text-transform:uppercase; color:{we.GOLD}; }}
.attn h4 {{ color:#8a6a2f; }}
.attn ul {{ margin:0; padding-left:15px; }} .attn li {{ margin:0 0 5px; font-size:11.5px; }}
table {{ width:100%; border-collapse:collapse; }}
th {{ text-align:right; font-size:8.5px; letter-spacing:.9px; text-transform:uppercase; color:{we.DIM}; padding:5px 6px; border-bottom:2px solid {we.GOLD}; }}
th:first-child, th:nth-child(2) {{ text-align:left; }}
td {{ padding:5px 6px; border-bottom:1px solid {we.LINE}; }}
tr.z td {{ background:{we.ZEBRA}; }}
td.cl {{ font-weight:700; }} td.pl {{ font-size:9px; text-transform:uppercase; letter-spacing:.6px; color:{we.DIM}; }}
td.n {{ text-align:right; font-variant-numeric: tabular-nums; white-space:nowrap; }}
.pos {{ color:{we.GREEN}; }} .neg {{ color:{we.RED}; }} .dim {{ color:{we.DIM}; }} .b {{ font-weight:700; }}
.note {{ font-size:8.8px; line-height:1.5; color:{we.DIM}; margin:10px 0 0; }}
.foot {{ margin-top:12px; background:#f3eee2; border-top:3px solid {we.GOLD}; text-align:center; font-size:9px; color:{we.DIM}; padding:8px; }}
</style></head><body>
<img class="hdr" src="{header_src}">
<div class="wrap">
  <div class="week"><span class="l">Week of</span><span class="r">{esc(rng)}</span></div>
  <div class="kpis">{kpi(signed(c["tot_change"]), "Net followers", top_sub)}{kpi(c["tot_posted"], "Posted days", "all accounts")}{kpi(c["tot_stories"], "Stories", "Instagram")}{kpi(c["tot_fff"], "5-5-5 days", "Instagram")}</div>
  {('<div class="attn"><h4>For Monday&#39;s meeting</h4><ul>' + "".join(f"<li>{a}</li>" for a in attn) + "</ul></div>") if attn else ""}
  <h4 class="sec">Every account</h4>
  <table><thead><tr><th>Client</th><th>Platform</th><th>Followers</th><th>Change</th><th>Posted days</th><th>Stories</th><th>5-5-5</th><th>Engagement</th></tr></thead><tbody>{"".join(trs)}</tbody></table>
  {('<h4 class="sec" style="margin-top:14px">Content batches due this week</h4><table><tbody>' + brows + "</tbody></table>") if brows else ""}
  <p class="note">Followers = last count logged by Sunday. Change = vs. the last count before Monday (– means not enough counts to compare). Posted days = days with at least one post (max 7); red means none. Stories and 5-5-5 are Instagram only. Engagement is the weekly rate logged for the week; on Instagram, green beats the industry benchmark and red is below it.</p>
  <div class="foot">Peak Exposure Media · internal weekly report · {DASH_URL_SHORT}</div>
</div></body></html>"""


DASH_URL_SHORT = we.DASH_URL.replace("https://", "")


def find_chrome():
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"):
        p = shutil.which(name)
        if p:
            return p
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    for cand in (os.path.join(base, "chromium"), ):
        if os.path.isfile(cand):
            return cand
    for root_, _, files in os.walk(base) if os.path.isdir(base) else []:
        for f in files:
            if f in ("chrome", "headless_shell") and "chromium" in root_:
                return os.path.join(root_, f)
    raise SystemExit("No Chrome/Chromium found for PDF rendering")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
    ap.add_argument("--week")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    repo = os.path.abspath(a.repo)
    if a.week:
        mon = a.week
    else:
        today = datetime.now(we.TZ).date()
        mon = (today - timedelta(days=today.weekday() + 7)).isoformat()
    if date.fromisoformat(mon).weekday() != 0:
        raise SystemExit("--week must be a Monday")
    out_dir = a.out or os.path.join(repo, "reports")
    os.makedirs(out_dir, exist_ok=True)
    out_pdf = os.path.join(out_dir, f"peak-pulse-{mon}.pdf")

    header = os.path.join(repo, "header-email.png")
    html = build_html(repo, mon, "file://" + header)
    with tempfile.TemporaryDirectory() as tmp:
        hp = os.path.join(tmp, "report.html")
        with open(hp, "w", encoding="utf-8") as f:
            f.write(html)
        cmd = [find_chrome(), "--headless=new", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
               "--allow-file-access-from-files", f"--print-to-pdf={out_pdf}", "file://" + hp]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if not os.path.exists(out_pdf) or os.path.getsize(out_pdf) < 5000:
            raise SystemExit("PDF was not created:\n" + r.stderr[-800:])
    print(out_pdf)


if __name__ == "__main__":
    main()
