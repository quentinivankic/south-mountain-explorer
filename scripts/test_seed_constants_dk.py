#!/usr/bin/env python3
"""Denmark re-enable + region-scoped quality tests for _seed_constants.py
(no network). Covers plan changes #1 and #2, with US/Canada control
assertions proving the DK keyword is region-scoped and existing behavior is
byte-for-byte unchanged."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "sc", Path(__file__).resolve().parent / "_seed_constants.py")
sc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sc)


# ---- change #1: DK re-enabled ----

def test_country_codes_is_exactly_dk():
    assert sc.COUNTRY_CODES == {"DK"}


def test_state_names_has_denmark():
    assert sc.STATE_NAMES["DK"] == "Denmark"


def test_display_state_dk_is_denmark():
    assert sc.display_state("DK") == "Denmark"


def test_slugify_danish_name_folds_to_ascii_and_ends_dk():
    # ø→o, æ→ae, å→a already in ASCII_TRANSLIT; code lowercased to -dk.
    assert (sc.slugify("Nationalpark Kongernes Nordsjælland", "DK")
            == "nationalpark-kongernes-nordsjaelland-dk")


def test_code_from_slug_recovers_dk():
    assert sc.code_from_slug("nationalpark-mols-bjerge-dk") == "DK"


# ---- change #2: Danish keyword, region-scoped ----

def test_dk_national_park_passes_under_dk_via_keyword():
    # No protect_class — only the Denmark-scoped 'nationalpark' keyword.
    assert sc.is_quality({"name": "Nationalpark Mols Bjerge"}, code="DK") is True


def test_dk_keyword_does_not_leak_to_us_code():
    assert sc.is_quality({"name": "Nationalpark Mols Bjerge"}, code="AZ") is False


def test_dk_keyword_does_not_leak_to_default_code():
    assert sc.is_quality({"name": "Nationalpark Mols Bjerge"}) is False


def test_dk_keyword_does_not_leak_to_canada_code():
    assert sc.is_quality({"name": "Nationalpark Mols Bjerge"}, code="CA-QC") is False


def test_speculative_tiny_reserve_gains_nothing_under_dk():
    # 'Lille Naturreservat' — no protect_class, not a national park. The old
    # broad Danish alternation (naturreservat) was deliberately NOT restored,
    # so this must stay out even under DK.
    assert sc.is_quality({"name": "Lille Naturreservat"}, code="DK") is False


def test_protect_class_authority_still_works_for_dk():
    assert sc.is_quality(
        {"name": "Some Area", "protect_class": "2"}, code="DK") is True


# ---- controls: US / Canada behavior unchanged ----

def test_us_name_keyword_still_passes_default():
    assert sc.is_quality({"name": "Pinnacle Peak Park"}) is True


def test_us_slug_unchanged():
    assert sc.slugify("Pinnacle Peak Park", "AZ") == "pinnacle-peak-park-az"


# ---- safety floor still enforced regardless of code ----

def test_private_access_blocked_even_with_dk_keyword():
    assert sc.is_quality(
        {"name": "Nationalpark Mols Bjerge", "access": "private"},
        code="DK") is False


def test_private_ownership_blocked_even_with_dk_keyword():
    assert sc.is_quality(
        {"name": "Nationalpark Mols Bjerge", "ownership": "private"},
        code="DK") is False


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")
