"""Deterministic blast-radius analysis: who is affected when these nodes change.

Runs entirely on the graph — no LLM needed — so impact visualization always
works, even offline. The LLM then reasons over this result to produce a plan.
"""
from __future__ import annotations

import heapq
from collections import defaultdict
from typing import Any, Optional

from .kg import KnowledgeGraph
from .retrieval import Retriever

SEED_TYPES = {"file", "class", "function", "endpoint", "model"}


def pick_seeds(retriever: Retriever, request: str, query_vec=None, k: int = 5) -> list[str]:
    hits = retriever.top_nodes(request, k=k * 3, types=SEED_TYPES, query_vec=query_vec)
    seeds = [node_id for node_id, _ in hits[:k]]
    return seeds


HOP_DECAY = 0.8  # extra decay per hop so far-away nodes rank sanely


def blast_radius(
    graph: KnowledgeGraph,
    seeds: list[str],
    max_hops: int = 4,
    min_score: float = 0.14,
    cap: int = 120,
) -> dict[str, Any]:
    """Dijkstra-flavored propagation with decay, in both directions:
    who depends on the seeds (affected) AND what the seeds depend on
    (will likely be touched by the change)."""
    adj = graph.affects_adjacency()
    for src, deps in graph.uses_adjacency().items():
        adj[src].extend(deps)
    best: dict[str, tuple[float, int, str]] = {}  # id -> (score, hop, why)
    heap: list[tuple[float, int, str, str]] = []
    for s in seeds:
        if s in graph.nodes:
            best[s] = (1.0, 0, "seed")
            heapq.heappush(heap, (-1.0, 0, s, "seed"))

    while heap:
        neg, hop, node_id, _why = heapq.heappop(heap)
        score = -neg
        if hop >= max_hops or score < best.get(node_id, (0, 0, ""))[0]:
            continue
        for dep, weight, reason in adj.get(node_id, []):
            new_score = score * weight * HOP_DECAY
            if new_score < min_score:
                continue
            prev = best.get(dep)
            if prev is None or new_score > prev[0]:
                label = graph.nodes[node_id]["label"]
                best[dep] = (new_score, hop + 1, f"{reason} → {label}")
                heapq.heappush(heap, (-new_score, hop + 1, dep, reason))

    ranked = sorted(best.items(), key=lambda kv: -kv[1][0])[:cap]
    affected = []
    services: dict[str, int] = defaultdict(int)
    for node_id, (score, hop, why) in ranked:
        node = graph.nodes[node_id]
        risk = "seed" if hop == 0 else ("high" if score >= 0.55 else "medium" if score >= 0.28 else "low")
        affected.append({
            "id": node_id, "label": node["label"], "type": node["type"],
            "cluster": node.get("cluster"), "score": round(score, 3),
            "hop": hop, "risk": risk, "why": why,
        })
        if node.get("cluster") and node["type"] != "repo":
            services[node["cluster"]] += 1

    ids = [a["id"] for a in affected]
    edges = [
        {"src": e["src"], "dst": e["dst"], "type": e["type"]}
        for e in graph.subgraph_edges(ids)
    ]
    return {
        "seeds": [s for s in seeds if s in graph.nodes],
        "affected": affected,
        "edges": edges,
        "services": dict(sorted(services.items(), key=lambda kv: -kv[1])),
        "summary": {
            "total": len(affected),
            "high": sum(1 for a in affected if a["risk"] == "high"),
            "medium": sum(1 for a in affected if a["risk"] == "medium"),
            "low": sum(1 for a in affected if a["risk"] == "low"),
            "clusters": len(services),
            "docs_to_update": sum(1 for a in affected if a["type"] == "doc"),
            "endpoints_touched": sum(1 for a in affected if a["type"] == "endpoint"),
        },
    }


def impact_context(graph: KnowledgeGraph, result: dict[str, Any], request: str) -> str:
    """Compact textual form of the blast radius for LLM grounding."""
    lines = [f"CHANGE REQUEST: {request}", "", "BLAST RADIUS (computed from the knowledge graph):"]
    for a in result["affected"][:60]:
        mark = "SEED" if a["risk"] == "seed" else a["risk"].upper()
        lines.append(f"- [{mark}] {a['type']} {a['id']} ({a['why']})")
    lines.append("")
    lines.append("AFFECTED AREAS: " + ", ".join(f"{k} ({v})" for k, v in result["services"].items()))
    seeds = result["seeds"]
    for s in seeds[:4]:
        node = graph.nodes.get(s, {})
        snippet = (node.get("snippet") or "").strip()
        if snippet:
            lines.append(f"\n--- SOURCE OF SEED {s} ---\n{snippet[:1500]}")
    return "\n".join(lines)[:24000]
