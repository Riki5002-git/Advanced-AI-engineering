"""Report: one HTML page with the snapshot. Opens in any browser, no internet needed."""
import html

from analytics.transform import LOOP_THRESHOLD

STEP_NAMES = {"search_docs": "חיפוש במסמכים", "get_price": "מחירון", "lookup_warning_code": "קודי התרעה",
              "check_service_slots": "תורים במוסך", "open_ticket": "פתיחת פנייה", "chat": "שיחה בלי כלי",
              "שאלת הלקוח": "שאלת הלקוח"}


def _e(x) -> str:
    return html.escape(str(x))


def _ms(v: float) -> str:
    return f"{v / 1000:.1f}s" if v >= 1000 else f"{v:.0f}ms"


def _pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def _usd(v: float) -> str:
    return f"${v:.4f}" if v < 1 else f"${v:,.2f}"


def _label(name: str) -> str:
    return f"{STEP_NAMES[name]} <code>{_e(name)}</code>" if name in STEP_NAMES else f"<code>{_e(name)}</code>"


def insights(m: dict) -> list[str]:
    out = []
    tools = [s for s in m["steps"] if s["kind"] == "tool"]
    llm_ms = sum(s["total_ms"] for s in m["steps"] if s["kind"] == "llm")
    if tools:
        slow = max(tools, key=lambda s: s["p95_ms"])
        out.append(f"<b>השלב האיטי ביותר:</b> {_label(slow['name'])}: p95 של {_ms(slow['p95_ms'])} לקריאה, "
                   f"ו־{_pct(slow['turn_share'])} מזמן התור בתורות שבהן הוא נקרא. "
                   f"בסך הכול, הקריאות למודל הן {_pct(llm_ms / max(1, sum(s['total_ms'] for s in m['steps'])))} מהזמן.")
    after = [r for r in m["cost_after_tool"] if r["after"] != "שאלת הלקוח"]
    if after:
        top = after[0]
        base = next((r for r in m["cost_after_tool"] if r["after"] == "שאלת הלקוח"), None)
        cmp = f", לעומת {base['avg_input_tokens']:,} בקריאה הראשונה של כל תור" if base else ""
        out.append(f"<b>השלב היקר ביותר:</b> קריאת המודל שאחרי {_label(top['after'])}: {_pct(top['cost_share'])} "
                   f"מכל העלות, בממוצע {top['avg_input_tokens']:,} טוקני קלט{cmp}.")
    k = m["kpis"]
    if k["loop_turn_rate"]:
        out.append(f"<b>לולאות:</b> {_pct(k['loop_turn_rate'])} מהתורות כוללות את אותה קריאה לכלי {LOOP_THRESHOLD} פעמים "
                   f"או יותר. השגיאה הנפוצה: {_e(m['top_errors'][0][0]) if m['top_errors'] else '-'}.")
    rel = m["by_release"]
    if len(rel) > 1:
        a, b = rel[-2], rel[-1]
        ch = (b["cost_per_turn"] - a["cost_per_turn"]) / a["cost_per_turn"] if a["cost_per_turn"] else 0
        out.append(f"<b>גרסאות:</b> בגרסה {_e(b['release'])} עלות תור ממוצעת {_usd(b['cost_per_turn'])}, "
                   f"{'+' if ch >= 0 else ''}{ch * 100:.0f}% לעומת {_e(a['release'])}.")
    out.append(f"<b>איכות:</b> {_pct(k['thumbs_up_rate'])} אגודל למעלה מתוך {k['feedback_count']:,} דירוגים; "
               f"{_pct(k['has_source_rate'])} מהתשובות מצביעות על מקור.")
    return out


