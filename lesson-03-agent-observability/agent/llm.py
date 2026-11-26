"""Two interchangeable "models" with the same interface:

- SimLLM: a simulated model. No key, no cost. It picks tools by simple rules, but its token counts,
  waits and mistakes (retrying a failing tool again and again) behave like a real agent's.
- ClaudeLLM: the real Claude API (LLM_BACKEND=anthropic, needs ANTHROPIC_API_KEY).

Both return an LLMResponse whose usage uses Langfuse's keys: input, output, cache_read_input_tokens,
cache_creation_input_tokens.
"""
import json
import random
import re
from dataclasses import dataclass

from agent.config import Clock, LIVE, MODEL
from agent.tools import MODEL_CODE, WARNING_CODE


@dataclass
class LLMResponse:
    content: list[dict]        # Claude-style blocks: {"type": "text"} / {"type": "tool_use"}
    usage: dict
    stop_reason: str
    model: str
    ttft: float = 0.0          # seconds until the first token


def estimate_tokens(text: str) -> int:
    """Hebrew takes more tokens per character than English. Close enough for a simulation."""
    heb = sum(1 for ch in text if "֐" <= ch <= "׿")
    return int(heb / 2.2 + (len(text) - heb) / 3.8) + 1


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for b in content:
        if b.get("type") == "text":
            parts.append(b["text"])
        elif b.get("type") == "tool_use":
            parts.append(json.dumps(b["input"], ensure_ascii=False))
        elif b.get("type") == "tool_result":
            parts.append(b["content"] if isinstance(b["content"], str) else json.dumps(b["content"], ensure_ascii=False))
    return "\n".join(parts)


# ----------------------------------------------------------------------------------------------------------
class SimLLM:
    def __init__(self, model: str = MODEL, clock: Clock = LIVE, rng: random.Random | None = None):
        self.model, self.clock, self.rng = model, clock, rng or random.Random()
        self._cache_warm_until = -1.0

    def complete(self, system: str, messages: list[dict], tools: list[dict], cache: bool = False) -> LLMResponse:
        prefix = estimate_tokens(system) + estimate_tokens(json.dumps(tools, ensure_ascii=False))
        rest = sum(estimate_tokens(text_of(m["content"])) for m in messages)
        usage = {"input": prefix + rest, "output": 0}
        if cache:
            usage["input"] = rest
            if self.clock.now() < self._cache_warm_until:
                usage["cache_read_input_tokens"] = prefix
            else:
                usage["cache_creation_input_tokens"] = prefix
            self._cache_warm_until = self.clock.now() + 300

        content = self._decide(messages)
        thinking = self.rng.randint(20, 120)       # hidden reasoning is billed as output too
        usage["output"] = thinking + sum(estimate_tokens(text_of([b])) + (25 if b["type"] == "tool_use" else 0)
                                         for b in content)
        ttft = 0.35 + (usage["input"] + usage.get("cache_creation_input_tokens", 0)) * 0.00004
        ttft += usage.get("cache_read_input_tokens", 0) * 0.000005
        if self.rng.random() < 0.04:
            ttft *= self.rng.uniform(3, 6)          # a busy moment at the provider
        self.clock.sleep(ttft + usage["output"] / 75)
        stop = "tool_use" if any(b["type"] == "tool_use" for b in content) else "end_turn"
        return LLMResponse(content, usage, stop, self.model, ttft)

    # ---- the "model": simple rules over the conversation ------------------------------------------------
    def _decide(self, messages: list[dict]) -> list[dict]:
        start = max(i for i, m in enumerate(messages) if m["role"] == "user" and isinstance(m["content"], str))
        question = messages[start]["content"]
        calls, results = [], {}
        for m in messages[start + 1:]:
            for b in m["content"] if isinstance(m["content"], list) else []:
                if b.get("type") == "tool_use":
                    calls.append(b)
                elif b.get("type") == "tool_result":
                    results[b["tool_use_id"]] = b
        if calls:
            last = calls[-1]
            res = results.get(last["id"], {})
            if res.get("is_error") and last["name"] == "open_ticket" and "Do not retry" not in res.get("content", ""):
                # The classic agent mistake: the same call again, with the same arguments.
                return [{"type": "text", "text": "המערכת לא ענתה, מנסה שוב."}, self._call(last["name"], last["input"])]
            nxt = self._follow_up(question, calls, results)
            if nxt:
                return [nxt]
            return [{"type": "text", "text": self._answer(question, calls, results)}]
        first = self._first_tool(question)
        if first is None:
            return [{"type": "text", "text": "בשמחה! אם יש עוד שאלה על הרכב, אני כאן."}]
        return [first]

    def _call(self, name: str, args: dict) -> dict:
        return {"type": "tool_use", "id": f"toolu_{self.rng.getrandbits(48):012x}", "name": name, "input": args}

    def _first_tool(self, q: str) -> dict | None:
        if re.search(r"תודה|מעולה|יופי", q) and len(q) < 25:
            return None
        if m := WARNING_CODE.search(q):
            code = m.group(0).upper()
            return self._call("lookup_warning_code", {"code": code if "-" in code else code[0] + "-" + code[1:]})
        if re.search(r"תלונה|נציג|לא מרוצה|פנייה|לבטל|החזר כספי", q):
            return self._call("open_ticket", {"topic": "תלונת לקוח", "details": q[:200]})
        if re.search(r"תור|לקבוע|זמין|פנוי", q):
            branch = next((b for b in ("תל אביב", "חיפה", "באר שבע") if b in q), "")
            return self._call("check_service_slots", {"branch": branch} if branch else {})
        if (m := MODEL_CODE.search(q)) and re.search(r"מחיר|עולה|כמה|עלות", q):
            code = m.group(0).upper().replace("OF", "OF-").replace("--", "-")
            return self._call("get_price", {"model_code": code})
        return self._call("search_docs", {"query": q})

    def _follow_up(self, q: str, calls: list[dict], results: dict) -> dict | None:
        names = [c["name"] for c in calls]
        last = calls[-1]
        res = results.get(last["id"], {})
        if last["name"] == "lookup_warning_code" and not res.get("is_error") and "מוסך" in res.get("content", "") \
                and "check_service_slots" not in names:
            return self._call("check_service_slots", {})
        if last["name"] == "get_price" and re.search(r"אביזר|גגון|וו גרירה|מטען", q) and "search_docs" not in names:
            return self._call("search_docs", {"query": q})
        return None

    def _answer(self, q: str, calls: list[dict], results: dict) -> str:
        out = []
        for c in calls:
            res = results.get(c["id"], {})
            body = res.get("content", "")
            if res.get("is_error"):
                if c["name"] == "open_ticket":
                    continue
                out.append("לא הצלחתי למצוא את המידע הזה במערכת.")
                continue
            if c["name"] == "get_price":
                d = json.loads(body)
                out.append(f"המחיר של {d['model']} ({d['code']}) הוא {d['basic']} ש״ח בגימור בסיסי"
                           + (f" ו־{d['full']} ש״ח בגימור מלא" if d["full"] != "אין" else "")
                           + ". המחיר כולל מע״מ ואגרת רישוי לשנה הראשונה [מחירון 2025].")
            elif c["name"] == "lookup_warning_code":
                d = json.loads(body)
                out.append(f"קוד {d['code']} ({d['light']}) פירושו {d['meaning']}. מה לעשות: {d['action']} [קודי התרעה].")
            elif c["name"] == "check_service_slots":
                d = json.loads(body)
                out.append("התורים הפנויים הקרובים: " + "; ".join(f"{b}: {s[0]}" for b, s in d.items()) + ".")
            elif c["name"] == "open_ticket":
                d = json.loads(body)
                out.append(f"פתחתי פנייה מספר {d['ticket_id']}. נציג יחזור אלייך תוך {d['sla_hours']} שעות.")
            elif c["name"] == "search_docs":
                out.append(self._from_docs(q, body))
        tickets = [c for c in calls if c["name"] == "open_ticket"]
        if tickets and all(results.get(c["id"], {}).get("is_error") for c in tickets):
            out.append("מצטערת, מערכת הפניות לא זמינה כרגע. אפשר לפנות למוקד בטלפון *8000.")
        return " ".join(out) or "מצטערת, לא מצאתי תשובה לשאלה הזו."

    def _from_docs(self, q: str, docs: str) -> str:
        """Picks the sentence that shares the most words with the question."""
        from agent.kb import tokens
        q_words = set(tokens(q))
        best, best_score = None, 0
        for doc_id, body in re.findall(r'<doc id="([^"]+)"[^>]*>\n(.*?)\n</doc>', docs, re.S):
            for ln in body.splitlines():
                ln = ln.strip()
                if not ln or ln.startswith("#") or len(ln) < 15:
                    continue
                if ln.startswith("|"):
                    ln = " · ".join(c.strip() for c in ln.strip("|").split("|"))
                score = len(q_words & set(tokens(ln)))
                if score > best_score:
                    best, best_score = (ln, doc_id), score
        if not best:
            return "לא מצאתי במסמכים תשובה לשאלה הזו."
        return f"{best[0]} [{best[1]}]"


