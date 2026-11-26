"""Two weeks of history for the demo: the same agent, run on a virtual clock, sent to Langfuse with the
plain OpenTelemetry SDK (with the real past timestamps) and saved to runs/demo.db.

    python seed_demo.py                    # 14 days, to Langfuse (if keys are set) and to runs/demo.db
    python seed_demo.py --days 7 --per-day 20
    python seed_demo.py --no-langfuse      # only the local database

What is inside the history, on purpose:
- Release v1.2, then v1.3 for the last 5 days. v1.3 returns whole documents from search_docs, 5 instead of 3.
- A CRM outage 4 days ago, 09:30-14:00. open_ticket fails, and the agent retries in a loop.
- Now and then a slow answer from the garage system or from the model.

The traces are built from the agent's own step records (agent.Step), not from your Langfuse code, so this
works the same before and after step 2. Langfuse Cloud's free plan keeps 30 days of data, so run this again
before a lesson. Each run adds new traces; use a fresh Langfuse project if you want a clean demo.
"""
import argparse
import base64
import json
import os
import random
import sys
import time
import uuid
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

sys.stdout.reconfigure(encoding="utf-8")
parser = argparse.ArgumentParser()
parser.add_argument("--days", type=int, default=14)
parser.add_argument("--per-day", type=int, default=40, help="conversations on a regular weekday")
parser.add_argument("--seed", type=int, default=7)
parser.add_argument("--db", default=None, help="default runs/demo.db (replaced)")
parser.add_argument("--no-langfuse", action="store_true")
args = parser.parse_args()

from opentelemetry import trace                                               # noqa: E402
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # noqa: E402
from opentelemetry.sdk.resources import Resource                              # noqa: E402
from opentelemetry.sdk.trace import TracerProvider                            # noqa: E402
from opentelemetry.sdk.trace.export import BatchSpanProcessor                 # noqa: E402
from opentelemetry.sdk.trace.id_generator import RandomIdGenerator            # noqa: E402
from opentelemetry.trace import Status, StatusCode                            # noqa: E402

import agent.agent as agent_mod                                               # noqa: E402
from agent.agent import Agent                                                 # noqa: E402
from agent.config import ROOT, Clock                                          # noqa: E402
from agent.pricing import cost_details                                        # noqa: E402
from agent.scenarios import SEGMENTS, pick_scenario                           # noqa: E402
from agent.simulation import run_conversation                                 # noqa: E402
from agent.tools import ToolContext                                           # noqa: E402
from agent.tracing import langfuse_online                                     # noqa: E402
from db import store                                                          # noqa: E402

IL = timezone(timedelta(hours=2))
RELEASES = {"v1.2": {"full_docs": False, "top_k": 3}, "v1.3": {"full_docs": True, "top_k": 5}}


# ---- 1. The agent runs with a do-nothing Langfuse client; we build the traces ourselves -----------------
class _Nothing:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self, **kw):
        return self


class NullLangfuse:
    def start_as_current_observation(self, **kw):
        return _Nothing()

    def get_current_trace_id(self):
        return None

    def create_score(self, **kw):
        pass

    def flush(self):
        pass


agent_mod.propagate_attributes = lambda **kw: nullcontext()


def turn_tree(conv, text, result) -> dict:
    """The observations of one turn: an agent root, with one generation or tool per step."""
    children = []
    for s in result.steps:
        node = {"name": s.name, "start": s.started_at, "end": s.started_at + s.duration_ms / 1000, "output": s.output}
        if s.kind == "llm":
            node.update(name="claude", type="generation", model=s.name, input=conv.messages[:s.n_messages],
                        usage=s.usage, cost=cost_details(s.name, s.usage),
                        first_token=s.started_at + s.ttft if s.ttft else None)
        else:
            node.update(type="tool", input=json.loads(s.args or "{}"))
            if s.status == "error":
                node.update(level="ERROR", status_message=s.error)
        children.append(node)
    root = {"name": "support-agent", "type": "agent", "start": result.started_at,
            "end": result.started_at + result.duration_ms / 1000, "input": text, "output": result.answer,
            "children": children, "turn": conv.turns}
    if result.stopped_by_guard:
        root.update(level="WARNING", status_message="stopped after MAX_STEPS")
    return root


# ---- 2. Plain OpenTelemetry, pointed at Langfuse's OTLP endpoint -------------------------------------------
class FixedTraceIds(RandomIdGenerator):
    """Lets us choose the trace id, so Langfuse and runs/demo.db use the same one."""
    next_trace_id: int | None = None

    def generate_trace_id(self) -> int:
        tid, self.next_trace_id = self.next_trace_id, None
        return tid or super().generate_trace_id()


def make_tracer():
    base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or "https://cloud.langfuse.com"
    auth = base64.b64encode(f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()).decode()
    exporter = OTLPSpanExporter(endpoint=base.rstrip("/") + "/api/public/otel/v1/traces",
                                headers={"Authorization": f"Basic {auth}", "x-langfuse-ingestion-version": "4"})
    ids = FixedTraceIds()
    provider = TracerProvider(resource=Resource.create({"service.name": "ofek-support-agent"}), id_generator=ids)
    provider.add_span_processor(BatchSpanProcessor(exporter, max_export_batch_size=200, max_queue_size=20000))
    return provider, provider.get_tracer("ofek-seed-demo"), ids


def _j(v) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)


