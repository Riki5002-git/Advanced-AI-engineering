"""Checks that everything is ready: Python, packages, Langfuse keys, and (optionally) the Claude key.

    python check_setup.py
"""
import importlib.util
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ok = True


def line(good: bool, text: str, fix: str = "") -> None:
    global ok
    ok = ok and good
    print(("  ✓ " if good else "  ✗ ") + text + ("" if good or not fix else f"\n      → {fix}"))


print("Python and packages")
line(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]}", "Python 3.10 or newer is needed")
for pkg in ("langfuse", "opentelemetry.sdk"):
    line(importlib.util.find_spec(pkg) is not None, pkg, "pip install -r requirements.txt")
if importlib.util.find_spec("langfuse"):
    from importlib.metadata import version
    print(f"      langfuse {version('langfuse')}")

from agent.config import LLM_BACKEND  # noqa: E402  (reads .env)
from agent.tracing import langfuse_online  # noqa: E402

print("\nLangfuse")
if langfuse_online():
    base = os.environ.get("LANGFUSE_BASE_URL") or os.environ.get("LANGFUSE_HOST") or "https://cloud.langfuse.com"
    print(f"      {base}")
    try:
        from agent.tracing import get_langfuse
        line(get_langfuse().auth_check(), "the keys work", "check LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY and the region "
             "(EU: https://cloud.langfuse.com, US: https://us.cloud.langfuse.com)")
    except Exception as e:
        line(False, f"could not reach Langfuse: {e}", "check the internet connection and LANGFUSE_BASE_URL")
else:
    from agent.config import ROOT
    if (ROOT / ".env.txt").exists() and not (ROOT / ".env").exists():
        line(False, "found .env.txt instead of .env", "rename it: ren .env.txt .env  (Mac/Linux: mv .env.txt .env)")
    print("  · no keys: offline mode. Traces go to runs/spans.jsonl (python trace_view.py).")
    print("    To connect: copy .env.example to .env and fill in the keys from Langfuse → Settings → API Keys.")

print("\nModel")
if LLM_BACKEND == "anthropic":
    line(importlib.util.find_spec("anthropic") is not None, "anthropic package", "pip install anthropic")
    line(bool(os.environ.get("ANTHROPIC_API_KEY")), "ANTHROPIC_API_KEY is set", "set it in the terminal or in .env")
else:
    print("  · LLM_BACKEND=sim: the simulated model. No key and no cost.")

print("\n" + ("Ready." if ok else "Fix the ✗ lines and run again."))
