# assemble/ — trail-object assembly (v3)

**Implemented and unit-tested** (`model.py` + `assemble.py`, `test_model.py`
— 11 tests incl. a synthetic Devils Bridge that reaches the arch). The
homelab RUNS and tunes it on real OSM. Algorithm below is the contract the
code follows, distilled from `../SPEC.md`.

## The algorithm shape locked in by the plan

Input: `data/aoi/<name>.osm.pbf` (or the full hiking subset).
Output: `data/aoi/<name>.trails.geojson` — **one Feature per assembled trail**.

1. **Relations first.** Every `type=route` + `route=hiking|foot|walking`
   relation IS a trail object. Resolve superrelations transitively
   (the Te Araroa/GR20 lesson — see `data-pipeline/build/route_index.py`
   for a working two-pass pyosmium pattern). Outside Denmark, relation members
   retain the original standalone `_is_trailish` eligibility contract. For the
   Denmark pilot only, signed hiking identity may restore default-foot main-line
   `residential`, `primary`, `tertiary`, `unclassified`, `living_street`,
   `pedestrian`, ordinary no-subtype `service`, and `footway=crossing` members.
   This is relation-only: none become standalone road-name checklist trails.
   Explicit `foot/access=no|private`, motorway/trunk/construction/raceway,
   motorized-only tags, indoor/non-hiking signals, and unsafe or malformed
   service subtypes remain fail-closed. Any `service`, `unclassified`, or
   `living_street` member with explicit `motor_vehicle/motorcar=yes|designated`
   requires `foot=yes|designated|permissive`; motor permission alone never
   supplies walking permission. Every present semicolon-separated
   service token is normalized; parking aisle/space, driveway/drive-through,
   emergency access, bus, blank, and unknown forms are denied. An explicitly
   classified alley retains the positive-foot requirement. Tracks retain the
   stricter legacy road-name, motor, and lane gates. Denmark restoration parses
   positive integer `lanes` components separated by semicolons: any component
   >=2 or malformed nonempty value is road-like and denied. Outside Denmark,
   legacy standalone and relation behavior is unchanged. A `steps` member
   remains part of the trail.

   The QA report preserves every relation/way road measurement, tags, names,
   surfaces, lanes, exact-area miles, and share for manual review. Miles,
   percentages, and aggregates are diagnostic only; there is no numerical
   road-content publication threshold. Terminal safety comes from exact-area
   unsafe/missing/inconsistent direct members and unexplained OSM topology.
2. **Name-stitch what remains.** Ways not claimed by a relation group by
   normalized name across `highway` type boundaries (path→steps→path must
   NOT break a trail) when connected end-to-end (shared nodes). This
   replaces Systems 1/2's name-only grouping that fragmented at every
   type change.
3. **Attach spurs.** Unnamed (or differently-tagged) segments that connect
   to exactly one assembled trail and terminate at (or near) a destination
   POI — `natural=peak/arch`, `waterway=waterfall`, `tourism=viewpoint`,
   `mountain_pass=yes` — are welded onto that trail. This is precisely the
   missing 838 ft of Devils Bridge.
4. **Emit trail records:** stable id, display name (canonical `name`, with
   `name:en` fallback for display), full MultiLineString geometry, length,
   destination POI(s) reached, and raw curation signals (sac_scale, access,
   informal, network, …) for downstream scoring — scoring itself stays a
   separate, tunable policy exactly as in System 2.