def emit(tracer, node: dict, ctx, common: dict) -> int:
    """One span per observation. The attribute names are the ones the Langfuse SDK itself sends."""
    attrs = {**common, "langfuse.observation.type": node["type"],
             "langfuse.observation.input": _j(node["input"]), "langfuse.observation.output": _j(node["output"])}
    if node.get("level"):
        attrs["langfuse.observation.level"] = node["level"]
        attrs["langfuse.observation.status_message"] = node["status_message"]
    if node["type"] == "generation":
        attrs.update({"langfuse.observation.model.name": node["model"],
                      "langfuse.observation.usage_details": _j(node["usage"]),
                      "langfuse.observation.cost_details": _j(node["cost"]),
                      "gen_ai.operation.name": "chat", "gen_ai.provider.name": "anthropic",
                      "gen_ai.request.model": node["model"]})
        if node.get("first_token"):
            attrs["langfuse.observation.completion_start_time"] = datetime.fromtimestamp(
                node["first_token"], timezone.utc).isoformat()
    if node["type"] == "tool":
        attrs.update({"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": node["name"]})
    span = tracer.start_span(node["name"], context=ctx, start_time=int(node["start"] * 1e9), attributes=attrs)
    n = 1
    for c in node.get("children", []):
        n += emit(tracer, c, trace.set_span_in_context(span), common)
    if node.get("level") == "ERROR":
        span.set_status(Status(StatusCode.ERROR, node["status_message"]))
    span.end(end_time=int(node["end"] * 1e9))
    return n


# ---- 3. The history ---------------------------------------------------------------------------------------
def conversation_starts(rng: random.Random, days: int, per_day: int) -> list[float]:
    now = datetime.now(IL)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    starts = []
    for d in range(days, -1, -1):
        day = today - timedelta(days=d)
        weekday = day.weekday()          # Monday=0 ... Friday=4, Saturday=5, Sunday=6
        n = per_day * (0.55 if weekday == 4 else 0.3 if weekday == 5 else 1.0) * rng.uniform(0.85, 1.15)
        for _ in range(int(n)):
            hour = rng.choices(range(7, 23), weights=[2, 5, 8, 9, 9, 8, 7, 7, 8, 8, 7, 6, 6, 5, 3, 2])[0]
            t = day + timedelta(hours=hour, minutes=rng.uniform(0, 60))
            if t < now - timedelta(minutes=5):
                starts.append(t.timestamp())
    return sorted(starts)


def main():
    rng = random.Random(args.seed)
    db_path = args.db or str(ROOT / "runs" / "demo.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    con = store.connect(db_path)
    send = langfuse_online() and not args.no_langfuse
    provider, tracer, ids = make_tracer() if send else (None, None, None)
    if send:
        from langfuse import Langfuse
        scorer = Langfuse()

    today = datetime.now(IL).date()
    release_day = today - timedelta(days=5)
    outage_day = today - timedelta(days=4)
    clock = Clock(virtual_start=0)
    agents = {}
    for rel, opts in RELEASES.items():
        ctx = ToolContext(kb=agent_mod.knowledge_base(), clock=clock, rng=rng, full_docs=opts["full_docs"],
                          top_k=opts["top_k"])
        agents[rel] = Agent(backend="sim", clock=clock, rng=rng, tool_ctx=ctx, release=rel, langfuse=NullLangfuse())

    starts = conversation_starts(rng, args.days, args.per_day)
    trees: list[dict] = []
    ids_rng = random.Random(args.seed + 1000)   # same --seed, same ids

    def keep(conv, text, result):
        result.trace_id = uuid.UUID(int=ids_rng.getrandbits(128), version=4).hex
        trees.append({"trace_id": result.trace_id, "tree": turn_tree(conv, text, result)})

    spans = scores = 0
    t0 = time.time()
    for i, start in enumerate(starts):
        when = datetime.fromtimestamp(start, IL)
        ag = agents["v1.3" if when.date() >= release_day else "v1.2"]
        ag.tool_ctx.crm_outage = when.date() == outage_day and 9.5 <= when.hour + when.minute / 60 < 14
        clock.t = start
        trees.clear()
        user = f"cust_{rng.randint(1000, 1400)}"
        conv = run_conversation(ag, con, pick_scenario(rng), user, rng.choice(SEGMENTS), rng,
                                send_scores=False, before_save=keep, conv_id=f"conv_{ids_rng.getrandbits(48):012x}")
        if send:
            for t in trees:
                ids.next_trace_id = int(t["trace_id"], 16)
                common = {"user.id": conv.user_id, "session.id": conv.id, "langfuse.trace.name": "support-turn",
                          "langfuse.version": ag.release, "langfuse.trace.tags": [ag.llm.model, conv.channel, "demo"],
                          "langfuse.trace.metadata.turn": str(t["tree"]["turn"])}
                spans += emit(tracer, t["tree"], None, common)
            for f in con.execute("select trace_id, name, value, created_at from feedback where conversation_id = ?",
                                 (conv.id,)):
                scorer.create_score(trace_id=f[0], name=f[1], value=f[2], data_type="BOOLEAN",
                                    timestamp=datetime.fromisoformat(f[3]))
                scores += 1
        if (i + 1) % 100 == 0:
            print(f"  {i + 1:,}/{len(starts):,} conversations ({when:%Y-%m-%d})")

    n_turns = con.execute("select count(*) from messages where role = 'user'").fetchone()[0]
    if not n_turns:
        sys.exit("No turns were saved. Write save_turn in db/store.py first (step 3).")
    print(f"\n{len(starts):,} conversations, {n_turns:,} turns, {time.time() - t0:.0f}s → {db_path}")
    if send:
        provider.force_flush()
        provider.shutdown()
        scorer.flush()
        print(f"Langfuse: {spans:,} observations and {scores:,} scores sent (≈{spans + scores:,} of the free plan's "
              f"50,000 monthly units). They appear within a minute or two.")
    else:
        print("Langfuse: skipped (no keys, or --no-langfuse).")
    print("Next: python pipeline.py --db " + os.path.relpath(db_path))


if __name__ == "__main__":
    main()
