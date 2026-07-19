"""Central configuration for ArchitectOS."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA_DIR = Path(os.getenv("ARCHITECTOS_DATA_DIR", str(ROOT / "data")))
WORKSPACE_DIR = DATA_DIR / "workspace"
GRAPH_PATH = DATA_DIR / "graph.json"
DEMO_CACHE_PATH = ROOT / "backend" / "demo_cache.json"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# openrouter wins if both are set — it's the cheaper/free-testing path; switch
# to a real OPENAI_API_KEY for the graded GPT-5.6/Codex demo recording.
LLM_PROVIDER = "openrouter" if OPENROUTER_API_KEY else ("openai" if OPENAI_API_KEY else "none")

NEO4J_URI = os.getenv("NEO4J_URI", "").strip()
NEO4J_USERNAME = os.getenv("NEO4J_USERNAME", "").strip()
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "").strip()
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "").strip() or None
NEO4J_ENABLED = bool(NEO4J_URI and NEO4J_USERNAME and NEO4J_PASSWORD)

# Model fallback chains: first entry that the account can access wins.
_env_reason = os.getenv("ARCHITECTOS_REASONING_MODEL", "").strip()
_env_codex = os.getenv("ARCHITECTOS_CODEX_MODEL", "").strip()

REASONING_MODELS = [m for m in [_env_reason] if m] + [
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.1",
    "gpt-5",
]
CODEX_MODELS = [m for m in [_env_codex] if m] + [
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "gpt-5.1-codex",
    "gpt-5-codex",
    "gpt-5.6-sol",
    "gpt-5.5",
]
EMBED_MODEL = os.getenv("ARCHITECTOS_EMBED_MODEL", "text-embedding-3-small")

# auto: use live models when a key is present, cached demo answers otherwise.
DEMO_MODE = os.getenv("ARCHITECTOS_DEMO", "auto").lower()  # auto | on | off

MAX_FILES = int(os.getenv("ARCHITECTOS_MAX_FILES", "3000"))
MAX_FILE_BYTES = int(os.getenv("ARCHITECTOS_MAX_FILE_BYTES", "300000"))
MAX_EMBED_NODES = int(os.getenv("ARCHITECTOS_MAX_EMBED_NODES", "1500"))
GRAPH_VIEW_CAP = int(os.getenv("ARCHITECTOS_GRAPH_VIEW_CAP", "700"))

HOST = os.getenv("ARCHITECTOS_HOST", "127.0.0.1")
PORT = int(os.getenv("ARCHITECTOS_PORT", "8321"))


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
