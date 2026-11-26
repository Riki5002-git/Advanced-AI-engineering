"""Runs one simulated conversation through the agent, saves it, and adds quality scores."""
import random
import re

from agent.agent import Agent, Conversation, TurnResult
from agent.tracing import langfuse_online
from db import store

_warned = False


def quality_scores(result: TurnResult, rng: random.Random) -> dict[str, float]:
    """Two kinds of quality signal for each turn:
    - has_source: an automatic check. Does the answer point to a source in [brackets]?
    - user_feedback: the simulated customer's thumbs up (1) or down (0)."""
    has_source = 1.0 if re.search(r"\[[^\]]+\]|פנייה מספר|התורים הפנויים", result.answer) else 0.0
    bad = (result.stopped_by_guard or "לא זמינה" in result.answer or "לא מצאתי" in result.answer
           or "לא הצלחתי" in result.answer or result.duration_ms > 15000)
    p_up = 0.25 if bad else (0.92 if has_source else 0.7)
    return {"has_source": has_source, "user_feedback": 1.0 if rng.random() < p_up else 0.0}


def save_turn_safely(con, conv, text, result) -> None:
    global _warned
    try:
        store.save_turn(con, conv, text, result)
    except NotImplementedError as e:
        if not _warned:
            print(f"  (not saved to the database yet: {e})")
            _warned = True


def run_conversation(agent: Agent, con, script: list[str], user_id: str, segment: str, rng: random.Random,
                     on_turn=None, send_scores: bool = True, before_save=None, conv_id: str | None = None) -> Conversation:
    conv = Conversation(user_id=user_id, channel=rng.choice(["web", "web", "app"]))
    if conv_id:
        conv.id = conv_id
    store.save_user(con, user_id, segment, at=agent.clock.now())
    store.save_conversation(con, conv, agent.release, at=agent.clock.now())
    con.commit()
    for text in script:
        result = agent.run_turn(conv, text)
        if before_save:
            before_save(conv, text, result)
        save_turn_safely(con, conv, text, result)
        for name, value in quality_scores(result, rng).items():
            if rng.random() < 0.6 or name != "user_feedback":     # not every customer clicks thumbs up/down
                store.save_feedback(con, conv.id, conv.turns, result.trace_id, name, value,
                                    at=result.started_at + result.duration_ms / 1000)
                if send_scores and langfuse_online() and result.trace_id:
                    agent.lf.create_score(trace_id=result.trace_id, name=name, value=value, data_type="BOOLEAN")
        if on_turn:
            on_turn(conv, text, result)
        if agent.clock.virtual:
            agent.clock.sleep(rng.uniform(4, 25))      # the customer reads and types (demo history only)
    return conv
