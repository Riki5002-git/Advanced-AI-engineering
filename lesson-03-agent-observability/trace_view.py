"""Prints the traces from runs/spans.jsonl (offline mode) as a tree, like Langfuse's trace view.

    python trace_view.py            # the last 3 traces
    python trace_view.py --last 10
    python trace_view.py 4f2a       # the trace whose id starts with 4f2a
"""
import argparse
import json
import sys
from collections import defaultdict

from agent.tracing import SPANS_FILE

sys.stdout.reconfigure(encoding="utf-8")
parser = argparse.ArgumentParser()
parser.add_argument("trace", nargs="?", default="")
parser.add_argument("--last", type=int, default=3)
args = parser.parse_args()

if not SPANS_FILE.exists():
    sys.exit("No runs/spans.jsonl yet. Run simulate.py first (without Langfuse keys).")

spans = [json.loads(line) for line in SPANS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
by_trace = defaultdict(list)
for s in spans:
    by_trace[s["trace_id"]].append(s)
order = sorted(by_trace, key=lambda t: min(s["start_ns"] for s in by_trace[t]))
chosen = [t for t in order if t.startswith(args.trace)] if args.trace else order[-args.last:]


def value(a: dict, key: str):
    raw = a.get(key)
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


def describe(s: dict) -> str:
    a = s["attributes"]
    kind = a.get("langfuse.observation.type", "span")
    ms = (s["end_ns"] - s["start_ns"]) / 1e6
    bits = [f"{kind:<10} {s['name']:<20} {ms:8.0f} ms"]
    if "langfuse.observation.usage_details" in a:
        u = json.loads(a["langfuse.observation.usage_details"])
        c = json.loads(a.get("langfuse.observation.cost_details", "{}"))
        bits.append(f"in {u.get('input', 0)} · out {u.get('output', 0)} tokens · ${c.get('total', 0):.4f}")
    if a.get("langfuse.observation.level") in ("ERROR", "WARNING"):
        bits.append(f"{a['langfuse.observation.level']}: {a.get('langfuse.observation.status_message', '')}")
    return "  ".join(bits)


for t in chosen:
    items = by_trace[t]
    children = defaultdict(list)
    ids = {s["span_id"] for s in items}
    for s in items:
        children[s["parent_id"] if s["parent_id"] in ids else None].append(s)
    root = children[None][0]
    a = root["attributes"]
    print(f"trace {t}  user={a.get('user.id', '-')}  session={a.get('session.id', '-')}")
    print(f"  input:  {value(a, 'langfuse.observation.input')}")

    def walk(s, depth):
        print("  " + "   " * depth + "└ " + describe(s))
        for c in sorted(children[s["span_id"]], key=lambda x: x["start_ns"]):
            walk(c, depth + 1)

    walk(root, 0)
    print(f"  output: {value(a, 'langfuse.observation.output')}\n")
