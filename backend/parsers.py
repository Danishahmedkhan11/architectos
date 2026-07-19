"""Source parsers that turn files into knowledge-graph entities.

Each parser returns a dict:
{
  "imports":   [{"module": str, "names": [str], "level": int}]        (python)
               [{"path": str, "names": [str]}]                        (js)
  "symbols":   [{"kind": "class"|"function"|"model", "name", "qualname",
                 "doc", "lineno", "end_lineno", "calls": [str],
                 "bases": [str], "decorators": [str], "routes": [(method, path)],
                 "columns": [str], "tablename": str|None}]
  "routes":    [(method, path, qualname)]        # flattened endpoint list
  "api_calls": [(method_or_None, path)]          # outbound HTTP calls to /api paths
  "mentions":  [str]                             # doc mentions of files/symbols
  "packages":  [str]                             # declared dependencies
  "doc":       str                               # module/file docstring or heading
}
"""
from __future__ import annotations

import ast
import json
import re
from typing import Any, Optional

HTTP_METHODS = {"get", "post", "put", "delete", "patch", "head", "options"}


def _empty() -> dict[str, Any]:
    return {
        "imports": [], "symbols": [], "routes": [], "api_calls": [],
        "mentions": [], "packages": [], "doc": "",
    }


# --------------------------------------------------------------------- python

def _call_names(node: ast.AST) -> list[str]:
    """Names of everything called inside a function body, e.g. `charge`, `stripe_client.charge`."""
    names: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Name):
                names.append(f.id)
            elif isinstance(f, ast.Attribute):
                names.append(f.attr)
                if isinstance(f.value, ast.Name):
                    names.append(f"{f.value.id}.{f.attr}")
    return names


def _decorator_route(dec: ast.expr, router_prefixes: dict[str, str]) -> Optional[tuple[str, str]]:
    """Detect @app.get("/path") / @router.post("/path") decorators."""
    if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)):
        return None
    method = dec.func.attr.lower()
    if method not in HTTP_METHODS or not dec.args:
        return None
    arg = dec.args[0]
    if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
        return None
    prefix = ""
    if isinstance(dec.func.value, ast.Name):
        prefix = router_prefixes.get(dec.func.value.id, "")
    return method.upper(), (prefix + arg.value) or "/"


def _router_prefixes(tree: ast.Module) -> dict[str, str]:
    """Map `router = APIRouter(prefix="/api/auth")` assignments to their prefix."""
    prefixes: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            fn = node.value.func
            fn_name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            if fn_name in ("APIRouter", "FastAPI", "Blueprint"):
                prefix = ""
                for kw in node.value.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                        prefix = kw.value.value
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        prefixes[target.id] = prefix
    return prefixes


def _is_model_class(cls: ast.ClassDef) -> bool:
    base_names = {b.id for b in cls.bases if isinstance(b, ast.Name)} | \
                 {b.attr for b in cls.bases if isinstance(b, ast.Attribute)}
    if base_names & {"Base", "DeclarativeBase", "Model", "BaseModelORM"}:
        return True
    for node in cls.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "__tablename__":
                    return True
    return False


def _model_details(cls: ast.ClassDef) -> tuple[Optional[str], list[str]]:
    tablename, columns = None, []
    for node in cls.body:
        if isinstance(node, ast.Assign) and node.targets and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name == "__tablename__" and isinstance(node.value, ast.Constant):
                tablename = str(node.value.value)
            elif isinstance(node.value, ast.Call):
                fn = node.value.func
                fn_name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
                if fn_name in ("Column", "mapped_column", "relationship"):
                    columns.append(name)
    return tablename, columns


