"""Shared, side-effect-free source normalization for parking verdicts.

The sidecar builder and trust replay both consume this module so store-key
identity, facility lookup, emitted aliases, and effective provenance cannot
drift between the publication and verification paths.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

OSM_KEY = "osm"
FID_KEY = "fid"
_OSM_ID_RE = re.compile(r"(?:node|way|relation)/[1-9][0-9]*")
_DECIMAL_FID_RE = re.compile(r"(?:0|[1-9][0-9]*)")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_DOSSIER_NAME_RE = re.compile(
    r"([a-z0-9]+(?:-[a-z0-9]+)*)_dossier\.json"
)

LEGACY_BASELINE_VERSION = 2
LEGACY_BASELINE_KIND = "parking-proofless-source-baseline"
LEGACY_BASELINE_SCHEMA = {
    "canonical_json": "utf-8-sort-keys-compact-no-nan-v1",
    "row_identity": ["store", "source_key", "row"],
    "row_sha256": "sha256-canonical-json",
    "store_sha256": "sha256-exact-bytes",
    "corpus_sha256": "sha256-store-nul-bytes-nul-v1",
    "dossier_inventory": "sorted-canonical-dossier-filenames-v1",
    "dossier_sha256": "sha256-exact-bytes",
    "dossier_corpus_sha256": "sha256-dossier-nul-bytes-nul-v1",
}

PUBLICATION_FLOOR_VERSION = 1
PUBLICATION_FLOOR_KIND = "parking-publication-floor"
PUBLICATION_FLOOR_SCHEMA = {
    "canonical_json": "utf-8-sort-keys-compact-no-nan-v1",
    "entry_identity": [
        "first_authority_kind",
        "first_authority_sha256",
        "first_attestation_sha256",
        "first_proof_sha256",
    ],
    "floor_sha256": "sha256-canonical-json-without-self-hash",
}

PUBLICATION_TRUST_ROOT_VERSION = 1
PUBLICATION_TRUST_ROOT_KIND = "parking-publication-trust-root"
PUBLICATION_TRUST_ROOT_SCHEMA = {
    "canonical_json": "utf-8-indent-2-sort-keys-no-nan-newline-v1",
    "artifact_sha256": "sha256-exact-bytes",
    "dossier_inventory": "sorted-canonical-dossier-filenames-v1",
    "path_derivation": "configured-store-filename-v1",
    "proof_registry_absence": "null",
}
_STORE_FILENAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]*\.json")
_SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def publication_artifact_filenames(store_filename: str) -> tuple[str, str, str]:
    """Derive canonical store/proof/floor basenames without accepting paths."""
    if (not isinstance(store_filename, str)
            or _STORE_FILENAME_RE.fullmatch(store_filename) is None
            or Path(store_filename).name != store_filename):
        raise ValueError("parking verdict store filename is noncanonical")
    stem = store_filename[:-5]
    return (
        store_filename,
        f"{stem}_publication_proofs.json",
        f"{stem}_publication_floor.json",
    )


@dataclass(frozen=True)
class PublicationTrustRoot:
    """Immutable exact-byte image rooted by a literal code pin."""

    exact_sha256: str
    generation: int
    parent_root_sha256: str | None
    baseline_filename: str
    baseline_sha256: str
    _stores: tuple[tuple[str, tuple[tuple[str, object], ...]], ...]
    _dossiers: tuple[tuple[str, str], ...]

    @property
    def store_names(self) -> tuple[str, ...]:
        return tuple(name for name, _entry in self._stores)

    @property
    def stores_by_name(self) -> dict[str, dict[str, object]]:
        return {
            name: dict(entry) for name, entry in self._stores
        }

    @property
    def dossiers_by_name(self) -> dict[str, str]:
        return dict(self._dossiers)


@dataclass(frozen=True)
class StoreSpec:
    filename: str
    key_kind: str
    dossier_slug: str | None
    default_judged: str | None


@dataclass(frozen=True)
class NormalizedStoreRecord:
    facility: dict
    aliases: tuple[str, ...]
    effective_src: object
    effective_judged: object
    fold_signature: dict


@dataclass(frozen=True)
class LegacyRowBaseline:
    """Immutable exact-row and dossier-byte allowlist for proofless rows."""

    _self_sha256: str
    _rows: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]
    _dossiers: tuple[tuple[str, int, str], ...]
    _dossier_corpus_sha256: str

    @property
    def self_sha256(self) -> str:
        return self._self_sha256

    @property
    def store_names(self) -> tuple[str, ...]:
        return tuple(store for store, _rows in self._rows)

    @property
    def has_proofless_rows(self) -> bool:
        return any(rows for _store, rows in self._rows)

    @property
    def dossier_names(self) -> tuple[str, ...]:
        return tuple(name for name, _size, _sha256 in self._dossiers)

    def keys_for(self, store_name: str) -> frozenset[str]:
        for store, rows in self._rows:
            if store == store_name:
                return frozenset(key for key, _row_hash in rows)
        raise ValueError(f"legacy baseline has no configured store {store_name}")

    def matches(self, store_name: str, source_key: str, row: dict) -> bool:
        expected = None
        for store, rows in self._rows:
            if store == store_name:
                expected = dict(rows).get(source_key)
                break
        return expected is not None and expected == legacy_row_sha256(
            store_name, source_key, row
        )

    def require_exact_dossiers(
            self, dossier_bytes_by_name: dict[str, bytes]) -> None:
        """Require the captured canonical dossier inventory and exact byte hashes."""
        expected = {
            name: (size, sha256)
            for name, size, sha256 in self._dossiers
        }
        actual_names = set(dossier_bytes_by_name)
        expected_names = set(expected)
        if actual_names != expected_names:
            added = sorted(actual_names - expected_names)
            removed = sorted(expected_names - actual_names)
            raise ValueError(
                "legacy proofless-row dossier inventory mismatch: "
                f"added={added[:5]} removed={removed[:5]}"
            )
        corpus_hash = hashlib.sha256()
        for name in sorted(expected):
            raw = dossier_bytes_by_name[name]
            if not isinstance(raw, bytes):
                raise ValueError(f"captured dossier {name} is not exact bytes")
            size, expected_hash = expected[name]
            actual_hash = hashlib.sha256(raw).hexdigest()
            if len(raw) != size or actual_hash != expected_hash:
                raise ValueError(
                    f"legacy proofless-row dossier bytes changed: {name}"
                )
            corpus_hash.update(
                name.encode("utf-8") + b"\0" + raw + b"\0"
            )
        if corpus_hash.hexdigest() != self._dossier_corpus_sha256:
            raise ValueError(
                "legacy proofless-row dossier corpus hash mismatch"
            )


class _SnapshotLease:
    __slots__ = ("active",)

    def __init__(self) -> None:
        self.active = True

    def revoke(self) -> None:
        self.active = False


_SNAPSHOT_ISSUER = object()


class ValidatedStoreSnapshot:
    """Opaque immutable capability valid only while its lock lease is active."""

    __slots__ = (
        "_data_dir", "_store_names", "_store_bytes", "_proof_bytes",
        "_floor_bytes", "_dossier_bytes", "_lease",
    )

    def __init_subclass__(cls, **kwargs) -> None:
        del kwargs
        raise TypeError("ValidatedStoreSnapshot may not be subclassed")

    def __setattr__(self, name, value) -> None:
        del name, value
        raise AttributeError("ValidatedStoreSnapshot is immutable")

    def __getattribute__(self, name):
        if name in {"__class__", "__slots__", "_require_active"}:
            return object.__getattribute__(self, name)
        lease = object.__getattribute__(self, "_lease")
        if not lease.active:
            raise ValueError("validated store snapshot lease has expired")
        if name == "_lease":
            raise AttributeError("validated store snapshot lease is private")
        return object.__getattribute__(self, name)

    def __init__(self, data_dir: str | Path, store_names: tuple[str, ...],
                 store_bytes: tuple[tuple[str, bytes], ...],
                 proof_bytes: tuple[tuple[str, bytes | None], ...],
                 floor_bytes: tuple[tuple[str, bytes], ...],
                 dossier_bytes: tuple[tuple[str, bytes], ...] = (), *,
                 _lease: _SnapshotLease | None = None,
                 _issuer: object = None) -> None:
        if _issuer is not _SNAPSHOT_ISSUER or not isinstance(_lease, _SnapshotLease):
            raise TypeError(
                "ValidatedStoreSnapshot values are issued only by the trust replay gate"
            )
        if not _lease.active:
            raise ValueError("validated store snapshot lease is not active")
        object.__setattr__(self, "_data_dir", Path(data_dir).resolve())
        object.__setattr__(self, "_store_names", tuple(store_names))
        object.__setattr__(self, "_store_bytes", tuple(
            (name, bytes(raw)) for name, raw in store_bytes
        ))
        object.__setattr__(self, "_proof_bytes", tuple(
            (name, None if raw is None else bytes(raw))
            for name, raw in proof_bytes
        ))
        object.__setattr__(self, "_floor_bytes", tuple(
            (name, bytes(raw)) for name, raw in floor_bytes
        ))
        object.__setattr__(self, "_dossier_bytes", tuple(
            (name, bytes(raw)) for name, raw in dossier_bytes
        ))
        object.__setattr__(self, "_lease", _lease)

    def _require_active(self) -> None:
        lease = object.__getattribute__(self, "_lease")
        if not lease.active:
            raise ValueError("validated store snapshot lease has expired")

    @property
    def store_names(self) -> tuple[str, ...]:
        self._require_active()
        return self._store_names

    @property
    def store_bytes_by_name(self) -> dict[str, bytes]:
        self._require_active()
        return dict(self._store_bytes)

    @property
    def proof_bytes_by_name(self) -> dict[str, bytes | None]:
        self._require_active()
        return dict(self._proof_bytes)

    @property
    def floor_bytes_by_name(self) -> dict[str, bytes]:
        self._require_active()
        return dict(self._floor_bytes)

    @property
    def dossier_bytes_by_name(self) -> dict[str, bytes]:
        self._require_active()
        return dict(self._dossier_bytes)

    def documents_for(self, data_dir: str | Path,
                      expected_store_names: tuple[str, ...]) -> dict[str, dict]:
        self._check_scope(data_dir, expected_store_names)
        documents = {}
        for name, raw in self._store_bytes:
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise ValueError(f"{self._data_dir / name} must contain an object")
            documents[name] = value
        return documents

    def dossier_indexes_for(
            self, data_dir: str | Path,
            expected_store_names: tuple[str, ...]) -> tuple[dict, dict]:
        self._check_scope(data_dir, expected_store_names)
        return dossier_indexes_from_bytes(dict(self._dossier_bytes))

    def _check_scope(self, data_dir: str | Path,
                     expected_store_names: tuple[str, ...]) -> None:
        self._require_active()
        if Path(data_dir).resolve() != self._data_dir:
            raise ValueError("validated store snapshot belongs to a different data directory")
        if expected_store_names != self._store_names:
            raise ValueError("validated store snapshot does not match configured stores")


def _issue_validated_snapshot(
        data_dir: str | Path, ordered_store_names: tuple[str, ...],
        store_bytes_by_name: dict[str, bytes],
        proof_bytes_by_name: dict[str, bytes | None],
        floor_bytes_by_name: dict[str, bytes],
        dossier_bytes_by_name: dict[str, bytes]) -> ValidatedStoreSnapshot:
    """Issue a capability after replay_trust has completed the proof gate."""
    if len(set(ordered_store_names)) != len(ordered_store_names):
        raise ValueError("configured parking verdict stores must be unique")
    expected = set(ordered_store_names)
    if (set(store_bytes_by_name) != expected
            or set(proof_bytes_by_name) != expected
            or set(floor_bytes_by_name) != expected):
        raise ValueError(
            "validated snapshot does not contain every configured "
            "store/proof/floor triple"
        )
    if (any(not isinstance(name, str) or not isinstance(raw, bytes)
            for name, raw in dossier_bytes_by_name.items())
            or len(dossier_bytes_by_name) != len(set(dossier_bytes_by_name))):
        raise ValueError("validated snapshot has malformed dossier bytes")
    store_bytes = tuple(
        (name, bytes(store_bytes_by_name[name])) for name in ordered_store_names
    )
    proof_bytes = tuple(
        (name, None if proof_bytes_by_name[name] is None
         else bytes(proof_bytes_by_name[name]))
        for name in ordered_store_names
    )
    floor_bytes = tuple(
        (name, bytes(floor_bytes_by_name[name])) for name in ordered_store_names
    )
    dossier_bytes = tuple(
        (name, bytes(dossier_bytes_by_name[name]))
        for name in sorted(dossier_bytes_by_name)
    )
    lease = _SnapshotLease()
    return ValidatedStoreSnapshot(
        data_dir, ordered_store_names, store_bytes, proof_bytes, floor_bytes,
        dossier_bytes, _lease=lease, _issuer=_SNAPSHOT_ISSUER,
    )


def _revoke_validated_snapshot(snapshot: ValidatedStoreSnapshot) -> None:
    if type(snapshot) is not ValidatedStoreSnapshot:
        raise TypeError("only an issued validated snapshot can be revoked")
    object.__getattribute__(snapshot, "_lease").revoke()


def configured_store_specs(stores: list | tuple,
                           key_kinds: dict[str, str]) -> tuple[StoreSpec, ...]:
    """Convert the legacy three-column configuration to explicit schemas."""
    specs = []
    seen = set()
    for configured in stores:
        if not isinstance(configured, (list, tuple)) or len(configured) != 3:
            raise ValueError("parking verdict store configuration must have three fields")
        filename, dossier_slug, default_judged = configured
        if not isinstance(filename, str) or not filename or filename in seen:
            raise ValueError("parking verdict store filenames must be unique strings")
        publication_artifact_filenames(filename)
        if (dossier_slug is not None
                and (not isinstance(dossier_slug, str)
                     or _SLUG_RE.fullmatch(dossier_slug) is None)):
            raise ValueError(f"{filename}: dossier slug is noncanonical")
        if default_judged is not None and not isinstance(default_judged, str):
            raise ValueError(f"{filename}: default judged date must be a string or null")
        seen.add(filename)
        key_kind = key_kinds.get(filename)
        if key_kind is None:
            # Compatibility for isolated callers with a synthetic store. The
            # baseline path/hash remain mandatory for proofless test rows.
            key_kind = FID_KEY if dossier_slug is not None else OSM_KEY
        if key_kind not in (OSM_KEY, FID_KEY):
            raise ValueError(f"{filename}: unknown source-key kind {key_kind!r}")
        specs.append(StoreSpec(
            filename=filename,
            key_kind=key_kind,
            dossier_slug=dossier_slug,
            default_judged=default_judged,
        ))
    return tuple(specs)


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def legacy_row_sha256(store_name: str, source_key: str, row: dict) -> str:
    return sha256_json({
        "store": store_name,
        "source_key": source_key,
        "row": row,
    })


def _reject_duplicate_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _strict_json_loads(raw: bytes, context: str) -> object:
    try:
        return json.loads(
            raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"{context} is not strict JSON: {error}") from error


_PUBLICATION_TRUST_STORE_KEYS = {
    "key_kind", "dossier_slug", "default_judged", "store_sha256",
    "proof_registry_sha256", "publication_floor_sha256",
}


def publication_trust_root_json_bytes(document: dict) -> bytes:
    """Return the one canonical on-disk encoding for a publication root."""
    return (json.dumps(
        document, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False,
    ) + "\n").encode("utf-8")


def _require_root_lineage(generation: object,
                          parent_root_sha256: object) -> tuple[int, str | None]:
    if type(generation) is not int or generation < 1:
        raise ValueError("publication trust root generation must be a positive integer")
    if generation == 1:
        if parent_root_sha256 is not None:
            raise ValueError("generation-one publication trust root must have no parent")
        return generation, None
    if (not isinstance(parent_root_sha256, str)
            or _SHA256_RE.fullmatch(parent_root_sha256) is None):
        raise ValueError("successor publication trust root requires a lowercase parent hash")
    return generation, parent_root_sha256


def _require_exact_byte_map(expected_names: set[str], values: object,
                            context: str, *, optional: bool = False) -> dict:
    if not isinstance(values, dict) or set(values) != expected_names:
        raise ValueError(f"{context} does not exactly match configured stores")
    result = {}
    for name in expected_names:
        raw = values[name]
        if optional and raw is None:
            result[name] = None
        elif not isinstance(raw, bytes):
            raise ValueError(f"{context} for {name} is not exact bytes")
        else:
            result[name] = bytes(raw)
    return result


def build_publication_trust_root_document(
        specs: tuple[StoreSpec, ...],
        store_bytes_by_name: dict[str, bytes],
        proof_bytes_by_name: dict[str, bytes | None],
        floor_bytes_by_name: dict[str, bytes],
        baseline_filename: str, baseline_bytes: bytes,
        dossier_bytes_by_name: dict[str, bytes], *, generation: int = 1,
        parent_root_sha256: str | None = None) -> dict:
    """Build a deterministic exact-byte trust image from captured artifacts."""
    generation, parent_root_sha256 = _require_root_lineage(
        generation, parent_root_sha256
    )
    if not isinstance(specs, tuple) or not specs:
        raise ValueError("publication trust root requires configured stores")
    expected_names = {spec.filename for spec in specs}
    if len(expected_names) != len(specs):
        raise ValueError("publication trust root store configuration is duplicated")
    for spec in specs:
        publication_artifact_filenames(spec.filename)
    stores = _require_exact_byte_map(
        expected_names, store_bytes_by_name, "publication trust root stores"
    )
    proofs = _require_exact_byte_map(
        expected_names, proof_bytes_by_name,
        "publication trust root proof registries", optional=True,
    )
    floors = _require_exact_byte_map(
        expected_names, floor_bytes_by_name,
        "publication trust root publication floors",
    )
    if (not isinstance(baseline_filename, str)
            or _STORE_FILENAME_RE.fullmatch(baseline_filename) is None
            or Path(baseline_filename).name != baseline_filename
            or not isinstance(baseline_bytes, bytes)):
        raise ValueError("publication trust root baseline image is noncanonical")
    if not isinstance(dossier_bytes_by_name, dict):
        raise ValueError("publication trust root dossier image must be a byte map")
    dossier_indexes_from_bytes(dossier_bytes_by_name)
    dossiers = []
    for name in sorted(dossier_bytes_by_name):
        raw = dossier_bytes_by_name[name]
        if (_DOSSIER_NAME_RE.fullmatch(name) is None
                or not isinstance(raw, bytes)):
            raise ValueError("publication trust root dossier image is noncanonical")
        dossiers.append({
            "filename": name,
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    store_entries = {}
    for spec in specs:
        proof = proofs[spec.filename]
        store_entries[spec.filename] = {
            "key_kind": spec.key_kind,
            "dossier_slug": spec.dossier_slug,
            "default_judged": spec.default_judged,
            "store_sha256": hashlib.sha256(stores[spec.filename]).hexdigest(),
            "proof_registry_sha256": (
                None if proof is None else hashlib.sha256(proof).hexdigest()
            ),
            "publication_floor_sha256": hashlib.sha256(
                floors[spec.filename]
            ).hexdigest(),
        }
    return {
        "version": PUBLICATION_TRUST_ROOT_VERSION,
        "kind": PUBLICATION_TRUST_ROOT_KIND,
        "schema": copy.deepcopy(PUBLICATION_TRUST_ROOT_SCHEMA),
        "generation": generation,
        "parent_root_sha256": parent_root_sha256,
        "legacy_baseline": {
            "filename": baseline_filename,
            "sha256": hashlib.sha256(baseline_bytes).hexdigest(),
        },
        "dossiers": dossiers,
        "stores": store_entries,
    }


def parse_publication_trust_root(
        raw: bytes, specs: tuple[StoreSpec, ...], expected_root_sha256: str,
        expected_baseline_filename: str) -> PublicationTrustRoot:
    """Parse only canonical bytes whose exact hash matches the code pin first."""
    if (not isinstance(expected_root_sha256, str)
            or _SHA256_RE.fullmatch(expected_root_sha256) is None):
        raise ValueError("configured publication trust root SHA-256 is required")
    if not isinstance(raw, bytes):
        raise ValueError("publication trust root must be exact bytes")
    exact_sha256 = hashlib.sha256(raw).hexdigest()
    if exact_sha256 != expected_root_sha256:
        raise ValueError("publication trust root exact-byte pin mismatch")

    value = _strict_json_loads(raw, "publication trust root")
    required = {
        "version", "kind", "schema", "generation", "parent_root_sha256",
        "legacy_baseline", "dossiers", "stores",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("publication trust root schema mismatch")
    if publication_trust_root_json_bytes(value) != raw:
        raise ValueError("publication trust root bytes are noncanonical")
    if (type(value.get("version")) is not int
            or value["version"] != PUBLICATION_TRUST_ROOT_VERSION
            or value.get("kind") != PUBLICATION_TRUST_ROOT_KIND
            or value.get("schema") != PUBLICATION_TRUST_ROOT_SCHEMA):
        raise ValueError("publication trust root version/kind/schema mismatch")
    generation, parent_root_sha256 = _require_root_lineage(
        value.get("generation"), value.get("parent_root_sha256")
    )

    publication_artifact_filenames(expected_baseline_filename)
    baseline = value.get("legacy_baseline")
    if (not isinstance(baseline, dict)
            or set(baseline) != {"filename", "sha256"}
            or baseline.get("filename") != expected_baseline_filename
            or not isinstance(baseline.get("sha256"), str)
            or _SHA256_RE.fullmatch(baseline["sha256"]) is None):
        raise ValueError("publication trust root baseline identity mismatch")

    stores = value.get("stores")
    expected_names = [spec.filename for spec in specs]
    if (not isinstance(stores, dict)
            or set(stores) != set(expected_names)
            or len(expected_names) != len(set(expected_names))):
        raise ValueError("publication trust root configured store set mismatch")
    immutable_stores = []
    for spec in specs:
        publication_artifact_filenames(spec.filename)
        entry = stores[spec.filename]
        if (not isinstance(entry, dict)
                or set(entry) != _PUBLICATION_TRUST_STORE_KEYS
                or entry.get("key_kind") != spec.key_kind
                or entry.get("dossier_slug") != spec.dossier_slug
                or entry.get("default_judged") != spec.default_judged):
            raise ValueError(
                f"publication trust root store identity mismatch for {spec.filename}"
            )
        for field in ("store_sha256", "publication_floor_sha256"):
            if (not isinstance(entry.get(field), str)
                    or _SHA256_RE.fullmatch(entry[field]) is None):
                raise ValueError(
                    f"publication trust root {field} is malformed for {spec.filename}"
                )
        proof_sha256 = entry.get("proof_registry_sha256")
        if (proof_sha256 is not None
                and (not isinstance(proof_sha256, str)
                     or _SHA256_RE.fullmatch(proof_sha256) is None)):
            raise ValueError(
                f"publication trust root proof registry hash is malformed for {spec.filename}"
            )
        immutable_stores.append((
            spec.filename, tuple((key, copy.deepcopy(entry[key]))
                                 for key in sorted(entry)),
        ))

    dossiers = value.get("dossiers")
    if not isinstance(dossiers, list):
        raise ValueError("publication trust root dossier inventory is malformed")
    immutable_dossiers = []
    names = []
    for entry in dossiers:
        if (not isinstance(entry, dict)
                or set(entry) != {"filename", "sha256"}
                or not isinstance(entry.get("filename"), str)
                or _DOSSIER_NAME_RE.fullmatch(entry["filename"]) is None
                or not isinstance(entry.get("sha256"), str)
                or _SHA256_RE.fullmatch(entry["sha256"]) is None):
            raise ValueError("publication trust root dossier inventory is malformed")
        names.append(entry["filename"])
        immutable_dossiers.append((entry["filename"], entry["sha256"]))
    if names != sorted(set(names)):
        raise ValueError("publication trust root dossier inventory is noncanonical")

    return PublicationTrustRoot(
        exact_sha256=exact_sha256,
        generation=generation,
        parent_root_sha256=parent_root_sha256,
        baseline_filename=baseline["filename"],
        baseline_sha256=baseline["sha256"],
        _stores=tuple(immutable_stores),
        _dossiers=tuple(immutable_dossiers),
    )


def validate_publication_trust_root_image(
        root: PublicationTrustRoot, specs: tuple[StoreSpec, ...],
        store_bytes_by_name: dict[str, bytes],
        proof_bytes_by_name: dict[str, bytes | None],
        floor_bytes_by_name: dict[str, bytes], baseline_filename: str,
        baseline_bytes: bytes, dossier_bytes_by_name: dict[str, bytes]) -> None:
    """Require one captured corpus to equal the root's exact byte image."""
    if not isinstance(root, PublicationTrustRoot):
        raise TypeError("publication trust image requires a parsed trust root")
    expected_names = {spec.filename for spec in specs}
    stores = _require_exact_byte_map(
        expected_names, store_bytes_by_name, "publication trust image stores"
    )
    proofs = _require_exact_byte_map(
        expected_names, proof_bytes_by_name,
        "publication trust image proof registries", optional=True,
    )
    floors = _require_exact_byte_map(
        expected_names, floor_bytes_by_name,
        "publication trust image publication floors",
    )
    if root.store_names != tuple(spec.filename for spec in specs):
        raise ValueError("publication trust image store ordering mismatch")
    rooted_stores = root.stores_by_name
    for spec in specs:
        entry = rooted_stores[spec.filename]
        actual = {
            "store_sha256": hashlib.sha256(stores[spec.filename]).hexdigest(),
            "proof_registry_sha256": (
                None if proofs[spec.filename] is None
                else hashlib.sha256(proofs[spec.filename]).hexdigest()
            ),
            "publication_floor_sha256": hashlib.sha256(
                floors[spec.filename]
            ).hexdigest(),
        }
        for field, digest in actual.items():
            if entry[field] != digest:
                raise ValueError(
                    f"publication trust root {field} mismatch for {spec.filename}"
                )
    if (baseline_filename != root.baseline_filename
            or not isinstance(baseline_bytes, bytes)
            or hashlib.sha256(baseline_bytes).hexdigest()
            != root.baseline_sha256):
        raise ValueError("publication trust root baseline bytes mismatch")
    if not isinstance(dossier_bytes_by_name, dict):
        raise ValueError("publication trust root dossier image is malformed")
    rooted_dossiers = root.dossiers_by_name
    if set(dossier_bytes_by_name) != set(rooted_dossiers):
        raise ValueError("publication trust root dossier inventory mismatch")
    for name, expected_sha256 in rooted_dossiers.items():
        raw_dossier = dossier_bytes_by_name[name]
        if (not isinstance(raw_dossier, bytes)
                or hashlib.sha256(raw_dossier).hexdigest() != expected_sha256):
            raise ValueError(
                f"publication trust root dossier bytes mismatch for {name}"
            )


