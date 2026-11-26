"""The agent's tools. The external systems (garage booking, CRM) are simulated, with realistic waits and failures,
so everyone sees the same kinds of problems without real accounts."""
import json
import random
import re
from dataclasses import dataclass, field

from agent.config import Clock, LIVE
from agent.kb import KnowledgeBase

# ---- settings you will measure in step 5 -------------------------------------------------------------
SEARCH_FULL_DOCS = False       # search_docs returns matching sections instead of whole documents
SEARCH_TOP_K = 5
PARALLEL_SLOT_LOOKUP = True    # check_service_slots waits only for the slowest branch
# -------------------------------------------------------------------------------------------------------

BRANCHES = ["תל אביב", "חיפה", "באר שבע"]


class ToolError(Exception):
    """A tool failed. The agent returns the message to the model as an error result."""


@dataclass
class ToolContext:
    kb: KnowledgeBase
    clock: Clock = LIVE
    rng: random.Random = field(default_factory=random.Random)
    customer_id: str = "anonymous"
    crm_outage: bool = False       # seed_demo.py turns this on to simulate an outage
    crm_fail_rate: float = 0.12
    full_docs: bool | None = None  # None = use SEARCH_FULL_DOCS
    top_k: int | None = None
    parallel_slots: bool | None = None


def _table_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if line.startswith("|") and not line.startswith("|---"):
            rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows


def search_docs(ctx: ToolContext, query: str) -> str:
    ctx.clock.sleep(ctx.rng.uniform(0.15, 0.35))
    full = SEARCH_FULL_DOCS if ctx.full_docs is None else ctx.full_docs
    k = ctx.top_k or SEARCH_TOP_K
    hits = ctx.kb.search_sections(query, k=k)
    if not hits:
        return "לא נמצאו מסמכים מתאימים."
    if full:
        seen, parts = [], []
        for s in hits:
            if s.doc_id not in seen:
                seen.append(s.doc_id)
                parts.append(f'<doc id="{s.doc_id}" title="{s.doc_title}">\n{ctx.kb.full_doc(s.doc_id)}\n</doc>')
        return "\n\n".join(parts)
    return "\n\n".join(f'<doc id="{s.doc_id}" section="{s.heading}">\n{s.text}\n</doc>' for s in hits)


def get_price(ctx: ToolContext, model_code: str) -> str:
    ctx.clock.sleep(ctx.rng.uniform(0.05, 0.12))
    code = model_code.strip().upper()
    for row in _table_rows(ctx.kb.full_doc("price-list-2025")):
        if len(row) >= 4 and row[1].upper() == code:
            return json.dumps({"model": row[0], "code": row[1], "basic": row[2], "full": row[3],
                               "currency": "ILS", "source": "price-list-2025"}, ensure_ascii=False)
    raise ToolError(f"400 unknown model code: {model_code}")


def lookup_warning_code(ctx: ToolContext, code: str) -> str:
    ctx.clock.sleep(ctx.rng.uniform(0.04, 0.09))
    code = code.strip().upper()
    for row in _table_rows(ctx.kb.full_doc("warning-codes")):
        if len(row) >= 4 and row[0].upper() == code:
            return json.dumps({"code": row[0], "light": row[1], "meaning": row[2], "action": row[3],
                               "source": "warning-codes"}, ensure_ascii=False)
    raise ToolError(f"404 warning code not found: {code}")


def _branch_slots(ctx: ToolContext, branch: str) -> tuple[list[str], float]:
    """One request to the garage system: (free slots, how long the request took)."""
    latency = ctx.rng.uniform(0.6, 1.5)
    if ctx.rng.random() < 0.08:
        latency += ctx.rng.uniform(1.5, 3.0)  # a slow answer now and then
    day = ctx.rng.randint(2, 9)
    slots = [f"בעוד {day} ימים, {h}:00" for h in sorted(ctx.rng.sample([8, 9, 10, 11, 13, 14, 15], 2))]
    return slots, latency


def check_service_slots(ctx: ToolContext, branch: str = "") -> str:
    branches = [b for b in BRANCHES if branch and b in branch] or BRANCHES
    results = [(b, *_branch_slots(ctx, b)) for b in branches]
    parallel = PARALLEL_SLOT_LOOKUP if ctx.parallel_slots is None else ctx.parallel_slots
    # Sequential: the waits add up. Parallel: we wait only for the slowest branch.
    ctx.clock.sleep(max(r[2] for r in results) if parallel else sum(r[2] for r in results))
    return json.dumps({b: slots for b, slots, _ in results}, ensure_ascii=False)


def open_ticket(ctx: ToolContext, topic: str, details: str = "") -> str:
    if ctx.crm_outage or ctx.rng.random() < ctx.crm_fail_rate:
        if ctx.rng.random() < 0.5:
            ctx.clock.sleep(2.5)
            raise ToolError("timeout: CRM did not answer within 2.5s")
        ctx.clock.sleep(ctx.rng.uniform(0.1, 0.3))
        raise ToolError("503 Service Unavailable (CRM)")
    ctx.clock.sleep(ctx.rng.uniform(0.3, 0.8))
    return json.dumps({"ticket_id": f"T-{ctx.rng.randint(10000, 99999)}", "topic": topic,
                       "status": "open", "sla_hours": 48}, ensure_ascii=False)


FUNCTIONS = {
    "search_docs": search_docs,
    "get_price": get_price,
    "lookup_warning_code": lookup_warning_code,
    "check_service_slots": check_service_slots,
    "open_ticket": open_ticket,
}

TOOLS = [
    {"name": "search_docs",
     "description": "Search the company documents (models, warranty, service, accessories, FAQ). Returns document text.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "get_price",
     "description": "Price of a model from the 2025 price list. model_code is like OF-320.",
     "input_schema": {"type": "object", "properties": {"model_code": {"type": "string"}}, "required": ["model_code"]}},
    {"name": "lookup_warning_code",
     "description": "Meaning of a dashboard warning code, like E-23, and what the driver should do.",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "check_service_slots",
     "description": "Free service appointments in the garages. branch is optional: תל אביב, חיפה or באר שבע.",
     "input_schema": {"type": "object", "properties": {"branch": {"type": "string"}}}},
    {"name": "open_ticket",
     "description": "Open a customer-service ticket in the CRM, for complaints or requests a human must handle.",
     "input_schema": {"type": "object", "properties": {"topic": {"type": "string"}, "details": {"type": "string"}},
                      "required": ["topic"]}},
]


def run_tool(ctx: ToolContext, name: str, args: dict) -> str:
    if name not in FUNCTIONS:
        raise ToolError(f"unknown tool: {name}")
    return FUNCTIONS[name](ctx, **args)


MODEL_CODE = re.compile(r"OF-?(?:210|320|450H|450|E5|E7|V8)", re.I)
WARNING_CODE = re.compile(r"E-?\d{2}", re.I)