def parse_python(text: str) -> dict[str, Any]:
    out = _empty()
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    out["doc"] = (ast.get_docstring(tree) or "")[:400]
    prefixes = _router_prefixes(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out["imports"].append({"module": alias.name, "names": [], "level": 0})
        elif isinstance(node, ast.ImportFrom):
            out["imports"].append({
                "module": node.module or "", "level": node.level,
                "names": [a.name for a in node.names],
            })

    def handle_function(fn: ast.AST, qualprefix: str = "") -> dict[str, Any]:
        assert isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        qualname = f"{qualprefix}{fn.name}"
        routes = []
        decorators = []
        for dec in fn.decorator_list:
            route = _decorator_route(dec, prefixes)
            if route:
                routes.append(route)
                out["routes"].append((route[0], route[1], qualname))
            try:
                decorators.append(ast.unparse(dec)[:80])
            except Exception:
                pass
        return {
            "kind": "function", "name": fn.name, "qualname": qualname,
            "doc": (ast.get_docstring(fn) or "")[:300],
            "lineno": fn.lineno, "end_lineno": getattr(fn, "end_lineno", fn.lineno),
            "calls": _call_names(fn), "bases": [], "decorators": decorators,
            "routes": routes, "columns": [], "tablename": None,
        }

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out["symbols"].append(handle_function(node))
        elif isinstance(node, ast.ClassDef):
            is_model = _is_model_class(node)
            tablename, columns = _model_details(node) if is_model else (None, [])
            bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
            out["symbols"].append({
                "kind": "model" if is_model else "class",
                "name": node.name, "qualname": node.name,
                "doc": (ast.get_docstring(node) or "")[:300],
                "lineno": node.lineno, "end_lineno": getattr(node, "end_lineno", node.lineno),
                "calls": [], "bases": bases, "decorators": [],
                "routes": [], "columns": columns, "tablename": tablename,
            })
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out["symbols"].append(handle_function(sub, qualprefix=f"{node.name}."))
    return out


# ------------------------------------------------------------------- js / ts

JS_IMPORT_RE = re.compile(
    r"""import\s+(?:([\w${},\s*]+)\s+from\s+)?['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)""",
)
JS_FUNC_RE = re.compile(
    r"""(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(|"""
    r"""(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[\w$]+)\s*=>""",
)
JS_API_RE = re.compile(r"""['"`](?:(GET|POST|PUT|DELETE|PATCH)['"`]\s*,\s*['"`])?(/api/[^'"`\s?]*)""")
JS_METHOD_HINT_RE = re.compile(r"""method\s*:\s*['"](GET|POST|PUT|DELETE|PATCH)['"]""", re.I)
JS_ROUTE_RE = re.compile(r"""(?:app|router)\.(get|post|put|delete|patch)\(\s*['"`]([^'"`]+)['"`]""")


def parse_js(text: str) -> dict[str, Any]:
    out = _empty()
    first_comment = re.match(r"\s*//\s*(.+)", text)
    out["doc"] = first_comment.group(1)[:200] if first_comment else ""

    for m in JS_IMPORT_RE.finditer(text):
        path = m.group(2) or m.group(3)
        names = [n.strip() for n in re.sub(r"[{}*]", "", m.group(1) or "").split(",") if n.strip()]
        if path:
            out["imports"].append({"path": path, "names": names})

    lines = text.splitlines()
    for m in JS_FUNC_RE.finditer(text):
        name = m.group(1) or m.group(2)
        if not name:
            continue
        lineno = text[: m.start()].count("\n") + 1
        out["symbols"].append({
            "kind": "function", "name": name, "qualname": name, "doc": "",
            "lineno": lineno, "end_lineno": min(lineno + 24, len(lines)),
            "calls": [], "bases": [], "decorators": [], "routes": [],
            "columns": [], "tablename": None,
        })

    for m in JS_ROUTE_RE.finditer(text):
        out["routes"].append((m.group(1).upper(), m.group(2), ""))

    for m in JS_API_RE.finditer(text):
        explicit, path = m.group(1), m.group(2)
        method = explicit
        if not method:
            window = text[max(0, m.start() - 160): m.start() + 160]
            hint = JS_METHOD_HINT_RE.search(window)
            method = hint.group(1).upper() if hint else None
        out["api_calls"].append((method, path.rstrip("/") or "/"))
    return out


# ------------------------------------------------------------------ markdown

MD_MENTION_RE = re.compile(r"`([\w./\-]+)`|(?<![\w/])([\w\-]+(?:/[\w.\-]+)+\.(?:py|js|jsx|ts|tsx|md))")


def parse_markdown(text: str) -> dict[str, Any]:
    out = _empty()
    heading = re.search(r"^#\s+(.+)$", text, re.M)
    out["doc"] = heading.group(1).strip()[:200] if heading else text.strip().splitlines()[0][:200] if text.strip() else ""
    for m in MD_MENTION_RE.finditer(text):
        token = (m.group(1) or m.group(2) or "").strip()
        if token and len(token) > 2:
            out["mentions"].append(token)
    return out


# ---------------------------------------------------------------- dep files

def parse_package_json(text: str) -> dict[str, Any]:
    out = _empty()
    try:
        data = json.loads(text)
    except Exception:
        return out
    for section in ("dependencies", "devDependencies"):
        out["packages"].extend((data.get(section) or {}).keys())
    out["doc"] = data.get("description", "")[:200]
    return out


def parse_requirements(text: str) -> dict[str, Any]:
    out = _empty()
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "-")):
            out["packages"].append(re.split(r"[<>=\[~!;\s]", line)[0])
    return out
