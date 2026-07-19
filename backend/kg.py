"""The persistent knowledge graph: typed nodes + edges with fast adjacency.

Node types: repo, file, class, function, endpoint, model, doc, package
Edge types: CONTAINS, IMPORTS, DEFINES, CALLS, EXPOSES, CALLS_API,
            USES_MODEL, DOCUMENTS, DEPENDS_ON
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

NODE_TYPES = ["repo", "file", "class", "function", "endpoint", "model", "doc", "package"]

# Weight = how strongly a change in the edge target ripples back to the source
# (or forward, for the *_FWD kinds handled in affects_adjacency).
EDGE_TYPES = [
    "CONTAINS", "IMPORTS", "DEFINES", "CALLS", "EXPOSES",
    "CALLS_API", "USES_MODEL", "DOCUMENTS", "DEPENDS_ON",
]

TYPE_IMPORTANCE = {
    "repo": 5.0, "endpoint": 2.4, "model": 2.2, "class": 1.6,
    "file": 1.5, "function": 1.0, "doc": 1.2, "package": 0.6,
}


class KnowledgeGraph:
    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self._edge_keys: set[tuple[str, str, str]] = set()
        self.out: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.inc: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.meta: dict[str, Any] = {}

    # ---------------------------------------------------------------- build
    def add_node(self, node_id: str, type: str, label: str, **extra: Any) -> dict:
        node = self.nodes.get(node_id)
        if node is None:
            node = {"id": node_id, "type": type, "label": label}
            self.nodes[node_id] = node
        node.update({k: v for k, v in extra.items() if v is not None})
        return node

    def add_edge(self, src: str, dst: str, type: str, **extra: Any) -> None:
        if src == dst or src not in self.nodes or dst not in self.nodes:
            return
        key = (src, dst, type)
        if key in self._edge_keys:
            return
        self._edge_keys.add(key)
        edge = {"src": src, "dst": dst, "type": type, **extra}
        self.edges.append(edge)
        self.out[src].append(edge)
        self.inc[dst].append(edge)

    # ---------------------------------------------------------------- query
    def degree(self, node_id: str) -> int:
        return len(self.out.get(node_id, [])) + len(self.inc.get(node_id, []))

    def importance(self, node_id: str) -> float:
        node = self.nodes[node_id]
        return TYPE_IMPORTANCE.get(node["type"], 1.0) * (1.0 + math.log1p(self.degree(node_id)))

    def neighbors(self, node_id: str) -> list[dict[str, Any]]:
        seen: dict[str, dict[str, Any]] = {}
        for e in self.out.get(node_id, []):
            seen.setdefault(e["dst"], {"id": e["dst"], "rel": e["type"], "dir": "out"})
        for e in self.inc.get(node_id, []):
            seen.setdefault(e["src"], {"id": e["src"], "rel": e["type"], "dir": "in"})
        out = []
        for info in seen.values():
            n = self.nodes.get(info["id"])
            if n:
                out.append({**info, "label": n["label"], "type": n["type"]})
        return out

    def search(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        q = query.lower().strip()
        if not q:
            return []
        scored = []
        for n in self.nodes.values():
            hay = f"{n['label']} {n['id']} {n.get('path', '')}".lower()
            if q in hay:
                bonus = 3.0 if n["label"].lower().startswith(q) else (2.0 if q in n["label"].lower() else 1.0)
                scored.append((bonus + self.importance(n["id"]) * 0.1, n))
        scored.sort(key=lambda t: -t[0])
        return [n for _, n in scored[:limit]]

    def subgraph_edges(self, node_ids: Iterable[str]) -> list[dict[str, Any]]:
        ids = set(node_ids)
        return [e for e in self.edges if e["src"] in ids and e["dst"] in ids]

    def stats(self) -> dict[str, Any]:
        by_type: dict[str, int] = defaultdict(int)
        for n in self.nodes.values():
            by_type[n["type"]] += 1
        by_edge: dict[str, int] = defaultdict(int)
        for e in self.edges:
            by_edge[e["type"]] += 1
        return {
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "node_types": dict(by_type),
            "edge_types": dict(by_edge),
            "repo": self.meta.get("repo_name"),
            "source": self.meta.get("source"),
            "redactions": self.meta.get("redactions", 0),
            "clusters": sorted({n.get("cluster") for n in self.nodes.values() if n.get("cluster")}),
        }

    # ------------------------------------------------------------- analysis
    def affects_adjacency(self) -> dict[str, list[tuple[str, float, str]]]:
        """target -> [(dependent, weight, reason)]: who is affected when target changes."""
        adj: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
        for e in self.edges:
            s, d, t = e["src"], e["dst"], e["type"]
            if t == "IMPORTS":
                adj[d].append((s, 0.85, "imports it"))
            elif t == "CALLS":
                adj[d].append((s, 0.8, "calls it"))
            elif t == "DEFINES":
                adj[d].append((s, 0.9, "defines it"))
                adj[s].append((d, 0.75, "defined in it"))
            elif t == "EXPOSES":
                adj[s].append((d, 0.9, "exposed by it"))
                adj[d].append((s, 0.7, "exposes it"))
            elif t == "CALLS_API":
                adj[d].append((s, 0.85, "calls this API"))
            elif t == "USES_MODEL":
                adj[d].append((s, 0.8, "uses this data model"))
            elif t == "DOCUMENTS":
                adj[d].append((s, 0.55, "documents it"))
            elif t == "DEPENDS_ON":
                adj[d].append((s, 0.4, "depends on it"))
        return adj

    def uses_adjacency(self) -> dict[str, list[tuple[str, float, str]]]:
        """source -> [(dependency, weight, reason)]: what a change here will likely touch."""
        adj: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
        for e in self.edges:
            s, d, t = e["src"], e["dst"], e["type"]
            if t == "IMPORTS":
                adj[s].append((d, 0.7, "it imports this"))
            elif t == "CALLS":
                adj[s].append((d, 0.7, "it calls this"))
            elif t == "USES_MODEL":
                adj[s].append((d, 0.72, "it uses this data model"))
            elif t == "CALLS_API":
                adj[s].append((d, 0.7, "it calls this API"))
        return adj

    def module_view(self) -> dict[str, Any]:
        """Aggregate the graph to cluster level (service/module map)."""
        clusters: dict[str, dict[str, Any]] = {}
        for n in self.nodes.values():
            c = n.get("cluster")
            if not c:
                continue
            item = clusters.setdefault(c, {"id": c, "files": 0, "symbols": 0, "endpoints": 0})
            if n["type"] == "file":
                item["files"] += 1
            elif n["type"] == "endpoint":
                item["endpoints"] += 1
            elif n["type"] in ("class", "function", "model"):
                item["symbols"] += 1
        links: dict[tuple[str, str], int] = defaultdict(int)
        for e in self.edges:
            if e["type"] not in ("IMPORTS", "CALLS", "CALLS_API", "USES_MODEL"):
                continue
            cs = self.nodes[e["src"]].get("cluster")
            cd = self.nodes[e["dst"]].get("cluster")
            if cs and cd and cs != cd:
                links[(cs, cd)] += 1
        return {
            "clusters": list(clusters.values()),
            "links": [{"src": a, "dst": b, "weight": w} for (a, b), w in sorted(links.items(), key=lambda kv: -kv[1])],
        }

    # ---------------------------------------------------------- persistence
    def to_json(self) -> dict[str, Any]:
        return {"meta": self.meta, "nodes": list(self.nodes.values()), "edges": self.edges}

    @classmethod
    def from_json(cls, payload: dict[str, Any]) -> "KnowledgeGraph":
        g = cls()
        g.meta = payload.get("meta", {})
        for n in payload.get("nodes", []):
            g.nodes[n["id"]] = n
        for e in payload.get("edges", []):
            key = (e["src"], e["dst"], e["type"])
            if key in g._edge_keys or e["src"] not in g.nodes or e["dst"] not in g.nodes:
                continue
            g._edge_keys.add(key)
            g.edges.append(e)
            g.out[e["src"]].append(e)
            g.inc[e["dst"]].append(e)
        return g

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_json()), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Optional["KnowledgeGraph"]:
        if not path.exists():
            return None
        try:
            return cls.from_json(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None

    # ------------------------------------------------------------- neo4j
    def save_neo4j(self, uri: str, user: str, password: str, database: Optional[str] = None) -> None:
        """Persist the graph to Neo4j, replacing whatever is currently stored there."""
        from neo4j import GraphDatabase

        nodes_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for n in self.nodes.values():
            props = {k: v for k, v in n.items() if k != "vec" and v is not None}
            nodes_by_type[n["type"]].append(props)
        edges_by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for e in self.edges:
            edges_by_type[e["type"]].append({"src": e["src"], "dst": e["dst"]})

        driver = GraphDatabase.driver(uri, auth=(user, password))
        try:
            with driver.session(database=database) as session:
                session.run("MATCH (n:Entity) DETACH DELETE n")
                session.run("MERGE (m:GraphMeta {id: '__meta__'})")
                session.run(
                    "CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE"
                )
                for ntype, batch in nodes_by_type.items():
                    label = _neo4j_label(ntype)
                    session.run(
                        f"UNWIND $rows AS row CREATE (x:Entity:{label}) SET x = row",
                        rows=batch,
                    )
                for etype, batch in edges_by_type.items():
                    if etype not in EDGE_TYPES:
                        continue
                    session.run(
                        "UNWIND $rows AS row "
                        "MATCH (a:Entity {id: row.src}), (b:Entity {id: row.dst}) "
                        f"CREATE (a)-[:{etype}]->(b)",
                        rows=batch,
                    )
                session.run("MATCH (m:GraphMeta {id: '__meta__'}) SET m += $meta", meta=dict(self.meta))
        finally:
            driver.close()

    @classmethod
    def load_neo4j(cls, uri: str, user: str, password: str, database: Optional[str] = None) -> Optional["KnowledgeGraph"]:
        from neo4j import GraphDatabase

        driver = GraphDatabase.driver(uri, auth=(user, password))
        try:
            g = cls()
            with driver.session(database=database) as session:
                for record in session.run("MATCH (n:Entity) RETURN n"):
                    node = dict(record["n"])
                    if "id" in node:
                        g.nodes[node["id"]] = node
                for record in session.run(
                    "MATCH (a:Entity)-[r]->(b:Entity) RETURN a.id AS src, b.id AS dst, type(r) AS type"
                ):
                    src, dst, etype = record["src"], record["dst"], record["type"]
                    key = (src, dst, etype)
                    if key in g._edge_keys or src not in g.nodes or dst not in g.nodes:
                        continue
                    g._edge_keys.add(key)
                    edge = {"src": src, "dst": dst, "type": etype}
                    g.edges.append(edge)
                    g.out[src].append(edge)
                    g.inc[dst].append(edge)
                meta_record = session.run("MATCH (m:GraphMeta {id: '__meta__'}) RETURN m").single()
                if meta_record:
                    g.meta = {k: v for k, v in dict(meta_record["m"]).items() if k != "id"}
            return g if g.nodes else None
        finally:
            driver.close()


def _neo4j_label(node_type: str) -> str:
    return "".join(part.capitalize() for part in node_type.split("_"))
