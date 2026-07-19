"""Hybrid retrieval over the knowledge graph: keyword scoring + optional embeddings."""
from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any, Optional

from .kg import KnowledgeGraph

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]+|\d+")
STOP = {
    "the", "a", "an", "in", "on", "of", "for", "to", "and", "or", "is", "are",
    "how", "what", "where", "does", "do", "we", "this", "that", "with", "it",
    "my", "our", "me", "you", "can", "should", "would", "add", "make", "get",
}


def tokenize(text: str) -> list[str]:
    tokens = []
    for raw in TOKEN_RE.findall(text.lower()):
        tokens.append(raw)
        # split snake_case; keep camelCase pieces too
        tokens.extend(p for p in raw.split("_") if len(p) > 2)
    return [t for t in tokens if t not in STOP and len(t) > 1]


class Retriever:
    def __init__(self, graph: KnowledgeGraph) -> None:
        self.graph = graph
        self.doc_tokens: dict[str, dict[str, int]] = {}
        self.df: dict[str, int] = defaultdict(int)
        for node_id, node in graph.nodes.items():
            counts: dict[str, int] = defaultdict(int)
            for t in tokenize(node.get("etext", "") or ""):
                counts[t] += 1
            self.doc_tokens[node_id] = dict(counts)
            for t in counts:
                self.df[t] += 1
        self.n_docs = max(1, len(graph.nodes))

    # ------------------------------------------------------------------ score
    def _keyword_score(self, node_id: str, q_tokens: list[str]) -> float:
        counts = self.doc_tokens.get(node_id, {})
        score = 0.0
        for t in q_tokens:
            tf = counts.get(t, 0)
            if tf:
                idf = math.log(1 + self.n_docs / (1 + self.df.get(t, 0)))
                score += (1 + math.log(tf)) * idf
        return score

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        num = sum(x * y for x, y in zip(a, b))
        da = math.sqrt(sum(x * x for x in a)) or 1.0
        db = math.sqrt(sum(y * y for y in b)) or 1.0
        return num / (da * db)

    def top_nodes(
        self,
        query: str,
        k: int = 12,
        types: Optional[set[str]] = None,
        query_vec: Optional[list[float]] = None,
    ) -> list[tuple[str, float]]:
        q_tokens = tokenize(query)
        scored: list[tuple[str, float]] = []
        for node_id, node in self.graph.nodes.items():
            if node["type"] == "repo":
                continue
            if types and node["type"] not in types:
                continue
            score = self._keyword_score(node_id, q_tokens)
            vec = node.get("vec")
            if query_vec and vec:
                score += 6.0 * max(0.0, self._cosine(query_vec, vec))
            if score <= 0:
                continue
            score *= 1.0 + 0.06 * math.log1p(self.graph.degree(node_id))
            scored.append((node_id, score))
        scored.sort(key=lambda t: -t[1])
        return scored[:k]

    # ---------------------------------------------------------------- context
    def context_pack(self, query: str, query_vec: Optional[list[float]] = None, k: int = 10) -> dict[str, Any]:
        """Grounding context for the LLM + the citation node ids."""
        hits = self.top_nodes(query, k=k, query_vec=query_vec)
        cited = [node_id for node_id, _ in hits]
        related: dict[str, str] = {}
        blocks: list[str] = []
        meta = self.graph.meta or {}
        blocks.append(
            f"REPOSITORY: {meta.get('repo_name', 'unknown')} "
            f"({len(self.graph.nodes)} graph nodes, {len(self.graph.edges)} edges)"
        )
        for node_id, _score in hits:
            n = self.graph.nodes[node_id]
            neigh = self.graph.neighbors(node_id)[:10]
            rel_lines = ", ".join(f"{x['rel']}:{x['id']}" for x in neigh[:8])
            snippet = (n.get("snippet") or "").strip()
            blocks.append(
                f"--- NODE {node_id} ---\n"
                f"type: {n['type']} | label: {n['label']} | cluster: {n.get('cluster')}\n"
                + (f"doc: {n.get('doc')}\n" if n.get("doc") else "")
                + (f"related: {rel_lines}\n" if rel_lines else "")
                + (f"source:\n{snippet[:1600]}\n" if snippet else "")
            )
            for x in neigh:
                related.setdefault(x["id"], x["label"])
        text = "\n".join(blocks)[:26000]
        return {"text": text, "cited": cited, "related": list(related)[:30]}
