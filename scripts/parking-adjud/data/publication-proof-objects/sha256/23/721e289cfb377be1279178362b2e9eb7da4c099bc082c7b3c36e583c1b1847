# Blind parking challenger

You are the challenger for one Trekdex parking facility. Judge it independently.

- Read `{PROTOCOL_PATH}` (SHA-256 `{PROTOCOL_SHA256}`) and `{LESSONS_PATH}` (SHA-256 `{LESSONS_SHA256}`).
- Read only the packet at `{PACKET_PATH}` and the Z1/Z2/Z3 paths named inside it.
- Do not access the network, fetch a URL, or ask for fetch permission; `external_evidence` must remain empty.
- Do not read any verdict draft, primary decision, resolver manifest, challenger/arbiter output, or filename that encodes a verdict.
- Your host-bound identity is `{MODEL_ID}` in family `{MODEL_FAMILY}`. Do not alter or repeat that identity in your output.
- Apply the full EXISTS/PUBLIC/SERVES protocol. Return a complete canonical decision for the packet, even when your answer is REVIEW.
- Write exactly one JSON object to `{OUTPUT_PATH}` after every update; no prose outside it:

```json
{
  "assignment_id": "{ASSIGNMENT_ID}",
  "decision": {
    "fid": 1,
    "area": "area-slug",
    "osm": ["way/1"],
    "verdict": "KEEP",
    "prior": "surveyed",
    "exists": {"call": "yes", "evidence": "specific frame evidence"},
    "public": {"call": "yes", "evidence": "specific data/frame evidence"},
    "serves": {"call": "yes", "evidence": "specific trail/walk evidence"},
    "frames_used": ["z1", "z2", "z3"],
    "tags_cited": {},
    "confidence": "strong",
    "resolve_hint": null
  },
  "external_evidence": []
}
```

`external_evidence` must stay empty for a blind challenge. The host validates assignment, packet identity, schema, and all content hashes.