def bar_chart(rows: list[dict], key: str, fmt, title: str, color: str = "var(--teal)") -> str:
    if not rows:
        return ""
    w, h, pad_b, pad_t = 640, 200, 26, 18
    mx = max(r[key] for r in rows) or 1
    bw = (w - 20) / len(rows)
    bars = []
    for i, r in enumerate(rows):
        bh = (h - pad_b - pad_t) * r[key] / mx
        x = 10 + i * bw + 3
        y = h - pad_b - bh
        bars.append(f'<g><rect x="{x:.1f}" y="{y:.1f}" width="{bw - 6:.1f}" height="{max(bh, 1):.1f}" rx="3" fill="{color}">'
                    f'<title>{_e(r["day"])}: {_e(fmt(r[key]))}</title></rect>'
                    + (f'<text x="{x + (bw - 6) / 2:.1f}" y="{h - 8}" class="ax">{_e(r["day"][5:])}</text>'
                       if len(rows) <= 16 or i % 2 == 0 else "") + "</g>")
    peak = max(rows, key=lambda r: r[key])
    return (f'<figure><figcaption>{_e(title)} <span class="muted">· שיא: {_e(fmt(peak[key]))} ב־{_e(peak["day"])}</span>'
            f'</figcaption><svg viewBox="0 0 {w} {h}" role="img" aria-label="{_e(title)}">'
            f'<line x1="10" x2="{w - 10}" y1="{h - pad_b}" y2="{h - pad_b}" class="base"/>{"".join(bars)}</svg></figure>')


def share_bar(v: float, color: str) -> str:
    return f'<span class="share"><i style="width:{min(100, v * 100):.1f}%;background:{color}"></i></span> {_pct(v)}'


def table(headers: list[str], rows: list[list[str]]) -> str:
    th = "".join(f"<th>{h}</th>" for h in headers)
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="tw"><table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table></div>'


