"""Runs simulated customer conversations through the agent.

    python simulate.py                 # 8 conversations
    python simulate.py --n 30 --fast   # 30 conversations, waits shortened 10x
    python simulate.py --db runs/after.db   # a separate database, to compare before and after a change
"""
import argparse
import os
import random
import sys

sys.stdout.reconfigure(encoding="utf-8")

parser = argparse.ArgumentParser()
parser.add_argument("--n", type=int, default=8, help="how many conversations")
parser.add_argument("--seed", type=int, default=None, help="same seed = same conversations")
parser.add_argument("--fast", action="store_true", help="wait 10x less (durations are 10x shorter too)")
parser.add_argument("--db", default=None, help="SQLite file (default runs/agent.db)")
args = parser.parse_args()
if args.fast:
    os.environ["SIM_SPEED"] = "0.1"

from agent.agent import Agent                     # noqa: E402  (after SIM_SPEED is set)
from agent.config import DEFAULT_DB, LLM_BACKEND  # noqa: E402
from agent.scenarios import SEGMENTS, pick_scenario   # noqa: E402
from agent.simulation import run_conversation     # noqa: E402
from agent.tracing import get_langfuse, langfuse_online, trace_url  # noqa: E402
from db import store                              # noqa: E402

# Two random streams: one picks the customers, one drives the agent. With the same --seed, a change to the
# agent's settings still meets the same customers, so before/after comparisons are fair.
rng = random.Random(args.seed)
db_path = args.db or DEFAULT_DB
con = store.connect(db_path)
agent = Agent(rng=random.Random(None if args.seed is None else args.seed + 1))
print(f"backend={LLM_BACKEND}  model={agent.llm.model}  langfuse={'online' if langfuse_online() else 'offline (runs/spans.jsonl)'}  db={db_path}\n")


def show(conv, text, r):
    errors = f"  ✗{r.tool_errors} tool errors" if r.tool_errors else ""
    guard = "  ⚠ stopped by MAX_STEPS" if r.stopped_by_guard else ""
    print(f"  [{conv.turns}] {text}")
    print(f"      → {r.answer[:110]}")
    print(f"      {r.duration_ms / 1000:.1f}s  ${r.cost_usd:.4f}  {len(r.steps)} steps{errors}{guard}  {trace_url(r.trace_id)}")


total = 0.0
for i in range(args.n):
    script = pick_scenario(rng)
    user = f"cust_{rng.randint(1000, 1060)}"
    print(f"conversation {i + 1}/{args.n} · {user}")
    conv = run_conversation(agent, con, script, user, rng.choice(SEGMENTS), rng, on_turn=show)
    print()

get_langfuse().flush()
print("done. Langfuse may need ~30 seconds to show new traces." if langfuse_online()
      else "done. Run `python trace_view.py` to see the traces.")
