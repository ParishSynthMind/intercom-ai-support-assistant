"""Render the README images and the 4:3 portfolio thumbnail from recorded runs. Nothing typed in by hand:
every row comes from examples/local-run/run.json (and examples/live-run/run.json when present).

    python scripts/render_media.py   (needs: pip install playwright && playwright install chromium)
"""

import html
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
MEDIA = ROOT / "media"
FONT = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
STYLE = f"""<style>
 body{{margin:0;font-family:{FONT};background:#fff;color:#1a1a1a}}
 .wrap{{display:inline-block;padding:24px 28px;background:#fff}}
 h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#555;font-size:13px;margin:0 0 14px;max-width:980px;line-height:1.45}}
 table{{border-collapse:collapse;font-size:13px;width:1040px}}
 td,th{{border-bottom:1px solid #e6e6e6;padding:8px 10px;text-align:left;vertical-align:top}}
 th{{background:#f4f6fb;font-weight:600}}
 .tag{{display:inline-block;background:#eef2ff;color:#3546ab;border-radius:10px;padding:1px 8px;margin:1px 3px 1px 0;font-size:11.5px}}
 .tag.warn{{background:#fff4e0;color:#9a5b00}} .tag.bad{{background:#fde8e8;color:#b42318}}
 .note{{width:620px;background:#fff8e1;border:1px solid #f1d98a;border-radius:10px;padding:14px 16px;font-size:13.5px;line-height:1.5}}
 .note p{{margin:0 0 8px}} .note code{{background:#fff;padding:1px 5px;border-radius:4px;font-size:12px}}
 .label{{font-size:11px;color:#8a6d00;font-weight:600;letter-spacing:.04em;text-transform:uppercase;margin-bottom:6px}}
 .cust{{width:620px;background:#f1f3f5;border-radius:10px;padding:12px 16px;font-size:13.5px;margin-bottom:12px}}
 .thumb{{width:1200px;height:900px;box-sizing:border-box;padding:70px 80px;
   background:linear-gradient(135deg,#0b1d51 0%,#1f3fbf 55%,#3f7cff 100%);color:#fff;position:relative;overflow:hidden}}
 .thumb h2{{font-size:66px;line-height:1.05;margin:0 0 22px;letter-spacing:-1px}}
 .thumb .lead{{font-size:28px;opacity:.92;max-width:900px;line-height:1.35}}
 .chips{{margin-top:44px;display:flex;flex-wrap:wrap;gap:14px;max-width:1000px}}
 .chip{{background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.35);border-radius:30px;padding:12px 22px;font-size:24px}}
 .card{{position:absolute;right:70px;bottom:70px;width:470px;background:#fff8e1;color:#222;border-radius:16px;padding:22px 24px;
   font-size:20px;line-height:1.4;box-shadow:0 20px 50px rgba(0,0,0,.35)}}
 .card b{{color:#8a6d00}}
</style>"""


def tag_html(name):
    cls = "bad" if "injection" in name else ("warn" if "needs human" in name or "opted" in name else "")
    return f'<span class="tag {cls}">{html.escape(name)}</span>'


def strip(s):
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def triage_page(run):
    rows = []
    for st in run["steps"]:
        rows.append(f"<tr><td>{html.escape(st['customer_message'])}</td>"
                    f"<td>{''.join(tag_html(t) for t in st['tags'])}</td>"
                    f"<td>{'none (no AI draft)' if not st['internal_note'] or 'AI draft' not in st['internal_note'] else 'draft in internal note'}</td>"
                    f"<td>{len(st['customer_replies'])}</td></tr>")
    sub = (f"Five customer messages sent as signed Intercom webhooks to the assistant. Claude ({run['model']}) "
           "classified and drafted each one; Intercom itself is the local mock here. Nothing reached a customer: "
           f"replies sent = 0 until a person approved. Bad signature: HTTP {run['bad_signature_status']}. "
           "Duplicate delivery: dropped.")
    return (f"<div class='wrap'><h1>Triage results (recorded run)</h1><p class='sub'>{html.escape(sub)}</p>"
            "<table><tr><th>Customer message</th><th>Tags the assistant applied</th><th>Draft</th>"
            "<th>Sent to customer</th></tr>" + "".join(rows) + "</table></div>")


def note_page(step):
    note = step["internal_note"]
    body = re.sub(r"<p><i>ai-draft-ref:[0-9a-f]+</i></p>", "", note)
    return (f"<div class='wrap'><div class='cust'><div class='label'>Customer</div>{html.escape(step['customer_message'])}</div>"
            f"<div class='note'><div class='label'>Internal note (teammates only)</div>{body}</div></div>")


def thumb_page():
    return ("<div class='thumb'><h2>Intercom AI<br>Support Assistant</h2>"
            "<div class='lead'>Claude triages every conversation, tags and routes it, and drafts a cited reply "
            "as an internal note. A person approves before anything is sent.</div>"
            "<div class='chips'><span class='chip'>Signed webhooks</span><span class='chip'>Intent · priority · sentiment</span>"
            "<span class='chip'>RAG with citations</span><span class='chip'>Human approval</span>"
            "<span class='chip'>Injection guard</span></div>"
            "<div class='card'><b>AI draft #1 (not sent)</b><br>Intent: account · Priority: normal<br>"
            "Sources: account#1 Reset your password<br><code>approve 1</code></div></div>")


def main():
    MEDIA.mkdir(exist_ok=True)
    run = json.loads((ROOT / "examples" / "local-run" / "run.json").read_text(encoding="utf-8"))
    pages = [("local-triage.png", triage_page(run), ".wrap"),
             ("local-note.png", note_page(run["steps"][0]), ".wrap"),
             ("local-injection-note.png", note_page(run["steps"][2]), ".wrap"),
             ("thumbnail-4x3.png", thumb_page(), ".thumb")]
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1300, "height": 1000}, device_scale_factor=1)
        for name, body, sel in pages:
            pg.set_content(f"<html><head><meta charset='utf-8'>{STYLE}</head><body>{body}</body></html>")
            pg.locator(sel).screenshot(path=str(MEDIA / name))
            print("wrote", name)
        browser.close()


if __name__ == "__main__":
    main()
