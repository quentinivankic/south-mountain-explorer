#!/usr/bin/env python3
"""Canada hiking_tag_wins: keep a hiking way the ingest filter drops for a ski-piste tag.
Runs in scripts/ca-run-area.sh right after the optional adopt_ways step and before ca_trail_filter,
only for an area whose registry entry has `hiking_tag_wins`. The generic assembler drops every way
tagged piste:type (skitour, nordic ...) with no way to override it, so a signed hiking trail that
someone also tagged as a ski piste (Glacier NP Great Glacier, Sleeping Giant Burma, Algonquin
Minnesing) is missing. Each entry {name, source, reason, way_ids | relation[, max_gap_m]} appends ONE
feature to the assembled `trails.geojson` (same schema as adopt_ways, source "hiking-tag-wins"), which
then goes through ca_trail_filter, publish_areas and the live-duplicate gates like any trail.
  way_ids   explicit OSM way ids (per-way opt-in). Every listed way must carry piste:type, except
            ways of an assembled same-name trail that the entry lists in full (see below);
  relation  "r<id>" of a route=hiking relation (per-relation opt-in): the whole signed route, every
            member way present in the extract, at least one carrying a piste:type tag. A member way
            that another assembled trail already owns stays with that trail and is left out.
An assembled trail with the entry's name built only from ways the entry itself lists is the shorter
copy of the same route and is replaced by the entry's trail; any other same-name trail is a refused clash.
Hiking evidence for every piste-tagged way, all fail closed (exit 2 "SAFETY BLOCK"; trails file untouched):
  the way is a member of a type=route route=hiking relation in the extract, or has foot=designated, or
  (same entry only) has foot=yes and the exact name of a way of that entry that has the first two;
  piste:type is present and every value is skitour or nordic (alpine, downhill, sled and the rest stay
  out); and the adopt_ways way checks pass: path/footway/steps/pedestrian (a highway=track only with
  foot=yes|designated and no motor-vehicle access tag), no access=private|no, foot=no, mtb:scale.
A way in only a ski-route relation, or a bare piste way, is refused, so a ski-only trail never gets in.
Per-trail opt-in `named_sac_evidence: true` (way_ids form only, no hiking relation exists; Garibaldi Lake
Trail, Elfin Lakes Trail) adds one more kind of evidence for a piste-tagged way of THAT entry: foot=yes, a
hiking sac_scale (hiking, mountain_hiking, demanding_mountain_hiking) and the exact name of a way of the same
entry that carries no piste:type tag. Under the same flag, and only for a way with foot=yes|designated and such
a sac_scale, access=no (foot overrides it) and an easy mtb:scale 0|1 (a hiking road that bikes also use) no
longer trip the adopt_ways way checks; mtb:scale >= 2, access=private and every other check stay in force.
Chaining, 90% inside the area boundary, no way already in another assembled trail and no name clash
are the adopt_ways checks, reused unchanged.
Nothing here touches shared engine code; no OSM edit is made. Absent key = this step never runs.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ca_adopt_ways  # noqa: E402
import ca_trail_filter  # noqa: E402
HTW_KEYS = {"name", "source", "reason"}
OPTIONAL_KEYS = {"way_ids", "relation", "max_gap_m", "named_sac_evidence"}
SAC_HIKING = {"hiking", "mountain_hiking", "demanding_mountain_hiking"}
EASY_MTB = {"0", "1"}
PISTE_OK = {"skitour", "nordic"}
ALLOWED_HIGHWAY = ca_adopt_ways.ALLOWED_HIGHWAY | {"track"}
MOTOR_TAGS = ("motor_vehicle", "motorcar", "vehicle")
RELATION_ID = re.compile(r"^r[1-9][0-9]*$")
SOURCE_TAG = "hiking-tag-wins"
class HikingTagWinsError(Exception):
    pass
def validate_hiking_tag_wins(items: object, where: str = "hiking_tag_wins") -> list[dict]:
    """The validated list, or HikingTagWinsError. Shared by ca_publish.load_registry."""
    if not isinstance(items, list) or not items:
        raise HikingTagWinsError(f"{where} must be a non-empty list when present")
    names: set[str] = set()
    used: set[int] = set()
    relations: set[str] = set()
    for item in items:
        if (not isinstance(item, dict) or not HTW_KEYS <= set(item)
                or set(item) - HTW_KEYS - OPTIONAL_KEYS):
            raise HikingTagWinsError(f"{where} entries need {sorted(HTW_KEYS)} plus way_ids or relation, "
                                     f"and may add max_gap_m")
        label = f"{where} {item.get('name')!r}"
        if ("way_ids" in item) == ("relation" in item):
            raise HikingTagWinsError(f"{label}: give exactly one of way_ids or relation")
        base = {k: item[k] for k in HTW_KEYS}
        base["way_ids"] = item.get("way_ids", [1])
        if "max_gap_m" in item:
            base["max_gap_m"] = item["max_gap_m"]
        if "named_sac_evidence" in item and (item["named_sac_evidence"] is not True or "way_ids" not in item):
            raise HikingTagWinsError(f"{label}: named_sac_evidence must be true and goes with way_ids only")
        try:
            ca_adopt_ways.validate_adopt_ways([base], where)
        except ca_adopt_ways.AdoptError as error:
            raise HikingTagWinsError(str(error)) from None
        if "relation" in item:
            if not isinstance(item["relation"], str) or not RELATION_ID.match(item["relation"]):
                raise HikingTagWinsError(f"{label}: relation must look like 'r123'")
            if item["relation"] in relations:
                raise HikingTagWinsError(f"{label}: relation {item['relation']} is listed twice")
            relations.add(item["relation"])
        else:
            if used & set(item["way_ids"]):
                raise HikingTagWinsError(f"{label}: a way id is already listed by another entry")
            used |= set(item["way_ids"])
        key = ca_trail_filter.fold(item["name"])
        if key in names:
            raise HikingTagWinsError(f"{where} repeats {item['name']!r}")
        names.add(key)
    return items
def piste_problems(wid: int, tags: dict[str, str]) -> list[str]:
    """Why a way's piste:type is not a skitour/nordic tag that hiking may override (empty = fine)."""
    raw = tags.get("piste:type")
    if raw is None:
        return [f"way {wid}: no piste:type tag, nothing for hiking_tag_wins to override"]
    values = {v.strip().lower() for v in raw.split(";")}
    if not values or not values <= PISTE_OK:
        return [f"way {wid}: piste:type={raw} (only {sorted(PISTE_OK)} can be overridden)"]
    return []