For exact-area Denmark QA, clipping and the sliver floor use unrounded geometry
and mileage. The legacy `area_name is None` path preserves the prior rounded
three-decimal floor. Source-connected relations and name stitches split only by
the selected boundary carry the independent `boundary_induced_split=true`
fact; blocking `quality_disposition` values remain intact. Before assembly, the
pilot uses three targeted canonical ledgers, each exported from its sealed PBF:
raw Denmark and the full prefilter are first reduced with the pinned
`osmium getid -r --id-file` root-closure pattern (without tag stripping, so
direct member-way authority survives), while the compact AOI PBF
remains render scope. Raw Denmark is primary relation and way/node authority,
the full prefilter is the transformation witness, and the AOI is render scope.
The three scopes have fixed artifact paths and atomic provenance receipts that
bind each compact PBF to its canonical parent label, parent SHA/bytes/object
counts, selected-root hash, exact osmium command/version, compact
SHA/bytes/object counts, and workflow stage. `aoi.sh` emits its receipt from the
same shell array it executes and binds the canonical AOI name, bbox, and
root+bbox hash. Each relation-ledger creator immediately publishes the exact
ledger SHA/bytes through `$GITHUB_OUTPUT`; `aoi.sh` also publishes the topology
ledger SHA/bytes from its creator before emitting its PBF/receipt values. The
raw, prefilter, and AOI stage outputs therefore own their semantic-ledger
identities rather than trusting package-local manifest resealing. After package
sealing, those step outputs create an exclusive, read-only trust file outside
the QA tree; it is neither sealed nor uploaded, and final validation compares
it independently with every PBF, relation ledger, AOI topology ledger, receipt,
transformation receipt, and manifest seal before semantic parsing. The AOI
compact digest must differ from both broader closures. Equal raw/prefilter closures remain valid only when
the independently trusted parent identities differ and the trusted exact
prefilter command receipt binds raw input to filtered output. The default
`make aoi` path keeps its original `linestring,point` raw export and requires no
pyosmium topology exporter. The Denmark pilot explicitly sets the complete AOI
provenance environment, fails before extraction when its tools or pyosmium are
unavailable, widens only that raw export to include polygons, and records every
AOI way plus every relation's tags and ordered members directly from the PBF.
The AOI source-topology ledger must exactly match receipt way/relation counts,
contain the selected boundary and a root feature intersecting the pinned bbox,
and may carry an out-of-bbox smart reference only when it is bound either to
the verified selected-root graph or directly to an AOI relation matching the
prefilter's exact area selectors (`boundary=protected_area|national_park`,
`leisure=nature_reserve`, or `landuse=forest`). Relation 7046785 remains
mandatory by ID but receives no selector bypass: its tags must independently
qualify under the same policy. Those references are never standalone/render
candidates. The full raw Denmark PBF is never archived. Every direct member
carries raw, prefilter, AOI, and exact-area status.
AOI-absent child relations use their own recursive canonical/main raw geometry:
proven-outside children are informational, in-area loss is terminal, and
incomplete location evidence is terminal. The validator independently derives
`entirely-filtered` before considering superroute removal, preserving terminal
unsafe/missing evidence when no eligible main geometry survives; only a root
with eligible main geometry can become `removed-thru-hike`. The removed
hierarchy itself comes from accepted raw route relations containing accepted
route-relation children, including those child identities; producer status text
never controls member dispositions. Candidate relation identity is rebuilt
without candidate relation declarations: every ordinary emitted root's exact-area linework and complete contributing way IDs are
constructed from the raw graph and verified audit, then matched by geometry and
source-way authority to exactly one output candidate. For exact-quality
Denmark, relation display naming is deterministic: canonical `name`, then the
first non-empty `name:*` in sorted key order, then the first named eligible main
way in raw member order using that same sorted localized fallback. Producer
candidate, relation audit, graph-derived authority, and validator all use this
order; non-exact assembly, including `region=dk` without the exact-area gate,
retains its legacy source-tag insertion order. The resulting name is validated only after authority matching, so a name rewrite
cannot erase provenance. A root with no output candidate is accepted only when
a complete relation-backed curation row, typed exact-area outside/0.05-mi
sliver row, or `geometry-duplicate-absorbed` row binds its root, hierarchy,
direct members, full eligible preclip member/source identity, source records,
and authoritative preclip geometry. Exact-area intersection is additional
evidence and never selects which preclip ways a drop may disclose. A fully
outside root may retain any curation category that `_removal_verdict`
independently reproduces; `outside-exact-area` remains a clip-stage category.
An absorption row is created only after model curation, exact clipping,
final prepromotion same-name/area coalescing, stable-geometry POI promotion and
name rewriting, and Denmark quality disposition. No geometry fusion or linework
append occurs after promotion. Early Denmark dedupe and postpromotion
hike-fragment comparisons preserve relation candidates and private target
lineage. Stable
internal IDs are assigned to the complete pre-curation population, so a target
removed by model curation still reaches the terminal resolver with its removal stage, category, reason, and successor
lineage. The resolver follows winner chains with cycle and ambiguity fallback,
then may name only a survivor still present in the published population. Every
unresolved source is serialized in quality schema 7 with one exact reason
(`cycle`, `divergent-targets`, `missing-target`, `clipped-target`,
`model-curation-removed-target`, `quality-removed-target`, `coverage-failure`,
or `ambiguous-survivor`), source/target roots and identities, disposition
stage/category/reason, successor IDs, and canonical geometry hashes. The Mols
exact-Denmark producer exits nonzero when this list is nonempty; unrelated
regions retain the legacy fallback. Each successful row
binds the survivor's final key/name/source and canonical geometry hash, and
carries an exact-area linework coverage/equality proof while retaining the
absorbed root's complete preclip source identity. Validation independently
rebuilds both preclip geometries, replays exact-area coverage, verifies the final
geometry hash, and rejects lost linework, intermediate targets, cycles, or
ambiguous survivors.

