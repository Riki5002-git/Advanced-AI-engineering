"""The Langfuse client, in one of two modes:

- Online: LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are set (in .env or in the terminal). Spans go to Langfuse.
- Offline: no keys. The same Langfuse code runs, but the spans are written to runs/spans.jsonl, and
  `python trace_view.py` prints them as a tree. Useful before you have an account, or without internet.
"""
import json
import os
from pathlib import Path

from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from agent.config import ROOT

SPANS_FILE = ROOT / "runs" / "spans.jsonl"
_client = None


def langfuse_online() -> bool:
    return bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))


class JsonlSpanExporter(SpanExporter):
    """Writes each finished span as one JSON line. This is what an exporter does: it receives finished spans
    from the SDK and sends them somewhere. Langfuse's exporter sends them over HTTP (OTLP); this one, to a file."""

    def __init__(self, path: Path = SPANS_FILE):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, spans) -> SpanExportResult:
        with self.path.open("a", encoding="utf-8") as f:
            for s in spans:
                f.write(json.dumps({
                    "name": s.name,
                    "trace_id": format(s.context.trace_id, "032x"),
                    "span_id": format(s.context.span_id, "016x"),
                    "parent_id": format(s.parent.span_id, "016x") if s.parent else None,
                    "start_ns": s.start_time,
                    "end_ns": s.end_time,
                    "status": s.status.status_code.name,
                    "attributes": dict(s.attributes or {}),
                }, ensure_ascii=False, default=str) + "\n")
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        pass


def get_langfuse():
    global _client
    if _client is None:
        from langfuse import Langfuse
        if langfuse_online():
            # Only this code's spans. With LLM_BACKEND=anthropic, the Anthropic SDK sends an OpenTelemetry span of its
            # own for every call; Langfuse would record each model call twice, and the cost twice.
            try:
                from langfuse import is_langfuse_span
                _client = Langfuse(should_export_span=is_langfuse_span)
            except ImportError:   # older SDK without the filter
                _client = Langfuse()   # reads LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL
        else:
            _client = Langfuse(public_key="pk-lf-offline", secret_key="sk-lf-offline",
                               base_url="http://localhost:1", span_exporter=JsonlSpanExporter())
    return _client


def trace_url(trace_id: str | None) -> str:
    if not trace_id:
        return ""
    if not langfuse_online():
        return f"python trace_view.py {trace_id[:8]}"
    try:
        return get_langfuse().get_trace_url(trace_id=trace_id) or ""
    except Exception:
        return ""
