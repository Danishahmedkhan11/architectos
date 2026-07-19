"""Repository ingestion: walk sources, parse, and assemble the knowledge graph."""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from . import parsers
from .config import MAX_FILE_BYTES, MAX_FILES
from .kg import KnowledgeGraph

IGNORE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
    ".next", "coverage", ".idea", ".vscode", "target", ".mypy_cache", ".pytest_cache",
    "data", ".tox", "site-packages", "egg-info",
}
CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx"}
DOC_EXTS = {".md", ".rst"}
JS_SUFFIXES = [".js", ".jsx", ".ts", ".tsx", "/index.js", "/index.ts"]

# --- secret-safety gate (blueprint §8): never store or send credential material
KEY_FILE_NAMES = {"id_rsa", "id_ed25519", "id_ecdsa", ".env", ".env.local", ".npmrc", ".netrc"}
KEY_FILE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore"}

SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
     "[REDACTED-PRIVATE-KEY]"),
    (re.compile(r"\b(?:sk|rk)-[A-Za-z0-9_\-]{20,}\b"), "[REDACTED]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED]"),
    (re.compile(r"\bghp_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b"), "[REDACTED]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}\b"), "[REDACTED]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"), "[REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\b"), "[REDACTED-JWT]"),
]
SECRET_ASSIGNMENT = re.compile(
    r"""(?i)\b(api[_-]?key|secret[_-]?key|client[_-]?secret|password|passwd|auth[_-]?token|access[_-]?token|private[_-]?key)"""
    r"""(\s*[:=]\s*)(["'])(?!\$\{|\{\{|<|%s|os\.|process\.)[^"'\n]{12,}\3"""
)


def redact_secrets(text: str) -> tuple[str, int]:
    """Replace credential-looking strings before anything is stored or embedded."""
    count = 0
    for pattern, replacement in SECRET_PATTERNS:
        text, n = pattern.subn(replacement, text)
        count += n
    text, n = SECRET_ASSIGNMENT.subn(lambda m: f'{m.group(1)}{m.group(2)}"[REDACTED]"', text)
    count += n
    return text, count


def _cluster(relpath: str) -> str:
    parts = relpath.split("/")
    if len(parts) == 1:
        return "root"
    if parts[0] in ("services", "apps", "packages") and len(parts) > 2:
        return f"{parts[0]}/{parts[1]}"
    return parts[0]


def _norm_endpoint(method: Optional[str], path: str) -> str:
    path = re.sub(r"<[^>]+>", "{param}", path)
    path = re.sub(r"\{[^}]+\}", "{param}", path)
    return f"{(method or 'ANY').upper()} {path.rstrip('/') or '/'}"


def collect_files(root: Path) -> tuple[dict[str, str], int]:
    """(relpath -> redacted text, total redactions) for parseable files under root."""
    files: dict[str, str] = {}
    redactions = 0
    for p in sorted(root.rglob("*")):
        if len(files) >= MAX_FILES:
            break
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if any(part in IGNORE_DIRS or part.startswith(".") for part in rel.split("/")[:-1]):
            continue
        name = p.name
        if name in KEY_FILE_NAMES or p.suffix in KEY_FILE_SUFFIXES:
            continue  # credential material never enters the graph
        interesting = (
            p.suffix in CODE_EXTS or p.suffix in DOC_EXTS
            or name in ("package.json", "requirements.txt", "pyproject.toml")
        )
        if not interesting or name.endswith(".min.js"):
            continue
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                continue
            text, n = redact_secrets(p.read_text(encoding="utf-8", errors="replace"))
            redactions += n
            files[rel] = text
        except OSError:
            continue
    return files, redactions