def track_problems(wid: int, tags: dict[str, str]) -> list[str]:
    """A highway=track is walkable evidence only with an explicit positive foot tag and no motor access."""
    if tags.get("highway") != "track":
        return []
    out = []
    if tags.get("foot") not in ("yes", "designated"):
        out.append(f"way {wid}: highway=track needs foot=yes or foot=designated")
    out += [f"way {wid}: highway=track with {k}={tags[k]}" for k in MOTOR_TAGS
            if tags.get(k) in ("yes", "designated", "permissive")]
    return out
def evidence_problems(wid: int, tags: dict[str, str], hiking_relations: list[int], named_evidence: set[str]) -> list[str]:
    """A way is a hiking way through a route=hiking relation or foot=designated, or (same entry only) by
    carrying a positive foot tag and the exact name of a way of that entry that has the first kind."""
    if hiking_relations or tags.get("foot") == "designated":
        return []
    if tags.get("foot") in ("yes", "designated") and tags.get("name") and tags["name"] in named_evidence:
        return []
    return [f"way {wid}: not in a route=hiking relation and no foot=designated (a ski-only way stays out)"]
def sac_named_evidence(tags: dict[str, str], anchors: set[str]) -> bool:
    """Opt-in evidence (named_sac_evidence): foot=yes, a hiking sac_scale and the exact name of a non-piste way
    of the same entry (`anchors`)."""
    return (tags.get("foot") == "yes" and tags.get("sac_scale") in SAC_HIKING
            and bool(tags.get("name")) and tags["name"] in anchors)
def tolerated_tags(tags: dict[str, str]) -> dict[str, str]:
    """named_sac_evidence entries only: the tags with access=no (when foot=yes|designated) and an easy mtb:scale
    (0|1) taken out, for a way that has foot=yes|designated and a hiking sac_scale. Else the same dict."""
    if tags.get("foot") not in ("yes", "designated") or tags.get("sac_scale") not in SAC_HIKING:
        return tags
    out = dict(tags)
    if out.get("access") == "no":
        del out["access"]
    if out.get("mtb:scale") in EASY_MTB:
        del out["mtb:scale"]
    return out if out != tags else tags
def read_relations(pbf: str) -> dict[int, dict]:
    """{relation id: {tags, ways}} for every relation in the extract (members of type way only)."""
    import osmium
    found: dict[int, dict] = {}
    class _H(osmium.SimpleHandler):
        def relation(self, r):
            found[r.id] = {"tags": {t.k: t.v for t in r.tags},
                           "ways": [m.ref for m in r.members if m.type == "w"]}
    _H().apply_file(pbf)
    return found
