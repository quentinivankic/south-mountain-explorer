#!/usr/bin/env python3
"""Offline security contract tests for the local QA viewer listeners."""
import ast
import importlib.util
import os
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parent.parent
_SERVER_PATHS = (
    _ROOT / "trailforge" / "viewer" / "serve.py",
    _ROOT / "trailforge" / "viewer" / "serve-review.py",
)
_DOCUMENTED_PATHS = (
    _ROOT / ".github" / "workflows" / "trailforge-publish.yml",
    _ROOT / "trailforge" / "README.md",
    _ROOT / "trailforge" / "HOMELAB.md",
    _ROOT / "trailforge" / "Makefile",
    _ROOT / "trailforge" / "tools" / "run_golden.py",
    _ROOT / "trailforge" / "tools" / "validate_publish_pilot.py",
)
_LOOPBACK = "127.0.0.1"
_UNSUPPORTED_HOSTS = ("", "0.0.0.0", "::", "[::]", "localhost", "viewer.example")


def _load_server(path):
    name = "qa_viewer_" + path.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bind_calls(tree):
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "ThreadingHTTPServer"
    ]


def test_viewer_bind_is_statically_fixed_to_literal_ipv4_loopback():
    for path in _SERVER_PATHS:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        assignments = {
            target.id: node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        bind_host = assignments.get("BIND_HOST")
        assert isinstance(bind_host, ast.Constant)
        assert bind_host.value == _LOOPBACK

        calls = _bind_calls(tree)
        assert len(calls) == 1
        address = calls[0].args[0]
        assert isinstance(address, ast.Tuple) and len(address.elts) == 2
        assert isinstance(address.elts[0], ast.Name)
        assert address.elts[0].id == "BIND_HOST"

        environment_keys = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "environ"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        }
        assert environment_keys == {"PORT"}


def test_default_actual_bind_ignores_host_environment_and_is_loopback():
    for path in _SERVER_PATHS:
        for unsupported in _UNSUPPORTED_HOSTS:
            calls = []

            class FakeServer:
                def __init__(self, address, handler):
                    calls.append((address, handler))

            with mock.patch.dict(os.environ, {"HOST": unsupported}):
                module = _load_server(path)
                with mock.patch.object(
                        module.http.server, "ThreadingHTTPServer", FakeServer):
                    module.create_server()
            assert len(calls) == 1
            assert calls[0][0][0] == _LOOPBACK


def test_every_unsupported_non_loopback_bind_path_is_rejected():
    for path in _SERVER_PATHS:
        module = _load_server(path)
        for unsupported in _UNSUPPORTED_HOSTS:
            with mock.patch.object(module.http.server, "ThreadingHTTPServer") as server:
                try:
                    module.create_server(unsupported)
                except ValueError as exc:
                    assert _LOOPBACK in str(exc)
                else:
                    raise AssertionError(
                        f"{path.name} accepted non-loopback host {unsupported!r}")
                server.assert_not_called()


def test_artifact_and_user_instructions_use_literal_loopback_urls():
    for path in _DOCUMENTED_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "http://localhost:8000" not in text
        assert "http://127.0.0.1:8000" in text

    workflow = _DOCUMENTED_PATHS[0].read_text(encoding="utf-8")
    assert "loopback-only" in workflow
    assert "bound only to 127.0.0.1" in workflow
