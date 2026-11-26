"""The customer-service agent: a model, five tools, and a loop.

Every LLM call and tool call is also recorded as a Step (time, tokens, cost, status). The steps are saved to the
database in step 3 and analysed by the pipeline in step 4. The Langfuse code is what you add in step 2.
"""
import hashlib
import json
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from langfuse import propagate_attributes

from agent.config import LIVE, LLM_BACKEND, RELEASE, Clock
from agent.kb import KnowledgeBase
from agent.llm import make_llm
from agent.pricing import cost_details
from agent.prompts import SYSTEM
from agent.tools import TOOLS, ToolContext, ToolError, run_tool
from agent.tracing import get_langfuse

# ---- settings you will measure in step 5 -------------------------------------------------------------
MAX_STEPS = 12            # the most LLM calls in one turn, before the loop is stopped
REPEAT_GUARD = False      # stop a tool call that already failed twice with the same arguments
PROMPT_CACHING = False    # cache the system prompt and the tool definitions
# -------------------------------------------------------------------------------------------------------

_KB = None


def knowledge_base() -> KnowledgeBase:
    global _KB
    if _KB is None:
        _KB = KnowledgeBase()
    return _KB


@dataclass
class Step:
    kind: str                 # "llm" or "tool"
    name: str
    started_at: float         # seconds since 1970 (UTC)
    duration_ms: float
    status: str = "ok"        # "ok" or "error"
    error: str = ""
    input_tokens: int = 0     # all prompt tokens, cached ones included
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    args: str = ""            # tool arguments as JSON
    args_hash: str = ""       # same tool + same arguments = same hash (used to find loops)
    # kept for seed_demo.py, which rebuilds the traces from the steps:
    output: object = None
    usage: dict = field(default_factory=dict)
    ttft: float = 0.0
    n_messages: int = 0       # how many messages the model saw


@dataclass
class TurnResult:
    answer: str
    started_at: float
    duration_ms: float
    steps: list[Step]
    trace_id: str | None = None
    stopped_by_guard: bool = False

    @property
    def cost_usd(self) -> float:
        return round(sum(s.cost_usd for s in self.steps), 8)

    @property
    def tool_errors(self) -> int:
        return sum(1 for s in self.steps if s.kind == "tool" and s.status == "error")


@dataclass
class Conversation:
    user_id: str
    id: str = field(default_factory=lambda: "conv_" + uuid.uuid4().hex[:12])
    channel: str = "web"
    messages: list[dict] = field(default_factory=list)
    turns: int = 0


def _ts(t: float) -> datetime:
    return datetime.fromtimestamp(t, tz=timezone.utc)


