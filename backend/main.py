"""ArchitectOS API server — knowledge graph + GPT-5.6/Codex reasoning over your codebase."""
from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Generator, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, impact, llm, prompts
from .ingest import ingest_repo
from .kg import KnowledgeGraph
from .retrieval import Retriever

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("architectos")

STATE: dict[str, Any] = {"graph": None, "retriever": None}
JOBS: dict[str, dict[str, Any]] = {}


def set_graph(graph: KnowledgeGraph, persist: bool = True) -> None:
    STATE["graph"] = graph
    STATE["retriever"] = Retriever(graph)
    if persist:
        graph.save(config.GRAPH_PATH)
        if config.NEO4J_ENABLED:
            try:
                graph.save_neo4j(
                    config.NEO4J_URI, config.NEO4J_USERNAME, config.NEO4J_PASSWORD, config.NEO4J_DATABASE
                )
            except Exception:
                log.exception("Failed to persist graph to Neo4j")


def attach_embeddings(graph: KnowledgeGraph) -> int:
    """Embed the most important nodes (live mode only). Returns count embedded."""
    if not llm.live_enabled():
        return 0
    pending = [n for n in graph.nodes.values() if not n.get("vec") and n.get("etext")]
    pending.sort(key=lambda n: -graph.importance(n["id"]))
    pending = pending[: config.MAX_EMBED_NODES]
    if not pending:
        return 0
    vecs = llm.embed_texts([n["etext"] for n in pending])
    if not vecs:
        return 0
    for node, vec in zip(pending, vecs):
        node["vec"] = [round(x, 5) for x in vec]
    return len(pending)


def ingest_and_activate(path: Path, name: Optional[str] = None, source: Optional[str] = None) -> dict[str, Any]:
    graph = ingest_repo(path, name=name, source=source)
    embedded = attach_embeddings(graph)
    set_graph(graph)
    stats = graph.stats()
    stats["embedded_nodes"] = embedded
    log.info("Ingested %s: %s nodes / %s edges", graph.meta.get("repo_name"), stats["nodes"], stats["edges"])
    return stats


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    graph = None
    if config.NEO4J_ENABLED:
        try:
            graph = KnowledgeGraph.load_neo4j(
                config.NEO4J_URI, config.NEO4J_USERNAME, config.NEO4J_PASSWORD, config.NEO4J_DATABASE
            )
        except Exception:
            log.exception("Failed to load graph from Neo4j")
    if not graph or not graph.nodes:
        graph = KnowledgeGraph.load(config.GRAPH_PATH)
    if graph and graph.nodes:
        set_graph(graph, persist=False)
        log.info("Loaded persisted graph: %s (%d nodes)", graph.meta.get("repo_name"), len(graph.nodes))
    else:
        log.info("No persisted graph — waiting for a repo to be ingested.")
    yield


