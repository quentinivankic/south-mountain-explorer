#!/usr/bin/env python3
"""Contract tests for the documented offline TrailForge test target."""
from pathlib import Path


_MAKEFILE = (Path(__file__).resolve().parent.parent
             / "trailforge" / "Makefile").read_text(encoding="utf-8")


def test_make_test_runs_each_unit_suite_once_in_dependency_order():
    target = _MAKEFILE.split("\ntest:\n", 1)[1].split("\n\n", 1)[0]
    commands = [line.strip() for line in target.splitlines() if line.strip()]
    assert commands == [
        "$(PYTHON) -m unittest discover -s tools -p 'test_*.py'",
        "$(PYTHON) -m unittest discover -s assemble -p 'test_*.py'",
        "$(PYTHON) -m unittest discover -s serve -p 'test_*.py'",
    ]
    assert target.count("unittest discover") == 3
