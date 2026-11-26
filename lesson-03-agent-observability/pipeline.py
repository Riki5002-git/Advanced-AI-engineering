"""The data pipeline: Extract -> Transform -> Load -> Report.

    python pipeline.py                         # from runs/agent.db
    python pipeline.py --db runs/demo.db       # the two-week demo history (seed_demo.py)
    python pipeline.py --source langfuse       # straight from Langfuse's API
"""
import argparse
import sys
import time
from pathlib import Path

from agent.config import DEFAULT_DB, ROOT
from analytics import extract, load, report, transform

sys.stdout.reconfigure(encoding="utf-8")
parser = argparse.ArgumentParser()
parser.add_argument("--db", default=str(DEFAULT_DB))
parser.add_argument("--source", choices=["db", "langfuse"], default="db")
parser.add_argument("--days", type=int, default=14, help="with --source langfuse")
parser.add_argument("--out", default=None, help="HTML file (default: next to the database)")
args = parser.parse_args()

t0 = time.time()
raw = extract.from_db(args.db) if args.source == "db" else extract.from_langfuse(args.days)
print(f"1. extract   {len(raw['turns']):,} turns, {len(raw['steps']):,} steps, {len(raw['feedback']):,} scores  ({raw['source']})")
if not raw["turns"]:
    sys.exit("   No turns. Run simulate.py first, and make sure step 3 (save_turn) is done.")

metrics = transform.run(raw)
k = metrics["kpis"]
print(f"2. transform p95 {k['p95_ms'] / 1000:.1f}s · ${k['cost_per_conversation']:.4f} per conversation · "
      f"tool errors {k['tool_error_rate']:.1%} · loops {k['loop_turn_rate']:.1%} · thumbs up {k['thumbs_up_rate']:.1%}")

if args.source == "db":
    load.to_db(args.db, metrics)
    print("3. load      metrics_daily, metrics_steps, metrics_turns, metrics_runs  →  " + args.db)

out = Path(args.out) if args.out else (Path(args.db).with_suffix(".html") if args.source == "db" else ROOT / "runs" / "langfuse.html")
out.write_text(report.render(metrics), encoding="utf-8")
print(f"4. report    {out}  ({time.time() - t0:.1f}s)\n")
for line in report.insights(metrics):
    print("   • " + line.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""))
