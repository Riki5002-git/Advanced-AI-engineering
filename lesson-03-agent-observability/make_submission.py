"""Checks that the submission is complete, and builds the zip to upload on the course site.

    python make_submission.py

It never puts .env (your keys), runs/ (databases and spans) or caches in the zip, and it stops if it finds a
Langfuse or Claude key inside any file. The zip is created next to this folder: ../lesson-03-submission.zip
"""
import re
import sys
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
OUT = ROOT.parent / "lesson-03-submission.zip"
SKIP_DIRS = {"runs", "__pycache__", ".venv", "venv", ".git", "node_modules", "dist", ".idea", ".vscode"}
IMAGES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
SECRET = re.compile(r"sk-lf-[0-9a-f]{8}|sk-ant-[A-Za-z0-9_-]{12}")
TRACE_LINK = re.compile(r"https://(?:[a-z0-9-]+\.)?cloud\.langfuse\.com/project/[\w-]+/traces/[\w-]+")
problems, notes = [], []


def files():
    for p in sorted(ROOT.rglob("*")):
        rel = p.relative_to(ROOT)
        if not p.is_file() or set(rel.parts[:-1]) & SKIP_DIRS:
            continue
        if p.name.startswith(".env") and p.name != ".env.example":
            continue   # your keys: never submitted
        if p.suffix in {".db", ".sqlite", ".jsonl", ".zip", ".pyc"}:
            continue
        yield p, rel


def read(rel: str) -> str:
    p = ROOT / rel
    return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""


def still_raises(body: str) -> bool:
    """A live `raise NotImplementedError` line (not one left in a comment)."""
    return bool(re.search(r"^[ \t]*raise NotImplementedError", body, re.M))


# ---- the code (steps 2-4) ------------------------------------------------------------------------------
agent = read("agent/agent.py")
for kind, step in (("agent", "2a"), ("generation", "2b"), ("tool", "2c")):
    if f'as_type="{kind}"' not in agent and f"as_type='{kind}'" not in agent:
        problems.append(f"step {step}: no observation with as_type=\"{kind}\" in agent/agent.py")
if still_raises(read("db/store.py").split("def save_turn", 1)[-1].split("\ndef ", 1)[0]):
    problems.append("step 3: save_turn in db/store.py still raises NotImplementedError")
transform = read("analytics/transform.py")
for fn, step in (("percentile", "4a"), ("find_loops", "4b")):
    body = transform.split(f"def {fn}", 1)[-1].split("\ndef ", 1)[0]
    if still_raises(body):
        problems.append(f"step {step}: {fn} in analytics/transform.py still raises NotImplementedError")

# ---- the report and the evidence -------------------------------------------------------------------------
report = read("REPORT.md")
if not re.search(r"^שם:[ \t]*\S", report, re.M):
    problems.append("REPORT.md: write your name on the line that starts with שם:")
links = set(TRACE_LINK.findall(report))
if len(links) < 2:
    problems.append(f"REPORT.md: {len(links)} of 2 public trace links. In the trace: ... menu -> Make Public -> Copy share link")
empty_rows = len(re.findall(r"^\|[^|\n]*\|\s*\|\s*$", report, re.M))
if empty_rows:
    notes.append(f"REPORT.md: {empty_rows} table rows are still empty")
if not re.search(r"```sql[ \t]*\n\s*[^`\s]", report):   # a query, not just the closing ```
    problems.append("REPORT.md: part 2 has no SQL query")
images = [rel for p, rel in files() if p.suffix.lower() in IMAGES]
if not images:
    problems.append("no screenshot: save a screenshot of one session (Langfuse -> Sessions) in the folder screenshots/")

# ---- keys must never be submitted ----------------------------------------------------------------------
leaks = [str(rel) for p, rel in files() if p.suffix.lower() not in IMAGES and p.stat().st_size < 2_000_000
         and SECRET.search(p.read_text(encoding="utf-8", errors="ignore"))]
if leaks:
    print("STOP: a secret key was found in: " + ", ".join(leaks))
    print("Remove it from the file (keys belong only in .env), and run again. No zip was created.")
    sys.exit(1)

# ---- the zip -------------------------------------------------------------------------------------------
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    count = 0
    for p, rel in files():
        z.write(p, Path(ROOT.name) / rel)
        count += 1
size = OUT.stat().st_size / 1024

print(f"Checks: {len(links)} trace links, {len(images)} screenshots, code steps 2-4 "
      f"{'look done' if not any(x.startswith('step') for x in problems) else 'NOT done'}.")
for p in problems:
    print("  ✗ " + p)
for n in notes:
    print("  · " + n)
print(f"\nCreated {OUT}  ({count} files, {size:.0f} KB). Upload it on the exercise page of the course site.")
if problems:
    print("You can upload it as is, but the ✗ lines will cost points. Fix them and run again to replace the zip.")
