"""Extract: read the raw data, from our database or from Langfuse, into plain lists of dicts.
Both sources produce the same shape, so the rest of the pipeline does not care where the data came from."""
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone


def from_db(path) -> dict:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    rows = lambda sql: [dict(r) for r in con.execute(sql)]  # noqa: E731
    steps = rows("select * from steps order by conversation_id, turn, seq")
    turns = rows("""
        select q.conversation_id, q.turn, q.trace_id, q.created_at as started_at, a.created_at as ended_at,
               q.content as question, a.content as answer, c.user_id, c.release, c.channel, u.segment
        from messages q
        join messages a on a.conversation_id = q.conversation_id and a.turn = q.turn and a.role = 'assistant'
        join conversations c on c.id = q.conversation_id
        join users u on u.id = c.user_id
        where q.role = 'user'
        order by q.created_at""")
    feedback = rows("select * from feedback")
    con.close()
    return {"steps": steps, "turns": turns, "feedback": feedback, "source": f"db:{path}"}


def _get(o, *names, default=None):
    for n in names:
        v = o.get(n) if isinstance(o, dict) else getattr(o, n, None)
        if v is not None:
            return v
    return default


def _iso(v) -> str:
    return v.isoformat() if isinstance(v, datetime) else str(v)


def from_langfuse(days: int = 14) -> dict:
    """The same data from Langfuse's public API (observations), for the last `days` days.
    Needs LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY. Large projects should use Langfuse's blob-storage export."""
    from agent.tracing import get_langfuse
    lf = get_langfuse()
    since = datetime.now(timezone.utc) - timedelta(days=days)
    obs, cursor = [], None
    while True:
        page = lf.api.observations.get_many(from_start_time=since, limit=1000, cursor=cursor,
                                            fields="core,basic,usage,io,metadata")
        data = _get(page, "data", default=[])
        obs.extend(data)
        cursor = _get(_get(page, "meta", default={}), "cursor")
        if not cursor or not data:
            break

    by_trace = defaultdict(list)
    for o in obs:
        by_trace[_get(o, "traceId", "trace_id")].append(o)
    steps, turns = [], []
    for trace_id, items in by_trace.items():
        items.sort(key=lambda o: _iso(_get(o, "startTime", "start_time")))
        root = next((o for o in items if _get(o, "type", default="").upper() == "AGENT"), None)
        if root is None:
            continue
        conv = _get(root, "sessionId", "session_id", default=trace_id)
        meta = _get(root, "metadata", default={}) or {}
        turn = int(_get(meta, "turn", default=1) or 1)
        start, end = _iso(_get(root, "startTime", "start_time")), _iso(_get(root, "endTime", "end_time"))
        turns.append({"conversation_id": conv, "turn": turn, "trace_id": trace_id, "started_at": start,
                      "ended_at": end, "question": _get(root, "input", default=""),
                      "answer": _get(root, "output", default=""), "user_id": _get(root, "userId", "user_id"),
                      "release": _get(root, "version", default=""), "channel": "", "segment": ""})
        for seq, o in enumerate(x for x in items if x is not root):
            kind = "llm" if _get(o, "type", default="").upper() == "GENERATION" else "tool"
            usage = _get(o, "usageDetails", "usage_details", default={}) or {}
            cost = _get(o, "costDetails", "cost_details", default={}) or {}
            s, e = _get(o, "startTime", "start_time"), _get(o, "endTime", "end_time")
            dur = (datetime.fromisoformat(_iso(e).replace("Z", "+00:00"))
                   - datetime.fromisoformat(_iso(s).replace("Z", "+00:00"))).total_seconds() * 1000 if e else 0
            args = json.dumps(_get(o, "input", default={}), ensure_ascii=False, sort_keys=True)
            steps.append({
                "conversation_id": conv, "turn": turn, "trace_id": trace_id, "seq": seq, "kind": kind,
                "name": _get(o, "model", default=_get(o, "name")) if kind == "llm" else _get(o, "name"),
                "started_at": _iso(s), "duration_ms": dur,
                "status": "error" if _get(o, "level", default="") == "ERROR" else "ok",
                "error": _get(o, "statusMessage", "status_message"),
                "input_tokens": sum(v for k, v in usage.items() if k.startswith("input") or k.startswith("cache")),
                "output_tokens": usage.get("output", 0), "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
                "cost_usd": cost.get("total", _get(o, "totalCost", "total_cost", default=0) or 0),
                "args_hash": f"{_get(o, 'name')}:{args}" if kind == "tool" else None,
            })
    return {"steps": steps, "turns": turns, "feedback": [], "source": "langfuse"}
