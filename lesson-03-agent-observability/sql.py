"""Runs one SQL query on the database and prints the result as a table.

    python sql.py "select count(*) from conversations"
    python sql.py "select name, count(*), avg(duration_ms) from steps group by name" --db runs/demo.db
"""
import argparse
import sqlite3
import sys

from agent.config import DEFAULT_DB

sys.stdout.reconfigure(encoding="utf-8")
parser = argparse.ArgumentParser()
parser.add_argument("query")
parser.add_argument("--db", default=str(DEFAULT_DB))
parser.add_argument("--limit", type=int, default=40)
args = parser.parse_args()

con = sqlite3.connect(args.db)
cur = con.execute(args.query)
if cur.description is None:
    con.commit()
    sys.exit(f"ok ({cur.rowcount} rows changed)")
cols = [d[0] for d in cur.description]
rows = [[("" if v is None else f"{v:.4f}" if isinstance(v, float) else str(v))[:60] for v in r]
        for r in cur.fetchmany(args.limit)]
widths = [max(len(c), *(len(r[i]) for r in rows)) if rows else len(c) for i, c in enumerate(cols)]
print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
print("  ".join("-" * w for w in widths))
for r in rows:
    print("  ".join(v.ljust(w) for v, w in zip(r, widths)))
print(f"({len(rows)} rows{'+' if len(rows) == args.limit else ''})")
