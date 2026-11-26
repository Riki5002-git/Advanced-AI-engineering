"""Transform: from raw steps and turns to metrics. Pure Python, no database, so each function is easy to test."""
from collections import Counter, defaultdict
from datetime import datetime

LOOP_THRESHOLD = 3   # the same tool with the same arguments, 3 times or more in one turn = a loop


def percentile(values: list[float], p: float) -> float:
    """TODO (step 4): the p-th percentile (p between 0 and 100) of values, with linear interpolation.

    Sort the values. The position is (n - 1) * p / 100. If it falls between two values, take the point
    between them in the same proportion. percentile([1, 2, 3, 4], 50) == 2.5. An empty list returns 0.
    """
    if not values:
        return 0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * p / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def find_loops(turn_steps: list[dict]) -> int:
    """TODO (step 4): how many times the most repeated tool call in this turn was made.

    A tool call is identified by args_hash (tool name + arguments). Only steps with kind == "tool" count.
    Returns 0 if the turn has no tool calls. A result of LOOP_THRESHOLD or more means the agent was looping.
    """
    hashes = Counter(
        step.get("args_hash")
        for step in turn_steps
        if step.get("kind") == "tool" and step.get("args_hash")
    )
    return max(hashes.values(), default=0)


def _t(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _rate(part: float, whole: float) -> float:
    return round(part / whole, 4) if whole else 0.0


def build_turns(raw: dict) -> list[dict]:
    """One row per turn, with everything that happened inside it."""
    steps_by_turn = defaultdict(list)
    for s in raw["steps"]:
        steps_by_turn[(s["conversation_id"], s["turn"])].append(s)
    fb = defaultdict(dict)
    for f in raw["feedback"]:
        fb[(f["conversation_id"], f["turn"])][f["name"]] = f["value"]
    out = []
    for t in raw["turns"]:
        st = steps_by_turn.get((t["conversation_id"], t["turn"]), [])
        llm = [s for s in st if s["kind"] == "llm"]
        tools = [s for s in st if s["kind"] == "tool"]
        first_tool = tools[0]["name"] if tools else "chat"
        out.append({
            **t,
            "day": t["started_at"][:10],
            "duration_ms": (_t(t["ended_at"]) - _t(t["started_at"])).total_seconds() * 1000,
            "cost_usd": sum(s["cost_usd"] for s in st),
            "input_tokens": sum(s["input_tokens"] for s in llm),
            "llm_calls": len(llm),
            "tool_calls": len(tools),
            "tool_errors": sum(1 for s in tools if s["status"] == "error"),
            "max_repeat": find_loops(st),
            "intent": first_tool,
            "user_feedback": fb[(t["conversation_id"], t["turn"])].get("user_feedback"),
            "has_source": fb[(t["conversation_id"], t["turn"])].get("has_source"),
        })
    return out


def kpis(turns: list[dict]) -> dict:
    convs = {t["conversation_id"] for t in turns}
    durations = [t["duration_ms"] for t in turns]
    tool_calls = sum(t["tool_calls"] for t in turns)
    votes = [t["user_feedback"] for t in turns if t["user_feedback"] is not None]
    sourced = [t["has_source"] for t in turns if t["has_source"] is not None]
    total_cost = sum(t["cost_usd"] for t in turns)
    return {
        "conversations": len(convs),
        "turns": len(turns),
        "p50_ms": round(percentile(durations, 50)),
        "p95_ms": round(percentile(durations, 95)),
        "total_cost": round(total_cost, 4),
        "cost_per_conversation": round(total_cost / len(convs), 5) if convs else 0,
        "tool_error_rate": _rate(sum(t["tool_errors"] for t in turns), tool_calls),
        "loop_turn_rate": _rate(sum(1 for t in turns if t["max_repeat"] >= LOOP_THRESHOLD), len(turns)),
        "thumbs_up_rate": _rate(sum(votes), len(votes)),
        "has_source_rate": _rate(sum(sourced), len(sourced)),
        "feedback_count": len(votes),
    }


def daily(turns: list[dict]) -> list[dict]:
    by_day = defaultdict(list)
    for t in turns:
        by_day[t["day"]].append(t)
    rows = []
    for day in sorted(by_day):
        k = kpis(by_day[day])
        rows.append({"day": day, **k})
    return rows


def step_stats(raw: dict) -> list[dict]:
    """Per step name: how often, how slow, how expensive. Sorted by total time."""
    groups = defaultdict(list)
    for s in raw["steps"]:
        groups[("llm" if s["kind"] == "llm" else "tool", s["name"])].append(s)
    total_ms = sum(s["duration_ms"] for s in raw["steps"]) or 1
    total_cost = sum(s["cost_usd"] for s in raw["steps"]) or 1
    turn_ms = defaultdict(float)
    for s in raw["steps"]:
        turn_ms[(s["conversation_id"], s["turn"])] += s["duration_ms"]
    rows = []
    for (kind, name), items in groups.items():
        d = [s["duration_ms"] for s in items]
        in_turns = {(s["conversation_id"], s["turn"]) for s in items}
        rows.append({
            "kind": kind, "name": name, "calls": len(items),
            "p50_ms": round(percentile(d, 50)), "p95_ms": round(percentile(d, 95)),
            "total_ms": round(sum(d)), "time_share": round(sum(d) / total_ms, 4),
            "turn_share": round(sum(d) / (sum(turn_ms[k] for k in in_turns) or 1), 4),  # of the turns that use it
            "cost_usd": round(sum(s["cost_usd"] for s in items), 4),
            "cost_share": round(sum(s["cost_usd"] for s in items) / total_cost, 4),
            "error_rate": _rate(sum(1 for s in items if s["status"] == "error"), len(items)),
        })
    return sorted(rows, key=lambda r: -r["total_ms"])


def cost_after_tool(raw: dict) -> list[dict]:
    """The LLM call right after a tool reads that tool's result. Which tools make the next call expensive?"""
    by_turn = defaultdict(list)
    for s in raw["steps"]:
        by_turn[(s["conversation_id"], s["turn"])].append(s)
    groups = defaultdict(list)
    for st in by_turn.values():
        st.sort(key=lambda s: s["seq"])
        prev = "שאלת הלקוח"
        for s in st:
            if s["kind"] == "tool":
                prev = s["name"]
            else:
                groups[prev].append(s)
                prev = "after-llm"
    total = sum(s["cost_usd"] for v in groups.values() for s in v) or 1
    rows = [{"after": k, "llm_calls": len(v),
             "avg_input_tokens": round(sum(s["input_tokens"] for s in v) / len(v)),
             "cost_usd": round(sum(s["cost_usd"] for s in v), 4),
             "cost_share": round(sum(s["cost_usd"] for s in v) / total, 4)}
            for k, v in groups.items() if k != "after-llm"]
    return sorted(rows, key=lambda r: -r["cost_usd"])


def by_field(turns: list[dict], field: str) -> list[dict]:
    groups = defaultdict(list)
    for t in turns:
        groups[t.get(field) or "-"].append(t)
    rows = []
    for key, items in groups.items():
        k = kpis(items)
        rows.append({field: key, "turns": k["turns"], "p95_ms": k["p95_ms"],
                     "cost_per_turn": round(sum(t["cost_usd"] for t in items) / len(items), 5),
                     "tool_error_rate": k["tool_error_rate"], "loop_turn_rate": k["loop_turn_rate"],
                     "thumbs_up_rate": k["thumbs_up_rate"]})
    return sorted(rows, key=lambda r: -r["turns"])


def top_errors(raw: dict, n: int = 5) -> list[tuple[str, int]]:
    c = Counter(f"{s['name']}: {(s.get('error') or '')[:60]}" for s in raw["steps"] if s["status"] == "error")
    return c.most_common(n)


def loop_examples(turns: list[dict], n: int = 5) -> list[dict]:
    loops = [t for t in turns if t["max_repeat"] >= LOOP_THRESHOLD]
    return sorted(loops, key=lambda t: (-t["max_repeat"], -t["cost_usd"]))[:n]


def run(raw: dict) -> dict:
    turns = build_turns(raw)
    return {
        "source": raw["source"],
        "kpis": kpis(turns),
        "daily": daily(turns),
        "steps": step_stats(raw),
        "cost_after_tool": cost_after_tool(raw),
        "by_release": sorted(by_field(turns, "release"), key=lambda r: r["release"]),
        "by_intent": by_field(turns, "intent"),
        "top_errors": top_errors(raw),
        "loops": loop_examples(turns),
        "turns": turns,
    }