The creator-owned AOI topology schema 3 also preserves every node in the exact
Denmark destination policy: `natural=peak|arch|saddle|cliff|rock|stone`,
`tourism=viewpoint|attraction`,
`historic=archaeological_site|castle|ruins`, `amenity=shelter`, and
`highway=trailhead`. Each row binds the stable OSM node ID, complete tags,
deterministic NFC display name (`name`, then sorted `name:*`), coordinate, and
eligibility class. The exact AOI export includes tagged OSM nodes as GeoJSON
`Point` features; final validation independently indexes every eligible point
and requires exact node ID, tags, normalized name, coordinate, and class parity
with the topology ledger. No exact-Denmark destination node type is exempt from
that point-export contract. Exact-Denmark promotion is deferred until after
boundary clipping and a final same-name/area coalescing pass. Promotion then
runs once against immutable candidate linework. `promote_hikes` records exactly
one selected destination in rank order: a named eligible POI within 250 feet of
a degree-one pre-weld relation main-way endpoint that remains an endpoint of
the final candidate. Unnamed welded non-member linework can improve geometry
but never creates naming authority. Shortest great-circle distance wins, then
OSM node ID, then stable endpoint order. An ambiguous nearest endpoint is
recorded and skipped; ranking continues until an unambiguous root/way/node
source is found. If multiple final candidates resolve to the same promoted
name, one owner is selected by endpoint distance, POI OSM ID, authoritative
root order, then stable candidate index. Every other contender remains a route
with a `destination-already-claimed` decision; no postpromotion fusion occurs.
Distance uses the 3,958.7613-mile earth radius and is serialized in feet with
the source endpoint coordinate, root relation ID, authoritative source way/node
identity, reach limit, rank, tags, class, name, and node ID. The endpoint index
is optional diagnostic display data; it is never endpoint authority. For a
fused multi-root candidate, the selected endpoint must map to exactly one
contributing root; zero-root or ambiguous-root endpoints do not promote. Before
promotion, every multi-root candidate orders its roots by the verified producer
authority and takes its exact display name (`name`, sorted `name:*`, then first
named eligible main way) plus identity tags from the first root. The QA-only
`identity_root_relation_id` records that root on the candidate and decision row.
Promotion eligibility is then replayed for every contributing root: any
`rwn`/`nwn`/`iwn` root declines `not-local-route`; otherwise any root that does
not classify as a route declines `not-route-candidate`. Nonlocal takes
precedence when both classes occur, while every blocking root ID and reason is
retained in authority order. Fused candidate tags cannot override this root
authority. Single-root and non-exact/non-Denmark behavior is unchanged.