app = FastAPI(title="ArchitectOS", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


@app.middleware("http")
async def no_cache_static(request, call_next):
    # This is a build-step-free static app (no hashed filenames) — without an
    # explicit no-cache directive, some browsers hold onto a stale graph.js/app.js
    # after a deploy and silently run old code. Force revalidation on every load
    # instead (still cheap: a 304 round-trip via ETag/Last-Modified when unchanged).
    response = await call_next(request)
    if request.url.path in ("/", "/app.js", "/graph.js", "/styles.css", "/index.html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# ------------------------------------------------------------------- helpers

def graph_or_503() -> KnowledgeGraph:
    graph = STATE.get("graph")
    if not graph:
        raise HTTPException(503, "No repository ingested yet")
    return graph


def sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def stream_response(gen: Generator[str, None, None]) -> StreamingResponse:
    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


CITATION_RE = re.compile(r"\[\[([^\]\n]+)\]\]")


def relay_llm(gen, graph: Optional[KnowledgeGraph] = None) -> Generator[str, None, None]:
    """Relay LLM chunks as SSE; before `done`, audit [[citations]] against the graph."""
    parts: list[str] = []
    for chunk in gen:
        if "delta" in chunk:
            parts.append(chunk["delta"])
            yield sse("delta", {"text": chunk["delta"]})
        elif "error" in chunk:
            yield sse("error", {"message": chunk["error"]})
        if chunk.get("done"):
            if graph is not None:
                cited = set(CITATION_RE.findall("".join(parts)))
                invalid = sorted(c for c in cited if c not in graph.nodes)
                yield sse("citations", {"total": len(cited), "invalid": invalid})
            yield sse("done", {"engine": chunk.get("engine"), "cached": chunk.get("cached", False)})


def node_brief(graph: KnowledgeGraph, node_id: str) -> Optional[dict[str, Any]]:
    n = graph.nodes.get(node_id)
    if not n:
        return None
    return {"id": n["id"], "label": n["label"], "type": n["type"], "cluster": n.get("cluster")}


# -------------------------------------------------------------------- models

class IngestBody(BaseModel):
    path: Optional[str] = None
    git_url: Optional[str] = None


class AskBody(BaseModel):
    question: str


class ImpactBody(BaseModel):
    request: str


class GenerateBody(BaseModel):
    request: str


# ------------------------------------------------------------------ metadata

@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    graph = STATE.get("graph")
    return {
        "engine": llm.engine_info(),
        "stats": graph.stats() if graph else None,
    }


@app.get("/api/graph")
def get_graph() -> dict[str, Any]:
    graph = graph_or_503()
    ids = sorted(
        (nid for nid, n in graph.nodes.items() if n["type"] != "repo"),
        key=lambda nid: -graph.importance(nid),
    )[: config.GRAPH_VIEW_CAP]
    id_set = set(ids)
    nodes = []
    for nid in ids:
        n = graph.nodes[nid]
        nodes.append({
            "id": nid, "type": n["type"], "label": n["label"],
            "cluster": n.get("cluster"), "path": n.get("path"),
            "degree": graph.degree(nid),
        })
    edges = [
        {"src": e["src"], "dst": e["dst"], "type": e["type"]}
        for e in graph.edges
        if e["type"] != "CONTAINS" and e["src"] in id_set and e["dst"] in id_set
    ]
    return {"nodes": nodes, "edges": edges, "stats": graph.stats(), "truncated": len(id_set) < len(graph.nodes) - 1}


@app.get("/api/node")
def get_node(id: str) -> dict[str, Any]:
    graph = graph_or_503()
    n = graph.nodes.get(id)
    if not n:
        raise HTTPException(404, f"Unknown node: {id}")
    return {
        "node": {k: v for k, v in n.items() if k not in ("vec", "etext")},
        "neighbors": graph.neighbors(id)[:40],
    }


@app.get("/api/search")
def search(q: str) -> dict[str, Any]:
    graph = graph_or_503()
    return {"results": [
        {"id": n["id"], "label": n["label"], "type": n["type"], "cluster": n.get("cluster")}
        for n in graph.search(q, limit=15)
    ]}


# -------------------------------------------------------------------- ingest

@app.post("/api/ingest")
def ingest(body: IngestBody) -> dict[str, Any]:
    if body.path:
        path = Path(body.path).expanduser()
        if not path.is_dir():
            raise HTTPException(400, f"Not a directory: {path}")
        stats = ingest_and_activate(path, source=str(path))
        return {"status": "done", "stats": stats}
    if body.git_url:
        url = body.git_url.strip()
        if not re.match(r"^(https://|git@)[\w.@:/\-~]+$", url):
            raise HTTPException(400, "That doesn't look like a git URL")
        job_id = uuid.uuid4().hex[:10]
        JOBS[job_id] = {"state": "cloning", "message": f"Cloning {url}...", "stats": None}
        threading.Thread(target=_clone_job, args=(job_id, url), daemon=True).start()
        return {"status": "job", "job": job_id}
    raise HTTPException(400, "Provide `path` or `git_url`")


def _clone_job(job_id: str, url: str) -> None:
    try:
        name = re.sub(r"\.git$", "", url.rstrip("/").split("/")[-1]) or "repo"
        dest = config.WORKSPACE_DIR / f"{name}-{int(time.time())}"
        result = subprocess.run(
            ["git", "clone", "--depth", "1", url, str(dest)],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode != 0:
            JOBS[job_id] = {"state": "error", "message": result.stderr[-400:], "stats": None}
            return
        JOBS[job_id] = {"state": "ingesting", "message": "Parsing and building graph...", "stats": None}
        stats = ingest_and_activate(dest, name=name, source=url)
        JOBS[job_id] = {"state": "done", "message": "Ready", "stats": stats}
    except Exception as err:  # surface to the UI rather than dying silently
        JOBS[job_id] = {"state": "error", "message": str(err)[-400:], "stats": None}


@app.get("/api/ingest/status")
def ingest_status(job: str) -> dict[str, Any]:
    if job not in JOBS:
        raise HTTPException(404, "Unknown job")
    return JOBS[job]


# ------------------------------------------------------------------- ask/RAG

@app.post("/api/ask")
def ask(body: AskBody) -> StreamingResponse:
    graph = graph_or_503()
    retriever: Retriever = STATE["retriever"]
    question = body.question.strip()

    def gen() -> Generator[str, None, None]:
        qvec = llm.embed_query(question)
        pack = retriever.context_pack(question, query_vec=qvec)
        sources = [b for nid in pack["cited"] if (b := node_brief(graph, nid))]
        yield sse("sources", {"nodes": sources})
        user = f"GRAPH CONTEXT:\n{pack['text']}\n\nQUESTION: {question}"
        yield from relay_llm(llm.stream_answer(
            "ask", prompts.ASK_SYSTEM, user, cache_prompt=question, max_output_tokens=2500,
        ), graph=graph)

    return stream_response(gen())


# -------------------------------------------------------------------- impact

@app.post("/api/impact")
def impact_endpoint(body: ImpactBody) -> StreamingResponse:
    graph = graph_or_503()
    retriever: Retriever = STATE["retriever"]
    request = body.request.strip()

    def gen() -> Generator[str, None, None]:
        qvec = llm.embed_query(request)
        seeds = impact.pick_seeds(retriever, request, query_vec=qvec)
        result = impact.blast_radius(graph, seeds)
        yield sse("blast", result)
        context = impact.impact_context(graph, result, request)
        yield from relay_llm(llm.stream_answer(
            "impact", prompts.IMPACT_SYSTEM, context, cache_prompt=request,
            max_output_tokens=3500, effort="medium",
        ), graph=graph)

    return stream_response(gen())


# ------------------------------------------------------------------ generate

@app.post("/api/generate")
def generate(body: GenerateBody) -> StreamingResponse:
    graph = graph_or_503()
    retriever: Retriever = STATE["retriever"]
    request = body.request.strip()

    def gen() -> Generator[str, None, None]:
        qvec = llm.embed_query(request)
        seeds = impact.pick_seeds(retriever, request, query_vec=qvec)
        result = impact.blast_radius(graph, seeds)
        yield sse("blast", {"summary": result["summary"], "services": result["services"]})
        context = impact.impact_context(graph, result, request)
        user = f"{context}\n\nGenerate the implementation now."
        yield from relay_llm(llm.stream_answer(
            "generate", prompts.CODEGEN_SYSTEM, user, cache_prompt=request,
            max_output_tokens=8000, effort="high", kind="codex",
        ), graph=graph)

    return stream_response(gen())


# -------------------------------------------------------------- architecture

def build_mermaid(module_view: dict[str, Any]) -> str:
    def mid(name: str) -> str:
        return re.sub(r"[^A-Za-z0-9_]", "_", name)

    lines = ["flowchart LR"]
    for c in module_view["clusters"]:
        total = c["files"] + c["symbols"]
        lines.append(f'    {mid(c["id"])}["{c["id"]}\\n{c["files"]} files · {c["symbols"]} symbols"]')
    for l in module_view["links"]:
        lines.append(f'    {mid(l["src"])} -- "{l["weight"]}" --> {mid(l["dst"])}')
    return "\n".join(lines)


@app.get("/api/architecture")
def architecture() -> StreamingResponse:
    graph = graph_or_503()

    def gen() -> Generator[str, None, None]:
        view = graph.module_view()
        mermaid = build_mermaid(view)
        yield sse("overview", {"module_view": view, "mermaid": mermaid, "stats": graph.stats()})
        endpoints = [n["label"] for n in graph.nodes.values() if n["type"] == "endpoint"][:30]
        models = [
            f"{n['label']} (columns: {', '.join(n.get('columns') or [])})"
            for n in graph.nodes.values() if n["type"] == "model"
        ][:20]
        docs = [f"{n['id']}: {n.get('doc', '')}" for n in graph.nodes.values() if n["type"] == "doc"][:15]
        key_files = sorted(
            (n for n in graph.nodes.values() if n["type"] == "file"),
            key=lambda n: -graph.degree(n["id"]),
        )[:15]
        user = (
            f"REPOSITORY: {graph.meta.get('repo_name')}\n\n"
            f"MODULE MAP:\n{json.dumps(view, indent=1)}\n\n"
            f"API ENDPOINTS:\n" + "\n".join(f"- {e}" for e in endpoints) + "\n\n"
            f"DATA MODELS:\n" + "\n".join(f"- {m}" for m in models) + "\n\n"
            f"DOCS:\n" + "\n".join(f"- {d}" for d in docs) + "\n\n"
            f"MOST CONNECTED FILES:\n" + "\n".join(f"- {n['id']} (degree {graph.degree(n['id'])})" for n in key_files)
        )
        yield from relay_llm(llm.stream_answer(
            "architecture", prompts.ARCHITECTURE_SYSTEM, user[:24000],
            cache_prompt="architecture brief overview", max_output_tokens=3000,
        ), graph=graph)

    return stream_response(gen())


# -------------------------------------------------------------------- static

app.mount("/", StaticFiles(directory=str(config.ROOT / "static"), html=True), name="static")