# ----------------------------------------------------------------------------------------------------------
SYSTEM_SUFFIX_FOR_CLAUDE = "\nענה בעברית, בשלושה משפטים לכל היותר."


class ClaudeLLM:
    def __init__(self, model: str = MODEL):
        import anthropic
        try:
            # Our own Langfuse spans already record each call, so the SDK's built-in tracing stays off.
            self.client = anthropic.Anthropic(open_telemetry=False)
        except TypeError:                       # older SDK without built-in tracing
            self.client = anthropic.Anthropic()
        self.model = model

    def complete(self, system: str, messages: list[dict], tools: list[dict], cache: bool = False) -> LLMResponse:
        sys_block = {"type": "text", "text": system + SYSTEM_SUFFIX_FOR_CLAUDE}
        if cache:
            sys_block["cache_control"] = {"type": "ephemeral"}
        resp = self.client.messages.create(
            model=self.model, max_tokens=4000, system=[sys_block], messages=messages, tools=tools,
            output_config={"effort": "low"},
        )
        u = resp.usage
        usage = {"input": u.input_tokens, "output": u.output_tokens}
        if getattr(u, "cache_read_input_tokens", 0):
            usage["cache_read_input_tokens"] = u.cache_read_input_tokens
        if getattr(u, "cache_creation_input_tokens", 0):
            usage["cache_creation_input_tokens"] = u.cache_creation_input_tokens
        content = [b.model_dump(mode="json", exclude_none=True) for b in resp.content]
        if resp.stop_reason == "refusal":
            content = [{"type": "text", "text": "מצטערת, לא אוכל לעזור בבקשה הזו."}]
        return LLMResponse(content, usage, resp.stop_reason, resp.model)


def make_llm(backend: str, clock: Clock = LIVE, rng: random.Random | None = None):
    if backend == "anthropic":
        return ClaudeLLM()
    return SimLLM(clock=clock, rng=rng)