Quality schema 7 contains one `promotion_decisions` row for every final
pre-quality relation candidate, including candidates later removed by quality
or terminal absorption. A row is either `promoted`, with POI/root/way/node,
distance, and rank, or `declined` with an exact reason such as
`no-authoritative-endpoints`, `no-eligible-poi-in-reach`,
`ambiguous-endpoint-authority`, or `destination-already-claimed`; skipped
ambiguous POIs remain explicit evidence. Final validation independently derives
every candidate's surviving per-root pre-weld endpoints from sealed graph,
final candidate, and topology evidence, reruns POI ranking, ambiguity
fall-through, and cross-candidate ownership, then requires exact agreement
between the complete decision ledger and candidate kind/name/destination
evidence before allowing the single deterministic `<destination> Trail` name.
`region=dk` without exact-area enforcement retains the legacy POI table and
promotion behavior and emits no Denmark-only destination evidence or decision
ledger. QA-only evidence is not copied into app rows.
Ordinary drop linework must not appear
in output, while an absorbed root may appear only through its verified terminal
survivor. Multi-root curation rows replay the declared
verdict against every root's authoritative name and tags. Roots follow the raw
root order, each root contributes its depth-first relation hierarchy in member
order with first occurrence winning, and direct memberships retain that
relation order. Every matched authority way must remain in the candidate's
member, source geometry, source record, and rendered-way fields even when another way has identical or
containing geometry. A candidate's declared relation/root/direct fields must
equal the rebuilt values exactly. Shared same-identity ways include every
matching root, legitimate same-name root/name-stitch fusion remains valid, and
unmatched, multiply matched, extra, or ambiguous identities fail closed. Raw/prefilter-present
members proven outside the exact area and removed-thru-hike-only gaps remain
visible but informational; prefilter/AOI loss affecting exact-area geometry,
relevant raw absence, authority mismatch, unsafe in-area members, and
unexplained topology remain terminal.

Exact assembly writes a one-feature `*.exact-area.geojson` with relation
identity and canonical geometry hash. The Mols dry-run publisher must consume
that exact Polygon/MultiPolygon (including holes) under `--exact-boundary`; it
does not re-merge neighbouring PBF areas. The final validator independently
matches it to exactly one assembly target relation and reuses it for publisher
replay. Replay includes conversion, the sealed-empty Mols nonhiking sidecar,
degenerate-remnant pruning/recounts, and the fresh-output no-prior-parking
contract. The final validator also rebuilds hierarchy/order, roles, tags, node
IDs/coordinates, exact clipping, app rows and distances, SVG layer geometry and
IDs, current Bjergetapen binding, and all artifact seals. Preview output is
dry-run-only and confined to an explicit non-symlink runner QA root.

Closed standalone `highway=pedestrian` exclusion is Denmark-only; AZ, CA, and
the global legacy path retain named closed pedestrian loops. In Denmark,
`area!=no` closedness comes from the canonical AOI source-way node topology,
never `osmium export`'s visual Polygon/LineString choice. Named unclaimed rings
receive exterior-ring diagnostics; unnamed rings and graph-bound members of an
emitted signed relation (including outside-clipped and non-rendering
approach/connection roles) remain auditable without duplicate removal rows.
`area=no` remains linear in every region. The compact run-33 and run-34 replays
are test-only and never app output.

### Denmark rule sets (`--dk-rules`, registry `rules`)

`golden/dk-areas.json` gives every entry `rules`: `legacy` (Mols, the pinned
control; output unchanged), `dk-v2` (every other area today) or `dk-v3` (dk-v2
plus the whole-route policy below; opt-in per area, nothing live changes until an
area is republished with it). `scripts/dk-run-area.sh` reads it and passes
`--dk-rules`. `dk-v2` adds the quality removal
`dk-road-name` (like the US `FR`/road-code drops, but Danish names):

