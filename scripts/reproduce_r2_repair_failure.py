#!/usr/bin/env python3
"""Reproduce the exact R2 publication defects at the repair base commit.

This verifier is local and read-only. It uses only `git show` with a fixed,
credential-free environment and writes one canonical JSON receipt atomically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

BASE_SHA = "9af519a75fbd4a0ef15ab58d57cc08dc5230a722"
PUBLISH_PATH = ".github/workflows/trailforge-publish.yml"
STAGE_PATH = "scripts/stage-changed-r2.py"


def _git_show(repo: Path, path: str) -> str:
    environment = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/tmp",
        "LC_ALL": "C",
        "LANG": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
    }
    completed = subprocess.run(
        ["/usr/bin/git", "show", f"{BASE_SHA}:{path}"],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"cannot read exact base object {path}")
    return completed.stdout


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_atomic(path: Path, document: dict[str, Any]) -> None:
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise RuntimeError("receipt parent is missing or unsafe")
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        raise RuntimeError("receipt temporary path already exists")
    payload = json.dumps(
        document, ensure_ascii=False, allow_nan=False,
        sort_keys=True, separators=(",", ":"),
    ) + "\n"
    with temporary.open("x", encoding="utf-8", newline="") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    if not (repo / ".git").is_dir():
        raise RuntimeError("repo is not the exact isolated clone")

    publish = _git_show(repo, PUBLISH_PATH)
    stage = _git_show(repo, STAGE_PATH)
    defects = {
        "publish_dispatch_has_no_inputs": (
            "run: gh workflow run sync-geom-to-r2.yml" in publish
            and "-f source_sha=" not in publish
        ),
        "adapter_declares_success_schema_incapable": (
            "adapter_success_schema_capable=False" in stage
        ),
        "if_match_always_raises": (
            'if row["conditional_mode"] == "IF_MATCH":' in stage
            and "live overwrite disabled" in stage
        ),
    }
    if not all(defects.values()):
        raise RuntimeError(f"exact base no longer reproduces every defect: {defects}")

    current_publish = (repo / PUBLISH_PATH).read_text(encoding="utf-8")
    current_stage = (repo / STAGE_PATH).read_text(encoding="utf-8")
    repairs = {
        "publish_dispatch_passes_source_sha": '-f source_sha="$SOURCE_SHA"' in current_publish,
        "publish_dispatch_is_plan_only": "-f plan_only=true" in current_publish,
        "scoped_adapter_enabled": "class MolsConditionalWriter" in current_stage,
        "scoped_release_executor_used": "execute_scoped_release" in current_stage,
    }
    receipt: dict[str, Any] = {
        "schema": "trekdex-r2-repair-prefix-reproduction-v1",
        "base_sha": BASE_SHA,
        "base_file_sha256": {
            PUBLISH_PATH: _sha256(publish),
            STAGE_PATH: _sha256(stage),
        },
        "reproduced_defects": defects,
        "current_repair_markers": repairs,
        "provider_accessed": False,
        "credentials_accessed": False,
        "provider_effects": 0,
    }
    receipt["receipt_sha256"] = hashlib.sha256(
        (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode()
    ).hexdigest()
    _write_atomic(args.output, receipt)
    print(
        f"reproduced 3/3 base defects at {BASE_SHA}; "
        f"repair_markers={sum(repairs.values())}/{len(repairs)}; "
        f"receipt={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