def build_publication_trust_root_successor(
        root: PublicationTrustRoot, target_store: str,
        store_after_bytes: bytes, proof_after_bytes: bytes,
        floor_after_bytes: bytes) -> dict:
    """Advance only one configured store triple and bind the exact parent."""
    if not isinstance(root, PublicationTrustRoot):
        raise TypeError("publication trust root successor requires a parsed root")
    if target_store not in root.store_names:
        raise ValueError("publication trust root successor target is not configured")
    for raw in (store_after_bytes, proof_after_bytes, floor_after_bytes):
        if not isinstance(raw, bytes):
            raise ValueError("publication trust root successor requires exact bytes")
    stores = root.stores_by_name
    stores[target_store]["store_sha256"] = hashlib.sha256(
        store_after_bytes
    ).hexdigest()
    stores[target_store]["proof_registry_sha256"] = hashlib.sha256(
        proof_after_bytes
    ).hexdigest()
    stores[target_store]["publication_floor_sha256"] = hashlib.sha256(
        floor_after_bytes
    ).hexdigest()
    return {
        "version": PUBLICATION_TRUST_ROOT_VERSION,
        "kind": PUBLICATION_TRUST_ROOT_KIND,
        "schema": copy.deepcopy(PUBLICATION_TRUST_ROOT_SCHEMA),
        "generation": root.generation + 1,
        "parent_root_sha256": root.exact_sha256,
        "legacy_baseline": {
            "filename": root.baseline_filename,
            "sha256": root.baseline_sha256,
        },
        "dossiers": [
            {"filename": name, "sha256": sha256}
            for name, sha256 in root._dossiers
        ],
        "stores": stores,
    }