def hiking_relations_of(relations: dict[int, dict]) -> dict[int, list[int]]:
    """{way id: [route=hiking relation ids]}; a ski, piste or foot-route relation does not count."""
    out: dict[int, list[int]] = {}
    for rid, rel in relations.items():
        if rel["tags"].get("type") == "route" and rel["tags"].get("route") == "hiking":
            for w in rel["ways"]:
                out.setdefault(w, []).append(rid)
    return out
def resolve(entries: list[dict], relations: dict[int, dict]) -> list[dict]:
    """Entries with explicit way_ids (a relation entry becomes its piste-tagged member ways, filled
    in by build once the ways are read). Raises HikingTagWinsError for an absent or non-hiking relation."""
    out = []
    for entry in entries:
        item = dict(entry)
        if "relation" in item:
            rid = int(item["relation"][1:])
            rel = relations.get(rid)
            if rel is None:
                raise HikingTagWinsError(f"hiking_tag_wins {entry['name']!r}: {item['relation']} is not in the extract")
            if rel["tags"].get("type") != "route" or rel["tags"].get("route") != "hiking":
                raise HikingTagWinsError(f"hiking_tag_wins {entry['name']!r}: {item['relation']} is not type=route "
                                         "route=hiking")
            item["candidate_ways"] = list(dict.fromkeys(rel["ways"]))
        out.append(item)
    return out
def partial_copies(name: str, ids: set[int], features: list[dict]) -> list[dict]:
    """Assembled trails with this name built only from ways the entry itself lists (the shorter copy)."""
    key = ca_trail_filter.fold(name)
    return [f for f in features
            if ca_trail_filter.fold((f.get("properties") or {}).get("name")) == key
            and f["properties"].get("member_ways")
            and set(f["properties"]["member_ways"]) <= ids]