def render(m: dict, title: str = "תמונת מצב: סוכן השירות של אופק רכב") -> str:
    k = m["kpis"]
    days = m["daily"]
    span = f"{days[0]['day']} – {days[-1]['day']}" if days else ""
    tiles = [("שיחות", f"{k['conversations']:,}"), ("תורות", f"{k['turns']:,}"),
             ("זמן תגובה p50", _ms(k["p50_ms"])), ("זמן תגובה p95", _ms(k["p95_ms"])),
             ("עלות לשיחה", _usd(k["cost_per_conversation"])), ("עלות כוללת", _usd(k["total_cost"])),
             ("שגיאות כלים", _pct(k["tool_error_rate"])), ("תורות עם לולאה", _pct(k["loop_turn_rate"])),
             ("אגודל למעלה", _pct(k["thumbs_up_rate"])), ("תשובות עם מקור", _pct(k["has_source_rate"]))]
    for d in days:
        d["cpc"] = d["cost_per_conversation"]
    steps_rows = [[_label(s["name"]) if s["kind"] == "tool" else f"מודל <code>{_e(s['name'])}</code>",
                   f"{s['calls']:,}", _ms(s["p50_ms"]), _ms(s["p95_ms"]),
                   share_bar(s["time_share"], "var(--teal)"), _pct(s["turn_share"]), share_bar(s["cost_share"], "var(--amber)"),
                   _pct(s["error_rate"])] for s in m["steps"]]
    after_rows = [[_label(r["after"]), f"{r['llm_calls']:,}", f"{r['avg_input_tokens']:,}", _usd(r["cost_usd"]),
                   share_bar(r["cost_share"], "var(--amber)")] for r in m["cost_after_tool"]]
    rel_rows = [[_e(r["release"]), f"{r['turns']:,}", _usd(r["cost_per_turn"]), _ms(r["p95_ms"]),
                 _pct(r["tool_error_rate"]), _pct(r["loop_turn_rate"]), _pct(r["thumbs_up_rate"])] for r in m["by_release"]]
    intent_rows = [[_label(r["intent"]), f"{r['turns']:,}", _usd(r["cost_per_turn"]), _ms(r["p95_ms"]),
                    _pct(r["tool_error_rate"]), _pct(r["thumbs_up_rate"])] for r in m["by_intent"]]
    loop_rows = [[f"<code>{_e((t['trace_id'] or '-')[:12])}</code>", _e(t["question"])[:60], f"{t['max_repeat']}",
                  f"{t['llm_calls']}", _ms(t["duration_ms"]), _usd(t["cost_usd"])] for t in m["loops"]]
    err_rows = [[f"<code>{_e(e)}</code>", f"{n:,}"] for e, n in m["top_errors"]]
    return f"""<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title>
<style>
:root {{ --bg:#eef1f5; --card:#fff; --ink:#0f1a2b; --soft:#4a5872; --line:#d3dae4; --teal:#2f8f86; --amber:#d98a1c; --red:#c2413a; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0f1a2b; --card:#18263b; --ink:#eef1f5; --soft:#a9b7cc; --line:#2b3c56; --teal:#6fd1c6; --amber:#f2a63b; --red:#ff8a80; }} }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:16px/1.5 "Segoe UI", Arial, sans-serif; }}
main {{ max-width:1100px; margin:0 auto; padding:24px 16px 60px; }}
h1 {{ font-size:28px; margin:0 0 4px; }} h2 {{ font-size:20px; margin:32px 0 10px; }}
.muted {{ color:var(--soft); font-size:14px; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(170px,1fr)); gap:10px; margin-top:18px; }}
.tile {{ background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px 14px; }}
.tile b {{ display:block; font-size:26px; direction:ltr; text-align:right; font-variant-numeric:tabular-nums; }}
.tile span {{ color:var(--soft); font-size:14px; }}
.ins {{ background:var(--card); border:1px solid var(--line); border-right:4px solid var(--amber); border-radius:10px; padding:10px 18px; }}
.ins li {{ margin:6px 0; }}
.charts {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(320px,1fr)); gap:14px; }}
figure {{ margin:0; background:var(--card); border:1px solid var(--line); border-radius:10px; padding:12px; }}
figcaption {{ font-weight:600; margin-bottom:6px; }}
svg {{ width:100%; height:auto; }} svg rect:hover {{ opacity:.75; }}
.ax {{ font-size:11px; fill:var(--soft); text-anchor:middle; }} .base {{ stroke:var(--line); stroke-width:1; }}
.tw {{ overflow-x:auto; background:var(--card); border:1px solid var(--line); border-radius:10px; }}
table {{ border-collapse:collapse; width:100%; font-size:15px; }}
th, td {{ padding:8px 12px; text-align:right; border-bottom:1px solid var(--line); white-space:nowrap; font-variant-numeric:tabular-nums; }}
th {{ color:var(--soft); font-weight:600; font-size:13px; }}
code {{ direction:ltr; unicode-bidi:isolate; font-size:13px; color:var(--soft); }}
.share {{ display:inline-block; width:90px; height:8px; background:var(--line); border-radius:4px; vertical-align:middle; overflow:hidden; }}
.share i {{ display:block; height:100%; border-radius:4px; }}
</style></head><body><main>
<h1>{_e(title)}</h1>
<div class="muted">{_e(span)} · מקור: {_e(m["source"])}</div>
<div class="tiles">{"".join(f'<div class="tile"><span>{a}</span><b>{b}</b></div>' for a, b in tiles)}</div>
<h2>מה רואים</h2>
<ul class="ins">{"".join(f"<li>{x}</li>" for x in insights(m))}</ul>
<h2>לפי יום</h2>
<div class="charts">
{bar_chart(days, "cpc", _usd, "עלות ממוצעת לשיחה", "var(--amber)")}
{bar_chart(days, "p95_ms", _ms, "זמן תגובה p95 לתור")}
{bar_chart(days, "loop_turn_rate", _pct, "תורות עם לולאה", "var(--red)")}
{bar_chart(days, "thumbs_up_rate", _pct, "אגודל למעלה")}
</div>
<h2>לאן הולך הזמן, ולאן הולך הכסף</h2>
{table(["צעד", "קריאות", "p50", "p95", "חלק מהזמן", "מזמן התור שלו", "חלק מהעלות", "שגיאות"], steps_rows)}
<p class="muted">העלות כולה נמצאת בקריאות למודל. כדי לדעת מה מייקר אותן, מסתכלים על הצעד שלפני כל קריאה: התוצאה שלו נכנסת להקשר.</p>
{table(["קריאת מודל אחרי", "קריאות", "טוקני קלט בממוצע", "עלות", "חלק מהעלות"], after_rows)}
<h2>לפי גרסת הסוכן</h2>
{table(["גרסה", "תורות", "עלות לתור", "p95", "שגיאות כלים", "לולאות", "אגודל למעלה"], rel_rows)}
<h2>לפי סוג הבקשה (הכלי הראשון בתור)</h2>
{table(["סוג", "תורות", "עלות לתור", "p95", "שגיאות כלים", "אגודל למעלה"], intent_rows)}
<h2>שגיאות נפוצות</h2>
{table(["שגיאה", "פעמים"], err_rows)}
<h2>הלולאות הגרועות</h2>
{table(["trace", "שאלה", "אותה קריאה", "קריאות למודל", "זמן", "עלות"], loop_rows)}
</main></body></html>"""