def legacy_baseline_json_bytes(document: dict) -> bytes:
    return (json.dumps(
        document, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False,
    ) + "\n").encode("utf-8")


def build_legacy_baseline_document(
        specs: tuple[StoreSpec, ...],
        store_bytes_by_name: dict[str, bytes],
        dossier_bytes_by_name: dict[str, bytes]) -> dict:
    """Build a deterministic review artifact from an explicitly captured image."""
    expected_names = tuple(spec.filename for spec in specs)
    if set(store_bytes_by_name) != set(expected_names):
        raise ValueError("legacy baseline input does not exactly match configured stores")
    if not isinstance(dossier_bytes_by_name, dict):
        raise ValueError("legacy baseline dossier input must be an exact byte map")
    # Validate canonical names and all dossier content before binding the bytes.
    dossier_indexes_from_bytes(dossier_bytes_by_name)
    dossier_configuration = []
    dossier_corpus_hash = hashlib.sha256()
    for name in sorted(dossier_bytes_by_name):
        raw = dossier_bytes_by_name[name]
        if not isinstance(raw, bytes):
            raise ValueError(f"legacy baseline dossier {name} is not exact bytes")
        dossier_configuration.append({
            "filename": name,
            "byte_length": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
        dossier_corpus_hash.update(
            name.encode("utf-8") + b"\0" + raw + b"\0"
        )

    configuration = []
    rows_by_store = {}
    total = 0
    corpus_hash = hashlib.sha256()
    for spec in specs:
        raw = bytes(store_bytes_by_name[spec.filename])
        value = _strict_json_loads(raw, spec.filename)
        if not isinstance(value, dict):
            raise ValueError(f"{spec.filename} must contain an object")
        rows = {}
        for source_key, row in value.items():
            if not isinstance(row, dict):
                raise ValueError(f"{spec.filename}:{source_key} row must be an object")
            validate_store_source_key(spec, source_key, row)
            rows[source_key] = legacy_row_sha256(
                spec.filename, source_key, row
            )
        rows_by_store[spec.filename] = {
            key: rows[key] for key in sorted(rows)
        }
        configuration.append({
            "filename": spec.filename,
            "key_kind": spec.key_kind,
            "row_count": len(rows),
            "store_sha256": hashlib.sha256(raw).hexdigest(),
        })
        total += len(rows)
        corpus_hash.update(spec.filename.encode("utf-8") + b"\0" + raw + b"\0")
    body = {
        "version": LEGACY_BASELINE_VERSION,
        "kind": LEGACY_BASELINE_KIND,
        "schema": copy.deepcopy(LEGACY_BASELINE_SCHEMA),
        "configuration": {
            "stores": configuration,
            "dossiers": dossier_configuration,
            "source_row_count": total,
            "corpus_sha256": corpus_hash.hexdigest(),
            "dossier_corpus_sha256": dossier_corpus_hash.hexdigest(),
        },
        "rows": rows_by_store,
    }
    return {**body, "baseline_sha256": sha256_json(body)}


def parse_legacy_baseline(
        raw: bytes, specs: tuple[StoreSpec, ...],
        expected_baseline_sha256: str) -> LegacyRowBaseline:
    """Strictly load a reviewed baseline pinned by configuration."""
    if (not isinstance(expected_baseline_sha256, str)
            or _SHA256_RE.fullmatch(expected_baseline_sha256) is None):
        raise ValueError("configured legacy baseline SHA-256 is required")
    value = _strict_json_loads(raw, "legacy proofless-row baseline")
    if not isinstance(value, dict):
        raise ValueError("legacy proofless-row baseline must be an object")
    required = {
        "version", "kind", "schema", "configuration", "rows",
        "baseline_sha256",
    }
    if set(value) != required:
        raise ValueError("legacy proofless-row baseline schema mismatch")
    if legacy_baseline_json_bytes(value) != raw:
        raise ValueError("legacy proofless-row baseline bytes are noncanonical")
    if (type(value.get("version")) is not int
            or value["version"] != LEGACY_BASELINE_VERSION
            or value.get("kind") != LEGACY_BASELINE_KIND
            or value.get("schema") != LEGACY_BASELINE_SCHEMA):
        raise ValueError("legacy proofless-row baseline version/kind/schema mismatch")
    body = dict(value)
    claimed = body.pop("baseline_sha256", None)
    computed = sha256_json(body)
    if claimed != computed or claimed != expected_baseline_sha256:
        raise ValueError("legacy proofless-row baseline self-hash/configuration mismatch")

    configuration = value.get("configuration")
    rows_by_store = value.get("rows")
    configuration_keys = {
        "stores", "dossiers", "source_row_count", "corpus_sha256",
        "dossier_corpus_sha256",
    }
    if (not isinstance(configuration, dict)
            or set(configuration) != configuration_keys
            or not isinstance(configuration.get("stores"), list)
            or not isinstance(configuration.get("dossiers"), list)
            or type(configuration.get("source_row_count")) is not int
            or configuration["source_row_count"] < 0
            or not isinstance(rows_by_store, dict)):
        raise ValueError("legacy proofless-row baseline configuration is malformed")
    for field in ("corpus_sha256", "dossier_corpus_sha256"):
        if _SHA256_RE.fullmatch(str(configuration.get(field))) is None:
            raise ValueError(
                f"legacy proofless-row baseline {field} is malformed"
            )

    immutable_dossiers = []
    dossier_names = []
    for entry in configuration["dossiers"]:
        if (not isinstance(entry, dict)
                or set(entry) != {"filename", "byte_length", "sha256"}
                or not isinstance(entry.get("filename"), str)
                or _DOSSIER_NAME_RE.fullmatch(entry["filename"]) is None
                or type(entry.get("byte_length")) is not int
                or entry["byte_length"] < 0
                or not isinstance(entry.get("sha256"), str)
                or _SHA256_RE.fullmatch(entry["sha256"]) is None):
            raise ValueError(
                "legacy proofless-row baseline dossier inventory is malformed"
            )
        dossier_names.append(entry["filename"])
        immutable_dossiers.append((
            entry["filename"], entry["byte_length"], entry["sha256"],
        ))
    if dossier_names != sorted(set(dossier_names)):
        raise ValueError(
            "legacy proofless-row baseline dossier inventory is noncanonical"
        )

    expected_names = [spec.filename for spec in specs]
    if set(rows_by_store) != set(expected_names):
        raise ValueError("legacy proofless-row baseline store set mismatch")
    configured = configuration["stores"]
    if len(configured) != len(specs):
        raise ValueError("legacy proofless-row baseline store configuration mismatch")
    immutable_rows = []
    total = 0
    for spec, entry in zip(specs, configured):
        if (not isinstance(entry, dict)
                or set(entry) != {
                    "filename", "key_kind", "row_count", "store_sha256",
                }
                or entry.get("filename") != spec.filename
                or entry.get("key_kind") != spec.key_kind
                or type(entry.get("row_count")) is not int
                or entry["row_count"] < 0
                or _SHA256_RE.fullmatch(str(entry.get("store_sha256"))) is None):
            raise ValueError(
                f"legacy proofless-row baseline configuration mismatch for {spec.filename}"
            )
        rows = rows_by_store[spec.filename]
        if not isinstance(rows, dict) or len(rows) != entry["row_count"]:
            raise ValueError(
                f"legacy proofless-row baseline row count mismatch for {spec.filename}"
            )
        validated_rows = []
        for source_key, row_hash in rows.items():
            if (not isinstance(source_key, str)
                    or not isinstance(row_hash, str)
                    or _SHA256_RE.fullmatch(row_hash) is None):
                raise ValueError(
                    f"legacy proofless-row baseline has malformed row identity in {spec.filename}"
                )
            if (spec.key_kind == OSM_KEY
                    and _OSM_ID_RE.fullmatch(source_key) is None):
                raise ValueError(
                    f"legacy proofless-row baseline has noncanonical OSM key {source_key!r}"
                )
            if (spec.key_kind == FID_KEY
                    and _DECIMAL_FID_RE.fullmatch(source_key) is None):
                raise ValueError(
                    f"legacy proofless-row baseline has noncanonical fid key {source_key!r}"
                )
            validated_rows.append((source_key, row_hash))
        validated_rows.sort()
        immutable_rows.append((spec.filename, tuple(validated_rows)))
        total += len(validated_rows)
    if total != configuration["source_row_count"]:
        raise ValueError("legacy proofless-row baseline total row count mismatch")
    return LegacyRowBaseline(
        claimed, tuple(immutable_rows), tuple(immutable_dossiers),
        configuration["dossier_corpus_sha256"],
    )


@dataclass(frozen=True)
class PublicationFloor:
    """One closed, self-hashed append-only set of ever-current source keys."""

    store_name: str
    self_sha256: str
    _entries: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]

    @property
    def keys(self) -> frozenset[str]:
        return frozenset(key for key, _entry in self._entries)

    @property
    def entries_by_key(self) -> dict[str, dict[str, str]]:
        return {
            key: dict(entry) for key, entry in self._entries
        }


def publication_floor_json_bytes(document: dict) -> bytes:
    return (json.dumps(
        document, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False,
    ) + "\n").encode("utf-8")


def publication_floor_entry(row: dict,
                            authority: tuple[str, str, str]) -> dict[str, str]:
    """Bind the immutable first authority and publication-proof identity."""
    attestation = row.get("publication_attestation")
    if (not isinstance(authority, tuple) or len(authority) != 3
            or not isinstance(authority[0], str) or not authority[0]
            or _SHA256_RE.fullmatch(str(authority[1])) is None
            or not isinstance(attestation, dict)):
        raise ValueError("current row cannot produce a publication-floor identity")
    values = {
        "first_authority_kind": authority[0],
        "first_authority_sha256": authority[1],
        "first_attestation_sha256": attestation.get("attestation_sha256"),
        "first_proof_sha256": attestation.get("publication_proof_sha256"),
    }
    if any(not isinstance(value, str) for value in values.values()):
        raise ValueError("publication-floor identity fields must be strings")
    for field in (
        "first_authority_sha256", "first_attestation_sha256",
        "first_proof_sha256",
    ):
        if _SHA256_RE.fullmatch(values[field]) is None:
            raise ValueError(f"publication-floor {field} is malformed")
    return values


def build_publication_floor_document(
        store_name: str,
        entries_by_key: dict[str, dict[str, str]]) -> dict:
    if not isinstance(store_name, str) or not store_name.endswith(".json"):
        raise ValueError("publication floor requires a canonical store filename")
    if not isinstance(entries_by_key, dict):
        raise ValueError("publication floor entries must be an object")
    entries = {}
    required_entry = set(PUBLICATION_FLOOR_SCHEMA["entry_identity"])
    for source_key in sorted(entries_by_key):
        entry = entries_by_key[source_key]
        if (not isinstance(source_key, str) or not isinstance(entry, dict)
                or set(entry) != required_entry
                or not isinstance(entry.get("first_authority_kind"), str)
                or not entry["first_authority_kind"]):
            raise ValueError("publication floor entry schema mismatch")
        for field in required_entry - {"first_authority_kind"}:
            if (not isinstance(entry.get(field), str)
                    or _SHA256_RE.fullmatch(entry[field]) is None):
                raise ValueError(f"publication floor entry {field} is malformed")
        entries[source_key] = copy.deepcopy(entry)
    body = {
        "version": PUBLICATION_FLOOR_VERSION,
        "kind": PUBLICATION_FLOOR_KIND,
        "schema": copy.deepcopy(PUBLICATION_FLOOR_SCHEMA),
        "store": store_name,
        "entries": entries,
    }
    return {**body, "floor_sha256": sha256_json(body)}


def build_empty_publication_floor_document(store_name: str) -> dict:
    return build_publication_floor_document(store_name, {})


def parse_publication_floor(raw: bytes, spec: StoreSpec) -> PublicationFloor:
    value = _strict_json_loads(raw, f"{spec.filename} publication floor")
    required = {
        "version", "kind", "schema", "store", "entries", "floor_sha256",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError(f"{spec.filename} publication floor schema mismatch")
    if publication_floor_json_bytes(value) != raw:
        raise ValueError(f"{spec.filename} publication floor bytes are noncanonical")
    if (type(value.get("version")) is not int
            or value["version"] != PUBLICATION_FLOOR_VERSION
            or value.get("kind") != PUBLICATION_FLOOR_KIND
            or value.get("schema") != PUBLICATION_FLOOR_SCHEMA
            or value.get("store") != spec.filename
            or not isinstance(value.get("entries"), dict)):
        raise ValueError(f"{spec.filename} publication floor identity mismatch")
    body = dict(value)
    claimed = body.pop("floor_sha256", None)
    if (not isinstance(claimed, str)
            or _SHA256_RE.fullmatch(claimed) is None
            or claimed != sha256_json(body)):
        raise ValueError(f"{spec.filename} publication floor self-hash mismatch")
    canonical = build_publication_floor_document(
        spec.filename, value["entries"]
    )
    if canonical != value:
        raise ValueError(f"{spec.filename} publication floor is noncanonical")
    immutable_entries = []
    for source_key, entry in value["entries"].items():
        if (spec.key_kind == OSM_KEY
                and _OSM_ID_RE.fullmatch(source_key) is None):
            raise ValueError(
                f"{spec.filename} publication floor has noncanonical OSM key "
                f"{source_key!r}"
            )
        if (spec.key_kind == FID_KEY
                and _DECIMAL_FID_RE.fullmatch(source_key) is None):
            raise ValueError(
                f"{spec.filename} publication floor has noncanonical fid key "
                f"{source_key!r}"
            )
        immutable_entries.append((source_key, tuple(sorted(entry.items()))))
    return PublicationFloor(
        spec.filename, claimed, tuple(immutable_entries)
    )


def extend_publication_floor(
        floor: PublicationFloor,
        current_entries_by_key: dict[str, dict[str, str]]) -> dict:
    """Union current keys into a floor without changing any existing entry."""
    entries = floor.entries_by_key
    for source_key, entry in current_entries_by_key.items():
        if source_key not in entries:
            entries[source_key] = copy.deepcopy(entry)
    return build_publication_floor_document(floor.store_name, entries)


def _validated_aliases(value: object, context: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{context} must be a list of canonical OSM ids")
    aliases = []
    for alias in value:
        if not isinstance(alias, str) or _OSM_ID_RE.fullmatch(alias) is None:
            raise ValueError(f"{context} contains noncanonical OSM id {alias!r}")
        if alias not in aliases:
            aliases.append(alias)
    return aliases


def validate_store_source_key(
        spec: StoreSpec, source_key: object, value: dict) -> list[str]:
    """Bind the dictionary key to the identity attested by the full row."""
    context = f"{spec.filename}:{source_key}"
    if not isinstance(source_key, str):
        raise ValueError(f"{context} source key must be a string")
    aliases = _validated_aliases(value.get("osm"), f"{context} row osm")
    if spec.key_kind == OSM_KEY:
        if _OSM_ID_RE.fullmatch(source_key) is None:
            raise ValueError(f"{context} has a noncanonical OSM source key")
        if source_key not in aliases:
            raise ValueError(
                f"{context} OSM source key is absent from the row-attested osm vector"
            )
    else:
        fid = value.get("fid")
        if (_DECIMAL_FID_RE.fullmatch(source_key) is None
                or type(fid) is not int or fid < 0 or source_key != str(fid)):
            raise ValueError(
                f"{context} legacy source key must be the canonical decimal row fid"
            )
    return aliases


def _coordinate(value: object, context: str,
                minimum: float, maximum: float) -> int | float:
    if type(value) is int:
        if value < minimum or value > maximum:
            raise ValueError(f"{context} is out of range")
        return value
    if type(value) is float:
        if not math.isfinite(value) or value < minimum or value > maximum:
            raise ValueError(f"{context} is out of range")
        return value
    raise ValueError(f"{context} is out of range")


def _validated_facility(facility: object, context: str) -> dict:
    if not isinstance(facility, dict):
        raise ValueError(f"{context} must be an object")
    result = copy.deepcopy(facility)
    _coordinate(result.get("lat"), f"{context} latitude", -90.0, 90.0)
    _coordinate(result.get("lon"), f"{context} longitude", -180.0, 180.0)
    result["osm"] = _validated_aliases(result.get("osm"), f"{context} osm")
    tags = result.get("tags_union")
    if tags is not None and not isinstance(tags, dict):
        raise ValueError(f"{context} tags_union must be an object")
    rings = result.get("rings")
    if not rings and result.get("ring"):
        rings = [result["ring"]]
    if rings is None:
        rings = []
    if not isinstance(rings, list):
        raise ValueError(f"{context} rings must be a list")
    for ring_index, ring in enumerate(rings):
        if not isinstance(ring, list) or not ring:
            raise ValueError(f"{context} ring {ring_index} must be a nonempty list")
        for point_index, point in enumerate(ring):
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError(
                    f"{context} ring {ring_index} point {point_index} must have two coordinates"
                )
            _coordinate(
                point[0],
                f"{context} ring {ring_index} point {point_index} latitude",
                -90.0, 90.0,
            )
            _coordinate(
                point[1],
                f"{context} ring {ring_index} point {point_index} longitude",
                -180.0, 180.0,
            )
    return result


def dossier_indexes_from_bytes(
        dossier_bytes_by_name: dict[str, bytes]) -> tuple[dict, dict]:
    """Parse only captured dossier bytes into fresh, validated indexes."""
    by_fid: dict[tuple[str, int], dict] = {}
    by_osm: dict[str, list[tuple[str, dict]]] = {}
    for name in sorted(dossier_bytes_by_name):
        match = _DOSSIER_NAME_RE.fullmatch(name)
        if match is None:
            raise ValueError(f"captured dossier has noncanonical name {name!r}")
        raw = dossier_bytes_by_name[name]
        if not isinstance(raw, bytes):
            raise ValueError(f"captured dossier {name} is not exact bytes")
        dossier = _strict_json_loads(raw, name)
        if not isinstance(dossier, dict):
            raise ValueError(f"{name} must contain an object")
        slug = match.group(1)
        embedded_slug = dossier.get("slug")
        if embedded_slug is not None and embedded_slug != slug:
            raise ValueError(f"{name} embedded slug does not match its filename")
        facilities = dossier.get("facilities")
        if not isinstance(facilities, list):
            raise ValueError(f"{name} facilities must be a list")
        for index, source_facility in enumerate(facilities):
            context = f"{name} facility {index}"
            if not isinstance(source_facility, dict):
                raise ValueError(f"{context} must be an object")
            fid = source_facility.get("fid")
            if type(fid) is not int or fid < 0:
                raise ValueError(f"{context} fid must be a nonnegative integer")
            key = (slug, fid)
            if key in by_fid:
                raise ValueError(f"{name} has duplicate facility fid {fid}")
            facility = _validated_facility(source_facility, context)
            facility["_slug"] = slug
            by_fid[key] = facility
            for osm_id in facility.get("osm") or []:
                by_osm.setdefault(osm_id, []).append((slug, facility))
    return by_fid, by_osm


def embedded_facility(value: dict) -> dict | None:
    if value.get("lat") is None or value.get("lon") is None:
        return None
    return {
        "lat": value["lat"], "lon": value["lon"],
        "rings": copy.deepcopy(value.get("rings") or []),
        "osm": list(value.get("osm") or []),
        "tags_union": {"name": value.get("name")} if value.get("name") else {},
        "_slug": value.get("area"),
    }


def facility_for(value: dict, dossier_slug: str | None, by_fid: dict,
                 by_osm: dict, aliases: list[str] | tuple[str, ...] | None = None) -> dict | None:
    facility = embedded_facility(value)
    if facility is not None:
        return facility
    if dossier_slug is not None and "fid" in value:
        facility = by_fid.get((dossier_slug, value["fid"]))
        if facility is not None:
            return facility
    lookup_aliases = aliases if aliases is not None else value.get("osm") or []
    for alias in lookup_aliases:
        candidates = by_osm.get(alias)
        if candidates:
            area = value.get("area")
            for slug, facility in candidates:
                if slug == area:
                    return facility
            return candidates[0][1]
    return None


def fold_signature(value: dict, effective_src: object,
                   effective_judged: object) -> dict:
    """Decision, authority, and emitted provenance that a fold may not lose."""
    fields = (
        "verdict", "prior", "exists", "public", "serves", "frames_used",
        "tags_cited", "confidence", "resolve_hint", "coverage_gap", "override",
        "human_confirmation", "trust_resolution", "publication_attestation",
        "judge_provenance",
    )
    signature = {
        field: copy.deepcopy(value.get(field)) for field in fields
    }
    signature["src"] = copy.deepcopy(effective_src)
    signature["judged"] = copy.deepcopy(effective_judged)
    return signature


def normalize_store_record(spec: StoreSpec, source_key: object, value: dict,
                           by_fid: dict, by_osm: dict) -> NormalizedStoreRecord:
    """Validate and normalize one emitted verdict row for builder and replay."""
    context = f"{spec.filename}:{source_key}"
    row_aliases = validate_store_source_key(spec, source_key, value)

    # Current authority attests the row's complete publication projection,
    # including identity and geometry. Never let mutable dossier data add an
    # alias or substitute geometry after that attestation was issued.
    is_current_publication = value.get("publication_attestation") is not None
    facility = (
        embedded_facility(value)
        if is_current_publication
        else facility_for(
            value, spec.dossier_slug, by_fid, by_osm, aliases=row_aliases
        )
    )
    if facility is None:
        if is_current_publication:
            raise ValueError(
                f"{context} current publication has no attested embedded position"
            )
        raise ValueError(f"{context} has no dossier position")
    facility = _validated_facility(facility, f"{context} facility")
    facility_aliases = (
        [] if is_current_publication else _validated_aliases(
            facility.get("osm"), f"{context} facility osm"
        )
    )
    aliases = list(row_aliases)
    for alias in facility_aliases:
        if alias not in aliases:
            aliases.append(alias)
    if not aliases:
        raise ValueError(f"{context} has no OSM identity")

    effective_src = value.get("src") or spec.filename
    effective_judged = value.get("judged") or spec.default_judged
    return NormalizedStoreRecord(
        facility=facility,
        aliases=tuple(aliases),
        effective_src=effective_src,
        effective_judged=effective_judged,
        fold_signature=fold_signature(
            value, effective_src, effective_judged
        ),
    )
