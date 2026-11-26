"""Saving conversations to SQLite."""
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from agent.agent import Conversation, TurnResult

SCHEMA = Path(__file__).with_name("schema.sql")


def iso(t: float | None = None) -> str:
    dt = datetime.fromtimestamp(t, tz=timezone.utc) if t is not None else datetime.now(timezone.utc)
    return dt.isoformat(timespec="milliseconds")


def connect(path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("pragma foreign_keys = on")
    con.executescript(SCHEMA.read_text(encoding="utf-8"))
    return con


def save_user(con, user_id: str, segment: str = "private", at: float | None = None) -> None:
    con.execute("insert or ignore into users (id, segment, created_at) values (?, ?, ?)", (user_id, segment, iso(at)))


def save_conversation(con, conv: Conversation, release: str, at: float | None = None) -> None:
    con.execute(
        "insert or ignore into conversations (id, user_id, channel, release, started_at, last_at) values (?, ?, ?, ?, ?, ?)",
        (conv.id, conv.user_id, conv.channel, release, iso(at), iso(at)))


def save_turn(con, conv: Conversation, user_text: str, result: TurnResult) -> None:
    """Saves one turn: the user's message, the answer, and every step, in one transaction.

    - messages: two rows (role 'user' and role 'assistant'), both with turn = conv.turns and the trace_id.
    - steps: one row per item in result.steps, with seq = its position (0, 1, 2, ...).
    - conversations: update last_at and turns.
    Times are saved as ISO text: iso(result.started_at), iso(step.started_at).
    """
    with con:
        trace_id = result.trace_id
        con.execute(
            "insert into messages (conversation_id, turn, role, content, trace_id, created_at) "
            "values (?, ?, 'user', ?, ?, ?)",
            (conv.id, conv.turns, user_text, trace_id, iso(result.started_at)),
        )
        con.execute(
            "insert into messages (conversation_id, turn, role, content, trace_id, created_at) "
            "values (?, ?, 'assistant', ?, ?, ?)",
            (
                conv.id,
                conv.turns,
                result.answer,
                trace_id,
                iso(result.started_at + result.duration_ms / 1000),
            ),
        )
        for seq, step in enumerate(result.steps):
            con.execute(
                "insert into steps "
                "(conversation_id, turn, trace_id, seq, kind, name, started_at, duration_ms, status, error, "
                "input_tokens, output_tokens, cache_read_tokens, cost_usd, args_hash) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conv.id,
                    conv.turns,
                    trace_id,
                    seq,
                    step.kind,
                    step.name,
                    iso(step.started_at),
                    step.duration_ms,
                    step.status,
                    step.error or None,
                    step.input_tokens,
                    step.output_tokens,
                    step.cache_read_tokens,
                    step.cost_usd,
                    step.args_hash or None,
                ),
            )
        con.execute(
            "update conversations set last_at = ?, turns = ? where id = ?",
            (
                iso(result.started_at + result.duration_ms / 1000),
                conv.turns,
                conv.id,
            ),
        )


def save_feedback(con, conv_id: str, turn: int, trace_id: str | None, name: str, value: float,
                  comment: str = "", at: float | None = None) -> None:
    with con:
        con.execute("insert into feedback (conversation_id, turn, trace_id, name, value, comment, created_at) "
                    "values (?, ?, ?, ?, ?, ?, ?)", (conv_id, turn, trace_id, name, value, comment or None, iso(at)))