def build(entries: list[dict], relations: dict[int, dict], ways: dict[int, dict], features: list[dict],
          boundary: Any, area_name: str, excluded: frozenset[str] = frozenset()
          ) -> tuple[list[dict], list[dict], list[dict]]:
    """(new features, evidence, assembled partial copies to drop). `ways` holds every way id any entry
    can use (explicit or relation members). Raises HikingTagWinsError on any failed check."""
    hiking = hiking_relations_of(relations)
    adopt_entries = []
    view = dict(ways)            # what the adopt_ways way checks see; differs only for named_sac_evidence ways
    replaced: list[dict] = []
    extra: list[dict] = []
    for entry in resolve(entries, relations):
        label = f"hiking_tag_wins {entry['name']!r}"
        relation_mode = "candidate_ways" in entry
        if relation_mode:
            ids = [w for w in entry["candidate_ways"] if w in ways]
            if not any("piste:type" in ways[w]["tags"] for w in ids):
                raise HikingTagWinsError(f"{label}: {entry['relation']} has no piste-tagged member way in the extract")
        else:
            ids = list(entry["way_ids"])
            missing = [i for i in ids if i not in ways]
            if missing:
                raise HikingTagWinsError(f"{label}: ways missing from the extract: {missing[:8]}")
        partial = [f for f in partial_copies(entry["name"], set(ids), features) if not any(f is r for r in replaced)]
        partial_ways = {w for f in partial for w in f["properties"]["member_ways"]}
        skipped: list[int] = []
        if relation_mode:
            # A way another assembled trail already owns (Bald Hills / Maligne Pass) stays with that trail.
            owned = {w for f in features if not any(f is p for p in partial)
                     for w in (f.get("properties") or {}).get("member_ways") or []}
            skipped = [w for w in ids if w in owned]
            ids = [w for w in ids if w not in owned]
        flagged = entry.get("named_sac_evidence") is True and not relation_mode   # validator: way_ids form only
        anchors = {ways[i]["tags"]["name"] for i in ids
                   if "piste:type" not in ways[i]["tags"] and ways[i]["tags"].get("name")}
        sac_ways: list[int] = []
        named_evidence = {ways[i]["tags"]["name"] for i in ids
                          if "piste:type" in ways[i]["tags"] and ways[i]["tags"].get("name")
                          and (hiking.get(i) or ways[i]["tags"].get("foot") == "designated")}
        problems = []
        for i in ids:
            tags = ways[i]["tags"]
            if i in partial_ways:
                continue          # the assembler's own ingest filter already accepted this way
            if "piste:type" in tags:
                evidence = evidence_problems(i, tags, hiking.get(i, []), named_evidence)
                if evidence and flagged and sac_named_evidence(tags, anchors):
                    evidence = []
                    sac_ways.append(i)
                problems += piste_problems(i, tags) + evidence
            elif not relation_mode:
                problems += piste_problems(i, tags)
            problems += track_problems(i, tags)
        if problems:
            raise HikingTagWinsError(f"{label}: " + "; ".join(problems[:6]))
        if len(ids) > ca_adopt_ways.MAX_WAYS:
            raise HikingTagWinsError(f"{label}: {len(ids)} ways is more than {ca_adopt_ways.MAX_WAYS}")
        overrides = []
        if flagged:
            for i in ids:
                loosened = tolerated_tags(ways[i]["tags"])
                if loosened is not ways[i]["tags"]:
                    view[i] = {**ways[i], "tags": loosened}
                    overrides.append(i)
        adopt = {"name": entry["name"], "way_ids": ids, "source": entry["source"], "reason": entry["reason"],
                 "allow_piste": [i for i in ids if "piste:type" in ways[i]["tags"]]}
        if "max_gap_m" in entry:
            adopt["max_gap_m"] = entry["max_gap_m"]
        adopt_entries.append(adopt)
        replaced += partial
        extra.append({"left_with_other_trails": skipped, "named_sac_evidence_ways": sorted(sac_ways),
                      "tolerated_access_or_mtb_ways": sorted(overrides)})
    # A partial copy the whole route replaces, or a trail the registry's exclude_trails removes next, is not a clash.
    live = [f for f in features if (f.get("properties") or {}).get("name") not in excluded
            and not any(f is r for r in replaced)]
    try:
        new, evidence = ca_adopt_ways.build_features(adopt_entries, view, live, boundary, area_name, excluded,
                                                  ALLOWED_HIGHWAY)
    except ca_adopt_ways.AdoptError as error:
        raise HikingTagWinsError(str(error).replace("adopt_ways", "hiking_tag_wins", 1)) from None
    for feature, item, more in zip(new, evidence, extra):
        item.update(more)
        props = feature["properties"]
        props["source"] = SOURCE_TAG
        props["ckey"] = props["ckey"].replace("adopt-", "htw-", 1)
        props["hiking_tag_wins_source"] = props.pop("adopt_source")
        item["hiking_relations"] = sorted({r for w in item["way_ids"] for r in hiking.get(w, [])})
        item["replaced_partial_trails"] = [r["properties"]["name"] for r in replaced
                                           if ca_trail_filter.fold(r["properties"]["name"]) == ca_trail_filter.fold(item["name"])]
        item["foot_designated"] = sorted(w for w in item["way_ids"] if ways[w]["tags"].get("foot") == "designated")
    return new, evidence, replaced
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--registry", required=True)
    ap.add_argument("--area-id", required=True)
    ap.add_argument("--pbf", required=True)
    ap.add_argument("--trails", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args(argv)
    try:
        registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
        entry = next((a for a in registry["areas"] if a["id"] == args.area_id), None)
        if entry is None or "hiking_tag_wins" not in entry:
            raise HikingTagWinsError(f"{args.area_id}: no hiking_tag_wins in the registry")
        entries = validate_hiking_tag_wins(entry["hiking_tag_wins"], f"{args.area_id}: hiking_tag_wins")
        trails_path = Path(args.trails)
        doc = json.loads(trails_path.read_text(encoding="utf-8"))
        features = doc["features"]
        relations = read_relations(args.pbf)
        wanted: set[int] = set()
        for e in entries:
            if "relation" in e:
                rel = relations.get(int(e["relation"][1:]))
                wanted |= set(rel["ways"]) if rel else set()
            else:
                wanted |= set(e["way_ids"])
        ways = ca_adopt_ways.read_ways(args.pbf, wanted)
        new, evidence, replaced = build(entries, relations, ways, features, ca_adopt_ways.load_boundary(trails_path),
                              entry["name"],
                              frozenset(i["id"].split(":", 1)[1] for i in entry.get("exclude_trails") or []
                                        if str(i.get("id", "")).startswith(("n:", "n*:"))))
    except (HikingTagWinsError, OSError, ValueError, KeyError) as error:
        print(f"SAFETY BLOCK: {error}", file=sys.stderr)
        return 2
    doc["features"] = [f for f in features if not any(f is r for r in replaced)] + new
    trails_path.write_text(json.dumps(doc), encoding="utf-8")
    Path(args.report).write_text(json.dumps({"area": args.area_id, "kept": evidence}, indent=2) + "\n",
                                 encoding="utf-8")
    print(f"hiking_tag_wins: {len(new)} trail(s) appended to {trails_path.name}")
    return 0
if __name__ == "__main__":
    sys.exit(main())
