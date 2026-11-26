"""Load: write the computed metrics back into the database, as tables a dashboard or SQL query can read."""
import sqlite3
from datetime import datetime, timezone


def _replace(con, table: str, rows: list[dict]) -> None:
    con.execute(f"drop table if exists {table}")
    if not rows:
        return
    cols = list(rows[0])
    con.execute(f"create table {table} ({', '.join(cols)})")
    con.executemany(f"insert into {table} values ({', '.join('?' for _ in cols)})",
                    [tuple(r[c] for c in cols) for r in rows])


def to_db(path, metrics: dict) -> None:
    con = sqlite3.connect(path)
    with con:
        _replace(con, "metrics_daily", metrics["daily"])
        _replace(con, "metrics_steps", metrics["steps"])
        _replace(con, "metrics_turns", [{k: v for k, v in t.items() if k not in ("question", "answer")}
                                        for t in metrics["turns"]])
        _replace(con, "metrics_runs", [{"computed_at": datetime.now(timezone.utc).isoformat(), "source": metrics["source"],
                                        **metrics["kpis"]}])
    con.close()
