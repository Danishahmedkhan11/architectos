from backend import parsers


def test_python_symbols_and_routes():
    src = '''
from fastapi import APIRouter
from services.auth.jwt_utils import create_access_token

router = APIRouter(prefix="/api/demo")


@router.post("/things")
def make_thing(name: str):
    """Create a thing."""
    token = create_access_token(1, "a@b.c")
    return {"token": token}


class ThingService:
    def helper(self):
        return make_thing("x")
'''
    info = parsers.parse_python(src)
    names = {s["qualname"] for s in info["symbols"]}
    assert {"make_thing", "ThingService", "ThingService.helper"} <= names
    assert ("POST", "/api/demo/things", "make_thing") in info["routes"]
    make = next(s for s in info["symbols"] if s["qualname"] == "make_thing")
    assert "create_access_token" in make["calls"]
    assert make["doc"] == "Create a thing."
    assert {"module": "services.auth.jwt_utils", "names": ["create_access_token"], "level": 0} in info["imports"]


def test_python_model_detection():
    src = '''
from sqlalchemy import Column, Integer
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Widget(Base):
    __tablename__ = "widgets"
    id = Column(Integer, primary_key=True)
    size = Column(Integer)
'''
    info = parsers.parse_python(src)
    widget = next(s for s in info["symbols"] if s["name"] == "Widget")
    assert widget["kind"] == "model"
    assert widget["tablename"] == "widgets"
    assert "size" in widget["columns"]


def test_js_imports_functions_api_calls():
    src = '''
import { request } from "./api.js";
const helper = require("./helper");

export async function saveOrder(cart) {
  return request("POST", "/api/orders", cart);
}

const load = async () => fetch("/api/users/me", { method: "GET" });
'''
    info = parsers.parse_js(src)
    assert {"path": "./api.js", "names": ["request"]} in info["imports"]
    names = {s["name"] for s in info["symbols"]}
    assert {"saveOrder", "load"} <= names
    assert ("POST", "/api/orders") in info["api_calls"]
    assert ("GET", "/api/users/me") in info["api_calls"]


def test_markdown_mentions():
    info = parsers.parse_markdown("# Title\n\nSee `services/auth/jwt_utils.py` and frontend/src/api.js for details.")
    assert "services/auth/jwt_utils.py" in info["mentions"]
    assert "frontend/src/api.js" in info["mentions"]
    assert info["doc"] == "Title"