def ingest_repo(root: Path, name: Optional[str] = None, source: Optional[str] = None) -> KnowledgeGraph:
    root = root.resolve()
    repo_name = name or root.name
    g = KnowledgeGraph()
    g.meta = {
        "repo_name": repo_name,
        "source": source or str(root),
        "ingested_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
    }
    repo_id = f"repo::{repo_name}"
    g.add_node(repo_id, "repo", repo_name, cluster="root")

    files, redactions = collect_files(root)
    g.meta["redactions"] = redactions
    parsed: dict[str, dict[str, Any]] = {}

    # ---- pass 1: file + doc + package nodes -------------------------------
    py_module_map: dict[str, str] = {}
    for rel, text in files.items():
        suffix = Path(rel).suffix
        if suffix == ".py":
            info = parsers.parse_python(text)
            mod = rel[:-3].replace("/", ".")
            py_module_map[mod] = rel
            if rel.endswith("/__init__.py"):
                py_module_map[rel[: -len("/__init__.py")].replace("/", ".")] = rel
        elif suffix in (".js", ".jsx", ".ts", ".tsx"):
            info = parsers.parse_js(text)
        elif suffix in DOC_EXTS:
            info = parsers.parse_markdown(text)
        elif Path(rel).name == "package.json":
            info = parsers.parse_package_json(text)
        elif Path(rel).name == "requirements.txt":
            info = parsers.parse_requirements(text)
        else:
            info = parsers._empty()
        parsed[rel] = info

        lines = text.splitlines()
        node_type = "doc" if suffix in DOC_EXTS else "file"
        label = Path(rel).name if node_type == "file" else (info["doc"] or Path(rel).name)
        g.add_node(
            rel, node_type, label, path=rel, cluster=_cluster(rel),
            lang=suffix.lstrip("."), loc=len(lines), doc=info["doc"],
            snippet="\n".join(lines[:35]),
        )
        g.add_edge(repo_id, rel, "CONTAINS")

        for pkg in info["packages"]:
            pkg_id = f"pkg::{pkg}"
            g.add_node(pkg_id, "package", pkg, cluster="deps")
            g.add_edge(rel, pkg_id, "DEPENDS_ON")

    declared_packages = {n["label"] for n in g.nodes.values() if n["type"] == "package"}

    # ---- pass 2: symbols, endpoints ---------------------------------------
    symbol_index: dict[str, list[str]] = defaultdict(list)   # bare name -> node ids
    file_symbols: dict[str, dict[str, str]] = defaultdict(dict)  # rel -> {name: node_id}
    endpoint_ids: dict[str, str] = {}

    for rel, info in parsed.items():
        lines = files[rel].splitlines()
        for sym in info["symbols"]:
            sym_id = f"{rel}::{sym['qualname']}"
            start, end = sym["lineno"], min(sym["end_lineno"], sym["lineno"] + 50)
            snippet = "\n".join(lines[start - 1: end])
            node_type = {"class": "class", "model": "model"}.get(sym["kind"], "function")
            label = sym["qualname"] if node_type != "model" else f"{sym['name']} ({sym['tablename'] or 'table'})"
            g.add_node(
                sym_id, node_type, label, path=rel, cluster=_cluster(rel),
                lineno=start, doc=sym["doc"], snippet=snippet,
                columns=sym.get("columns") or None, tablename=sym.get("tablename"),
            )
            g.add_edge(rel, sym_id, "DEFINES")
            symbol_index[sym["name"]].append(sym_id)
            file_symbols[rel][sym["name"]] = sym_id
            file_symbols[rel][sym["qualname"]] = sym_id

        for method, path, qual in info["routes"]:
            ep = _norm_endpoint(method, path)
            ep_id = f"api::{ep}"
            g.add_node(ep_id, "endpoint", ep, cluster="api", path=path, method=method)
            endpoint_ids[ep] = ep_id
            g.add_edge(rel, ep_id, "EXPOSES")
            if qual and qual in file_symbols[rel]:
                g.add_edge(file_symbols[rel][qual], ep_id, "EXPOSES")

    # ---- pass 3: imports + model usage ------------------------------------
    import_targets: dict[str, dict[str, str]] = defaultdict(dict)  # rel -> {imported name/alias: target rel}

    def link_py_name(rel: str, target: str, name: str) -> None:
        """Map one imported name to its source file; add USES_MODEL for model classes."""
        import_targets[rel][name] = target
        sym_id = file_symbols.get(target, {}).get(name)
        if sym_id and g.nodes[sym_id]["type"] == "model":
            g.add_edge(rel, sym_id, "USES_MODEL")

    for rel, info in parsed.items():
        suffix = Path(rel).suffix
        if suffix == ".py":
            for imp in info["imports"]:
                module, names, level = imp["module"], imp["names"], imp["level"]
                if level:  # relative import -> absolute dotted path
                    base = Path(rel).parent
                    for _ in range(level - 1):
                        base = base.parent
                    module = (base.as_posix().replace("/", ".") + ("." + module if module else "")).strip(".")
                    if module == ".":
                        module = ""
                resolved_any = False
                if module in py_module_map:
                    target = py_module_map[module]
                    g.add_edge(rel, target, "IMPORTS")
                    resolved_any = True
                    for n in names:
                        dotted = f"{module}.{n}"
                        if dotted in py_module_map:  # `from pkg import submodule`
                            sub = py_module_map[dotted]
                            g.add_edge(rel, sub, "IMPORTS")
                            import_targets[rel][n] = sub
                        else:
                            link_py_name(rel, target, n)
                else:
                    for n in names:
                        dotted = f"{module}.{n}" if module else n
                        if dotted in py_module_map:
                            sub = py_module_map[dotted]
                            g.add_edge(rel, sub, "IMPORTS")
                            import_targets[rel][n] = sub
                            resolved_any = True
                if not resolved_any and module:
                    top = module.split(".")[0]
                    if top in declared_packages:
                        g.add_edge(rel, f"pkg::{top}", "DEPENDS_ON")
        elif suffix in (".js", ".jsx", ".ts", ".tsx"):
            for imp in info["imports"]:
                path = imp["path"]
                if path.startswith("."):
                    base = (Path(rel).parent / path).as_posix()
                    base = re.sub(r"/\./", "/", base)
                    while "../" in base:
                        base = re.sub(r"[^/]+/\.\./", "", base, count=1)
                    for suf in [""] + JS_SUFFIXES:
                        cand = base + suf
                        if cand in files:
                            g.add_edge(rel, cand, "IMPORTS")
                            for n in imp["names"]:
                                import_targets[rel][n] = cand
                            break
                else:
                    pkg = path.split("/")[0]
                    pkg_id = f"pkg::{pkg}"
                    g.add_node(pkg_id, "package", pkg, cluster="deps")
                    g.add_edge(rel, pkg_id, "DEPENDS_ON")

    # ---- pass 4: calls + api calls + doc mentions -------------------------
    for rel, info in parsed.items():
        for sym in info["symbols"]:
            src_id = file_symbols[rel].get(sym["qualname"])
            if not src_id:
                continue
            for called in set(sym["calls"]):
                target_id = None
                bare = called.split(".")[-1]
                if "." in called:
                    alias = called.split(".")[0]
                    target_file = import_targets[rel].get(alias)
                    if target_file:
                        target_id = file_symbols.get(target_file, {}).get(bare)
                if not target_id and bare in file_symbols[rel] and file_symbols[rel][bare] != src_id:
                    target_id = file_symbols[rel][bare]
                if not target_id and bare in import_targets[rel]:
                    target_id = file_symbols.get(import_targets[rel][bare], {}).get(bare)
                if not target_id:
                    candidates = symbol_index.get(bare, [])
                    if len(candidates) == 1 and candidates[0] != src_id:
                        target_id = candidates[0]
                if target_id:
                    g.add_edge(src_id, target_id, "CALLS")
                    target_node = g.nodes[target_id]
                    if target_node["type"] == "model":
                        g.add_edge(src_id, target_id, "USES_MODEL")

        for method, path in info["api_calls"]:
            target = _match_endpoint(endpoint_ids, method, path)
            if target:
                g.add_edge(rel, target, "CALLS_API")

        if g.nodes.get(rel, {}).get("type") == "doc":
            for mention in set(info["mentions"]):
                for target in _match_mention(g, files, symbol_index, mention):
                    g.add_edge(rel, target, "DOCUMENTS")

    # ---- retrieval text ----------------------------------------------------
    for n in g.nodes.values():
        n["etext"] = _embed_text(n)

    g.meta["files"] = len(files)
    return g


