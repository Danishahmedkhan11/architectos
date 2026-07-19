"""Hermetic test environment.

Env vars are pinned BEFORE any backend import (backend.config reads them at
import time), so the suite is independent of the developer's .env — no live
API keys, no Neo4j, and a throwaway data dir instead of the local graph.
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["ARCHITECTOS_DATA_DIR"] = tempfile.mkdtemp(prefix="architectos-test-")
os.environ["ARCHITECTOS_DEMO"] = "on"
for var in ("OPENAI_API_KEY", "OPENROUTER_API_KEY", "NEO4J_URI", "NEO4J_USERNAME",
            "NEO4J_PASSWORD", "NEO4J_DATABASE", "ARCHITECTOS_REASONING_MODEL",
            "ARCHITECTOS_CODEX_MODEL"):
    os.environ[var] = ""

import pytest  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.ingest import ingest_repo  # noqa: E402
from backend.retrieval import Retriever  # noqa: E402

# Small synthetic repo, generated fresh into a temp dir for the test session —
# no bundled sample directory lives in the repo. Just enough real structure
# (cross-file calls/imports, a model, two endpoints, a doc mention, a package
# dependency) for tests that need "some graph" to exist, without any fixed
# exact-shape assertions baked in.
_FIXTURE_FILES = {
    "services/auth/routes.py": '''"""Authentication endpoints: login, logout."""
from fastapi import APIRouter
from services.auth.sessions import create_session
from shared.models import User

router = APIRouter(prefix="/api/auth")


@router.post("/login")
def login(body: dict) -> dict:
    """Authenticate a user and start a session."""
    user = User()
    return create_session(user)


@router.post("/logout")
def logout() -> dict:
    """End the current session."""
    return {"ok": True}
''',
    "services/auth/sessions.py": '''"""Session management for authenticated users."""


def create_session(user) -> dict:
    """Create a signed session for the given user."""
    return {"token": "..."}
''',
    "shared/models.py": '''"""Shared data models."""


class User:
    """A registered user."""

    __tablename__ = "users"
''',
    "services/orders/service.py": '''"""Order placement service."""
from services.auth.sessions import create_session
from shared.models import User


def place_order(order_id: str) -> dict:
    """Place a new order for the current session's user."""
    user = User()
    create_session(user)
    return {"order_id": order_id}
''',
    "frontend/src/api.js": """// Frontend API client
export async function login(credentials) {
  return fetch('/api/auth/login', { method: 'POST', body: JSON.stringify(credentials) });
}

export async function placeOrder(orderId) {
  return fetch('/api/orders/' + orderId);
}
""",
    "docs/architecture.md": """# System architecture

Authentication is handled in `services/auth/routes.py`, which creates
sessions via `create_session`.
""",
    "requirements.txt": "fastapi==0.100.0\n",
}


@pytest.fixture(scope="session")
def fixture_repo(tmp_path_factory):
    root = tmp_path_factory.mktemp("architectos-fixture-repo")
    for rel, content in _FIXTURE_FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return root


@pytest.fixture(scope="session")
def graph(fixture_repo):
    return ingest_repo(fixture_repo, name="fixture-repo")


@pytest.fixture(scope="session")
def retriever(graph):
    return Retriever(graph)