- Applies only to rootless name stitches. A signed hiking relation, any way in
  one, or any way with `sac_scale`/`trail_visibility`/`hiking`/`trail`/`route`
  hiking tags is never removed. Names with a trail word (sti, rute, spor, trail,
  vandre, pilgrim, camino, sløjfe, rundtur) or a historic road stem (hærvej,
  ridevej, oksevej, drivvej) are never removed.
- `road-class-way`: last word is a street word (-vej, -gade, -alle/-allé,
  -stræde, -boulevard, -torv, -plads, -vænge, or definite -vejen, -alleen, ...)
  and a rendered way is track/service/residential/unclassified/tertiary/etc.
- `street-name-on-path`: on plain path/footway/pedestrian ways, only compound
  `...vej`, -gade/-alle/-stræde/-torv/-plads (and definite -gaden/...), or a
  genitive `X-s Vej` ("Bangs Vej"). "Den Vilde Vej" and "-vejen" stay.
- `access-point`: -zugang / -adgang.
- Forest-ride words count too: -linie/-linje (also definite -linien/-linjen),
  `Ride`, -stræde(n). On a plain path a compound `...vejen`/`...linie` is a
  forest ride ("Hjortevejen", "Jydelinie"); a bare "Den Vilde Vej" is not.
  Historic stems hulvej, kongevej, oldtidsvej, vikingevej are never removed.

`dk-v2` also adds these removals (evidence rules; all tag/name based):
- `dk-mtb-trail` (rootless name stitches only): at least 50% of the rendered
  length is MTB/cycle infrastructure: `mtb:scale` present (any value, 0 and up),
  `mtb:scale:imba`, a one-way `highway=path`, `bicycle=designated` or
  `highway=cycleway` without `foot=yes/designated/permissive`. A way is exempt
  (never counted) when it is in a hiking route relation, has `foot=designated`,
  `sac_scale`, `trail_visibility` or other hiking tags. Separately, an MTB
  trail-centre name (Rutchebanen, Evil Autobahn, Lubi-Lubi, Urania, Karrusellen,
  singletrack, flowtrail, ...) on an `mtb:scale` way is removed unless that way
  has `foot=designated` or `sac_scale`.
- `dk-non-hiking`: a bridle path (every rendered way is `horse=designated` or
  `highway=bridleway` with no foot=yes/designated) or a name that is a bridle
  path, running event or airstrip (ridesti, ...løbet, løberute, trackbane,
  landingsbane). Applies to relation-backed trails by name too.
- `dk-long-distance`: a clipped leg (`clipped` and at least 0.25 mi of the route
  lies outside) of a relation with network nwn, iwn or rwn, or a clipped or
  rootless trail whose WHOLE name is a known long-distance route (Hærvejen,
  Camønoen, Gudenåstien, Vestkyststien, Marguerit, Aarhus-Silkeborg,
  Nordsjællandsruten, Skjoldungestien, Djurslandstien, Marskstien, Kyst til
  Kyst, Pilgrimsrute). A name that only CONTAINS such a token ("Hærvejen rundt")
  is kept and listed in the report as `dk_long_distance_ambiguous`.
- `dk-fragment`: a rootless name stitch under 0.3 mi (`DK_FRAGMENT_MIN_MI`).
  Relation-backed trails are never removed by length.

