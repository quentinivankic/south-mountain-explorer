#!/usr/bin/env python3
"""trailforge-publish.yml optional-region wiring tests (change #5). Reads the
workflow as text (and parses with pyyaml when available). Non-tautological: if
the --region flag were made unconditional or the default non-empty, these
assertions fail."""
from pathlib import Path

_WF = (Path(__file__).resolve().parent.parent
       / ".github" / "workflows" / "trailforge-publish.yml")


def test_region_code_input_exists():
    text = _WF.read_text()
    assert "region_code:" in text


def test_region_flag_is_conditional_on_region_code():
    text = _WF.read_text()
    # Flag is set only when region_code is non-empty.
    assert 'REGION_FLAG=""' in text
    assert '[ -n "${{ github.event.inputs.region_code }}" ]' in text
    assert 'REGION_FLAG="--region ${{ github.event.inputs.region_code }}"' in text
    assert "--per-area-merge $REGION_FLAG" in text


def test_default_assemble_flags_intact():
    text = _WF.read_text()
    assert "--per-area-merge" in text
    assert "--min-length-mi 0.1" in text


def test_region_code_default_is_empty():
    try:
        import yaml  # type: ignore
    except Exception:
        # pyyaml absent: fall back to asserting the empty-default text shape.
        text = _WF.read_text()
        assert 'region_code:' in text
        # the input block declares default: ""
        idx = text.index("region_code:")
        assert 'default: ""' in text[idx:idx + 300]
        return
    doc = yaml.safe_load(_WF.read_text())
    # PyYAML parses the bare `on:` key as the boolean True; accept either.
    on = doc.get("on", doc.get(True))
    inputs = on["workflow_dispatch"]["inputs"]
    assert "region_code" in inputs
    assert inputs["region_code"].get("default", None) == ""


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