def _match_endpoint(endpoint_ids: dict[str, str], method: Optional[str], path: str) -> Optional[str]:
    norm_path = re.sub(r"\{[^}]+\}", "{param}", path.rstrip("/") or "/")
    if method:
        hit = endpoint_ids.get(f"{method.upper()} {norm_path}")
        if hit:
            return hit
    for ep, ep_id in endpoint_ids.items():
        ep_method, ep_path = ep.split(" ", 1)
        if ep_path == norm_path and (method is None or ep_method == method.upper()):
            return ep_id
        # `/api/users/42` should match `/api/users/{param}`
        a, b = norm_path.split("/"), ep_path.split("/")
        if len(a) == len(b) and all(x == y or y == "{param}" for x, y in zip(a, b)):
            if method is None or ep_method == method.upper():
                return ep_id
    return None


def _match_mention(g: KnowledgeGraph, files: dict[str, str], symbol_index: dict[str, list[str]], mention: str) -> list[str]:
    if mention in files:
        return [mention]
    tail_hits = [rel for rel in files if rel.endswith("/" + mention) or rel == mention]
    if tail_hits:
        return tail_hits[:2]
    if mention in symbol_index:
        return symbol_index[mention][:2]
    return []


def _embed_text(n: dict[str, Any]) -> str:
    parts = [f"{n['type']} {n['label']}"]
    if n.get("path"):
        parts.append(f"in {n['path']}")
    if n.get("doc"):
        parts.append(str(n["doc"]))
    if n.get("columns"):
        parts.append("columns: " + ", ".join(n["columns"]))
    if n.get("snippet"):
        parts.append(str(n["snippet"])[:700])
    return "\n".join(parts)[:1200]