class Agent:
    def __init__(self, backend: str = LLM_BACKEND, clock: Clock = LIVE, rng: random.Random | None = None,
                 tool_ctx: ToolContext | None = None, release: str = RELEASE, langfuse=None):
        self.clock = clock
        self.rng = rng or random.Random()
        self.llm = make_llm(backend, clock=clock, rng=self.rng)
        self.tool_ctx = tool_ctx or ToolContext(kb=knowledge_base(), clock=clock, rng=self.rng)
        self.release = release
        self.lf = langfuse if langfuse is not None else get_langfuse()

    # ---- one user message -> one answer ---------------------------------------------------------------
    def run_turn(self, conv: Conversation, user_text: str) -> TurnResult:
        conv.turns += 1
        conv.messages.append({"role": "user", "content": user_text})
        with propagate_attributes(user_id=conv.user_id, session_id=conv.id):
            with self.lf.start_as_current_observation(
                as_type="agent", name="customer_turn", input=user_text,
            ) as observation:
                result = self._loop(conv)
                result.trace_id = getattr(observation, "trace_id", None)
                if result.trace_id is None:
                    result.trace_id = self.lf.get_current_trace_id()
                observation.update(output=result.answer)
        return result

    def _loop(self, conv: Conversation) -> TurnResult:
        start = self.clock.now()
        self._steps: list[Step] = []
        self._failures: dict[str, int] = {}
        answer, stopped = "", False
        for _ in range(MAX_STEPS):
            resp = self._call_llm(conv)
            conv.messages.append({"role": "assistant", "content": resp.content})
            tool_uses = [b for b in resp.content if b.get("type") == "tool_use"]
            if not tool_uses:
                answer = " ".join(b["text"] for b in resp.content if b.get("type") == "text")
                break
            results = []
            for block in tool_uses:
                name, args = block["name"], block["input"]
                output, error = self._exec_tool(name, args)
                results.append({"type": "tool_result", "tool_use_id": block["id"], "content": output,
                                **({"is_error": True} if error else {})})
            conv.messages.append({"role": "user", "content": results})
        else:
            stopped = True
            answer = "מצטערת, נתקלתי בבעיה ולא הצלחתי להשלים את הבקשה. אפשר לפנות למוקד בטלפון *8000."
            conv.messages.append({"role": "assistant", "content": [{"type": "text", "text": answer}]})
        end = self.clock.now()
        return TurnResult(answer=answer, started_at=start, duration_ms=round((end - start) * 1000, 1),
                          steps=self._steps, stopped_by_guard=stopped)

    # ---- the two kinds of steps -----------------------------------------------------------------------
    def _call_llm(self, conv: Conversation):
        t0 = self.clock.now()
        step = Step(kind="llm", name=self.llm.model, started_at=t0, duration_ms=0, n_messages=len(conv.messages))
        self._steps.append(step)
        with self.lf.start_as_current_observation(
            as_type="generation", name=f"llm:{self.llm.model}", model=self.llm.model,
            input=conv.messages,
        ) as observation:
            resp = self.llm.complete(SYSTEM, conv.messages, TOOLS, cache=PROMPT_CACHING)
            u = resp.usage
            step.output, step.usage, step.ttft = resp.content, u, resp.ttft
            step.duration_ms = round((self.clock.now() - t0) * 1000, 1)
            step.input_tokens = u["input"] + u.get("cache_read_input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
            step.output_tokens = u["output"]
            step.cache_read_tokens = u.get("cache_read_input_tokens", 0)
            costs = cost_details(resp.model, u)
            step.cost_usd = costs["total"]
            observation.update(
                output=resp.content,
                model=resp.model,
                usage_details={
                    "input": step.input_tokens,
                    "output": step.output_tokens,
                    "cache_read_input_tokens": step.cache_read_tokens,
                    "cache_creation_input_tokens": u.get("cache_creation_input_tokens", 0),
                },
                cost_details=costs,
            )
        return resp

    def _exec_tool(self, name: str, args: dict) -> tuple[str, str]:
        """Runs a tool. Returns (output for the model, error message or '')."""
        args_json = json.dumps(args, ensure_ascii=False, sort_keys=True)
        key = hashlib.sha1(f"{name}:{args_json}".encode()).hexdigest()[:12]
        if REPEAT_GUARD and self._failures.get(key, 0) >= 2:
            msg = "blocked: this exact call already failed twice. Do not retry; tell the customer."
            return msg, msg
        t0 = self.clock.now()
        step = Step(kind="tool", name=name, started_at=t0, duration_ms=0, args=args_json, args_hash=key)
        self._steps.append(step)
        with self.lf.start_as_current_observation(
            as_type="tool", name=name, input=args,
        ) as observation:
            try:
                output, error = run_tool(self.tool_ctx, name, args), ""
            except ToolError as e:
                output, error = f"error: {e}", str(e)
                self._failures[key] = self._failures.get(key, 0) + 1
            step.duration_ms = round((self.clock.now() - t0) * 1000, 1)
            step.output = output
            if error:
                step.status, step.error = "error", error
                observation.update(
                    output=output,
                    level="ERROR",
                    status_message=error,
                )
            else:
                observation.update(output=output)
        return output, error
