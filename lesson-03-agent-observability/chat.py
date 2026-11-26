"""Chat with the agent yourself. Every turn is traced and saved like the simulated ones.

    python chat.py
    python chat.py --user cust_1001
"""
import argparse
import sys

sys.stdout.reconfigure(encoding="utf-8")
sys.stdin.reconfigure(encoding="utf-8")

from agent.agent import Agent, Conversation          # noqa: E402
from agent.config import DEFAULT_DB                  # noqa: E402
from agent.simulation import save_turn_safely        # noqa: E402
from agent.tracing import get_langfuse, langfuse_online, trace_url  # noqa: E402
from db import store                                 # noqa: E402

parser = argparse.ArgumentParser()
parser.add_argument("--user", default="cust_me")
parser.add_argument("--db", default=str(DEFAULT_DB))
args = parser.parse_args()

con = store.connect(args.db)
agent = Agent()
conv = Conversation(user_id=args.user)
store.save_user(con, args.user)
store.save_conversation(con, conv, agent.release)
con.commit()
print(f"session {conv.id} · langfuse {'online' if langfuse_online() else 'offline'} · an empty line ends the chat\n")

while True:
    try:
        text = input("את/ה: ").strip()
    except (EOFError, KeyboardInterrupt):
        break
    if not text:
        break
    r = agent.run_turn(conv, text)
    save_turn_safely(con, conv, text, r)
    print(f"אופקי: {r.answer}")
    print(f"       {r.duration_ms / 1000:.1f}s · ${r.cost_usd:.4f} · {len(r.steps)} steps · {trace_url(r.trace_id)}\n")
    get_langfuse().flush()

get_langfuse().flush()
