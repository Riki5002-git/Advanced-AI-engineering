"""Settings and a small clock. Reads a .env file next to the project (KEY=VALUE lines), without overriding
variables that are already set in the terminal."""
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


load_env()

LLM_BACKEND = os.environ.get("LLM_BACKEND", "sim")           # sim | anthropic
MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-5-5")
SIM_SPEED = float(os.environ.get("SIM_SPEED", "1"))          # 1 = real waits; 0.1 = ten times faster
RELEASE = os.environ.get("AGENT_RELEASE", "v1.3")
DEFAULT_DB = ROOT / "runs" / "agent.db"


class Clock:
    """Live mode really waits. Virtual mode only moves a counter forward, so seed_demo.py can
    produce two weeks of history in seconds, with the same durations a live run would have."""

    def __init__(self, virtual_start: float | None = None):
        self.virtual = virtual_start is not None
        self.t = virtual_start or 0.0

    def now(self) -> float:
        return self.t if self.virtual else time.time()

    def sleep(self, seconds: float) -> None:
        if self.virtual:
            self.t += seconds
        else:
            time.sleep(seconds * SIM_SPEED)


LIVE = Clock()
