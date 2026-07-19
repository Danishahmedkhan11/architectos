import json

import pytest
from fastapi.testclient import TestClient

from backend.main import app


@pytest.fixture(scope="module")
def client(fixture_repo):
    with TestClient(app) as c:  # context manager triggers lifespan
        res = c.post("/api/ingest", json={"path": str(fixture_repo)})
        assert res.status_code == 200
        yield c


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.split("\n\n"):
        event, data = None, None
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if event and data is not None:
            events.append((event, data))
    return events


def test_health_and_config(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    cfg = client.get("/api/config").json()
    assert cfg["stats"]["repo"]  # some repo is active — name is whatever the ingest path resolved to
    assert cfg["engine"]["mode"] in ("live", "demo")


def test_graph_endpoint(client):
    data = client.get("/api/graph").json()
    assert len(data["nodes"]) > 10
    assert len(data["edges"]) > 10
    types = {n["type"] for n in data["nodes"]}
    assert {"file", "function", "endpoint", "model", "doc"} <= types


def test_node_and_search(client):
    res = client.get("/api/search", params={"q": "login"}).json()
    assert any("login" in r["label"].lower() or "login" in r["id"] for r in res["results"])
    node = client.get("/api/node", params={"id": "services/auth/routes.py"}).json()
    assert node["node"]["type"] == "file"
    assert len(node["neighbors"]) > 3


def test_ask_streams_sources_and_answer(client):
    with client.stream("POST", "/api/ask", json={"question": "How does authentication work?"}) as res:
        body = "".join(res.iter_text())
    events = parse_sse(body)
    names = [e for e, _ in events]
    assert "sources" in names and "delta" in names and "done" in names
    sources = next(d for e, d in events if e == "sources")
    assert len(sources["nodes"]) > 0
    done = next(d for e, d in events if e == "done")
    assert done["engine"]  # live model id or demo-cache


def test_ask_emits_citation_audit(client):
    with client.stream("POST", "/api/ask", json={"question": "How does authentication work?"}) as res:
        body = "".join(res.iter_text())
    events = parse_sse(body)
    audit = next(d for e, d in events if e == "citations")
    # No live key and no demo cache configured for this test env, so the honest
    # no-key fallback runs — it makes zero citation claims. The invariant that
    # matters either way: whatever citations are made must be real nodes.
    assert audit["invalid"] == []


def test_impact_streams_blast_then_plan(client):
    with client.stream("POST", "/api/impact", json={"request": "Add OAuth login with Google"}) as res:
        body = "".join(res.iter_text())
    events = parse_sse(body)
    blast = next(d for e, d in events if e == "blast")
    assert blast["summary"]["total"] > 0
    assert any(a["id"] == "shared/models.py::User" for a in blast["affected"])
    text = "".join(d["text"] for e, d in events if e == "delta")
    assert len(text) > 100


def test_architecture_stream(client):
    with client.stream("GET", "/api/architecture") as res:
        body = "".join(res.iter_text())
    events = parse_sse(body)
    overview = next(d for e, d in events if e == "overview")
    assert "flowchart" in overview["mermaid"]
    assert len(overview["module_view"]["clusters"]) >= 3


def test_ingest_rejects_bad_input(client):
    assert client.post("/api/ingest", json={}).status_code == 400
    assert client.post("/api/ingest", json={"path": "/definitely/not/a/dir"}).status_code == 400
    assert client.post("/api/ingest", json={"git_url": "notaurl"}).status_code == 400
