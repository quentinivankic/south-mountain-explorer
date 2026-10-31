"""Denmark dk-v3 only: a display prefix for trails OSM names only by colour or number.

OSM names many marked Danish routes just 'Grøn rute', 'Gul' or 'Tur 2'. Inside one
area that is fine; in the app's search and lists it is indistinguishable from every
other area's 'Grøn rute'. The registry field `trail_name_prefix` (a place name,
e.g. 'Svanninge') is applied here, and only to such generic names:

    'Grøn rute'  ->  'Svanninge Grøn rute'     (osm_name keeps 'Grøn rute')

Rules (all pinned by test_dk_name_prefix.py):
  * a name is generic when EVERY token is a colour word, an ordinal/number word,
    a digit string or a filler word (rute, tur, spor, sti, og, ...) AND at least one
    token is a colour/ordinal/digit. A name that carries a place or any other word
    ('Kirkestien', 'Pamhule - rød rute', 'Spor ved Tiset') is never touched;
  * the separator is one space: it matches how live Danish names read ('Kollerup
    Plantage Blå') and keeps 'svanninge grøn' findable by the app's plain
    contains-search (a '·' or '-' separator would break that query);
  * the trail id is NOT derived from the display name: publish_areas hands the id
    derivation `osm_name`, so a prefix can never move a trail id (completion
    progress is keyed by trail id).
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

MAX_PREFIX_LEN = 40

COLOUR_WORDS = frozenset({
    "rød", "røde", "rødt", "gul", "gule", "gult", "blå", "blåt", "grøn", "grønne",
    "grønt", "orange", "brun", "brune", "brunt", "sort", "sorte", "lilla", "hvid",
    "hvide", "hvidt", "grå", "pink", "turkis", "lyserød", "lyseblå", "lyseblåt",
    "lysegrøn", "mørkeblå", "mørkegrøn", "violet",
    # English colours appear on a few bilingual Danish signs.
    "red", "yellow", "blue", "green", "brown", "purple", "white", "black",
})
ORDINAL_WORDS = frozenset({
    "første", "andet", "anden", "tredje", "fjerde", "femte", "sjette", "syvende",
    "ottende", "niende", "tiende",
})
FILLER_WORDS = frozenset({
    "rute", "ruten", "spor", "sporet", "tværspor", "tur", "løberute", "vandrerute",
    "vandretur", "route", "trail", "nr", "og", "sti", "stien",
})
_SPLIT = re.compile(r"[\s,;:/()\-–.]+")


def _tokens(name) -> list[str]:
    return [t for t in _SPLIT.split(unicodedata.normalize("NFC", str(name or "")).casefold()) if t]


def is_generic_name(name) -> bool:
    """True when the name is only colour/number/route words (see module doc)."""
    tokens = _tokens(name)
    if not tokens:
        return False
    if not all(t in COLOUR_WORDS or t in ORDINAL_WORDS or t in FILLER_WORDS or t.isdigit()
               for t in tokens):
        return False
    return any(t in COLOUR_WORDS or t in ORDINAL_WORDS or t.isdigit() for t in tokens)


def validate_prefix(prefix) -> str:
    """The prefix text, or ValueError. Plain, NFC, trimmed, 1..40 characters."""
    if not isinstance(prefix, str) or not prefix.strip():
        raise ValueError("name prefix must be a non-empty text")
    if prefix != prefix.strip() or prefix != unicodedata.normalize("NFC", prefix):
        raise ValueError("name prefix must be trimmed NFC text")
    if len(prefix) > MAX_PREFIX_LEN:
        raise ValueError(f"name prefix is longer than {MAX_PREFIX_LEN} characters")
    if any(unicodedata.category(c).startswith("C") for c in prefix):
        raise ValueError("name prefix contains a control character")
    if is_generic_name(prefix):
        raise ValueError("name prefix must name a place, not a colour or number")
    return prefix


def apply_name_prefix(features: list[dict], prefix: str) -> tuple[dict, list[str]]:
    """Prefix the generic-named features IN PLACE; return (report, failures).

    Each renamed feature keeps its OSM name in properties['osm_name']. A rename
    that would give a trail the display name of a DIFFERENT trail is a failure
    (nothing is silently merged or disambiguated); the caller fails the area.
    """
    validate_prefix(prefix)
    renamed = []
    for feature in features:
        props = feature.get("properties") or {}
        name = props.get("name")
        if not isinstance(name, str) or not name:
            continue
        if not is_generic_name(name):
            continue
        renamed.append(feature)
    original = Counter((f.get("properties") or {}).get("name") for f in features)
    failures: list[str] = []
    new_names = {id(f): f"{prefix} {f['properties']['name']}" for f in renamed}
    # A new name may only coincide with other features that are renamed from the
    # SAME original name (they were already one name before; nothing new merges).
    final = Counter()
    for f in features:
        props = f.get("properties") or {}
        final[new_names.get(id(f), props.get("name"))] += 1
    for f in renamed:
        old, new = f["properties"]["name"], new_names[id(f)]
        if final[new] != original[old]:
            failures.append(f"name-prefix collision: {old!r} -> {new!r} equals another trail's name")
    if failures:
        return {"prefix": prefix, "renamed": [], "unchanged": len(features)}, sorted(set(failures))
    rows = []
    for f in renamed:
        props = f["properties"]
        props["osm_name"] = props["name"]
        props["name"] = new_names[id(f)]
        rows.append({"osm_name": props["osm_name"], "name": props["name"],
                     "kind": props.get("kind"), "length_mi": props.get("length_mi")})
    rows.sort(key=lambda r: (r["osm_name"], str(r["length_mi"])))
    return ({"prefix": prefix, "renamed": rows,
             "unchanged": len(features) - len(rows)}, [])