`dk-v2` also supports a reviewed per-area removal list, `dk-curated-exclude`
(registry field `exclude_trails`, passed as `--dk-curated-exclude <json>` by
`scripts/dk-run-area.sh`; dk-v2 only, rejected for `legacy`):
- Each entry is `{"id", "name", "reason"}`. `id` is `r<OSM relation id>` (matches
  the trail's relation or root relation) or `n:<exact trail name>` (name stitches);
  `name` is the readable trail name and must equal the matched trail (for `n:` it
  is the id name); `reason` is the written justification, recorded on the removed
  feature (`curated_exclude_reason`), in the assembly report
  (`dk_curated_excluded`) and in the qualify report (`curated_excluded`).
- Applied after every automatic rule, so it only sees trails the rules kept.
  FAIL CLOSED: an entry that matches zero or more than one kept trail, or whose
  name differs, fails the area (assemble exits 2, `dk_curated_exclude_failures`),
  so stale lists (a trail already removed by a rule, renamed or gone from OSM)
  cannot rot silently.
- The blessed table is computed after exclusions; the excluded names go into the
  proposed `forbidden_names`. No geometry is created or edited.
- `hold: "curated-do-not-ship"` marks areas a curation pass reviewed and rejected.

### `dk-v3`: whole routes, bridged gaps, curated inclusions
dk-v2 clips every route to the OSM polygon and rejects a signed relation whose
clipped pieces are not provably one object. Both lose real routes: OSM
polygons are often smaller than the official Naturstyrelsen extent (a route
leaves and re-enters the polygon), and a relation whose member ways stop 5 to
50 m short of each other (a gate, a road crossing) is a single marked walk.
`dk-v3` (everything in dk-v2, plus):
- **Whole routes.** A signed route relation (name present, network lwn or
  untagged, never nwn/iwn/rwn, never a known long-distance name) whose area
  polygon holds at least `DK_V3_MIN_ROUTE_SHARE` (25%) of its whole length is
  published with its complete geometry: the real OSM member ways, unclipped
  (`dk_whole_route`, `dk_inside_mi`, `dk_route_share`; never `clipped`).
  `serve/publish_areas.py` honours the flag (exact-boundary mode only): no
  all-parks clamp, no second majority test. The decision is made on the
  unclipped candidates in `quality.dk_v3_whole_route_decisions`, before the clip.
- **Routes mostly elsewhere.** A relation route that is still clipped and below
  that share is removed as `dk-route-elsewhere` (no polygon-edge slivers of a
  route that belongs to a neighbouring area).
- **Bridged gaps.** A whole route passes connectivity when its member ways are
  one OSM-node-connected piece, or when every gap between pieces is at most
  `DK_V3_MAX_BRIDGE_M` (100 m, nearest real member geometry, single linkage).
  Nothing is drawn across a gap: the pieces stay separate segments (the iOS app
  scores completion per segment and only requires each segment to be covered).
  Wider gaps, missing member ways, or rendered geometry that is not exactly the
  union of the member ways stay `unresolved-relation-gap`. Evidence per route:
  `connectivity.widest_gap_m`, report `dk_v3_whole_routes`.
- **Curated inclusions** (registry `include_trails`, `--dk-curated-include`,
  dk-v3 only): `{"id": "r<relation>" | "n:<name>", "name", "reason"}`, same shape
  and validation as `exclude_trails`. A listed trail is published whole whatever
  its share of the polygon (an official route outside the OSM polygon, a named
  staircase under the 0.3 mi fragment limit). It still has to pass the
  structural gates (missing members, gaps, exact geometry). FAIL CLOSED: an
  entry that matches no candidate, matches several, is renamed, or is not
  published fails the area (assemble exits 2, `dk_curated_include_failures`).
- Because dk-v3 removes some trails earlier than dk-v2 did, a curated
  `exclude_trails` entry whose trail is now removed by `dk-route-elsewhere` is
  stale and fails the area: re-curate when moving an area to dk-v3.
The US/other-region path never sets `keep_whole`, so its output is unchanged.
Long-distance routes are not exempt from `unresolved-relation-gap`. The US
thru-hike list (`_THRU_HIKE_RE`) removes named thru-hikes, and
`publish_areas` keeps a route only in a park holding most of its length; neither
bypasses a connectivity gate, so there is no Danish equivalent to add.

## Definition of done (first milestone)

`make aoi && python3 assemble/assemble.py --aoi sedona` produces a
`Devils Bridge Trail` feature whose geometry passes within
`reach_tolerance_ft` of the golden destination. Then run the full golden
suite and iterate in the viewer (`make qa`).
