"""Deterministic observation fingerprints.

The fingerprint is what makes ingestion idempotent. Re-reading an unchanged row
produces the same value, the unique index on ``(stream_id, fingerprint)``
rejects the duplicate, and the row is counted as skipped.

What goes in, and why:

``source_id`` / ``stream_id``
    Same row read through two different streams is two different observations.
    They came from different configurations and may carry different columns.

``external_id``
    Which row in the source table this is about.

``event_time`` and ``event_time_semantics``
    The same values asserted about a different instant is a different
    statement. Semantics is included because ``recorded`` and ``observed``
    times mean different things, so the same instant under different semantics
    is a different claim.

``payload``
    The values themselves.

What stays out, and why it matters more:

``ingested_at``
    Including it would give every read a fresh fingerprint, and *every sync
    would duplicate every row*. This is the single most important exclusion.

``sync_run_id``, connector version, wall-clock
    Same reasoning. Nothing about *when or how we looked* may affect the
    identity of *what we saw*. All of it is recorded in provenance instead,
    where it is visible without being load-bearing.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

#: Bumping this changes every fingerprint, so a change here is a decision to
#: re-ingest the world. It exists so that if the canonical form ever must
#: change, the break is explicit and versioned rather than silent.
FINGERPRINT_VERSION = 1


def canonical_json(payload: Any) -> str:
    """Serialise deterministically.

    ``sort_keys`` fixes key order, ``separators`` removes incidental
    whitespace, and ``ensure_ascii`` avoids any dependence on the platform's
    Unicode handling.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def compute_fingerprint(
    *,
    source_id: uuid.UUID | str,
    stream_id: uuid.UUID | str,
    external_id: str,
    event_time: datetime,
    event_time_semantics: str,
    payload: dict[str, Any],
) -> str:
    """Return the hex SHA-256 fingerprint for an observation.

    `payload` must already be normalised — see
    :func:`app.ingestion.normalization.normalize_row`. Passing raw driver
    values would make the fingerprint depend on Python object identity and
    repr, which is exactly the non-determinism this guards against.
    """
    document = {
        "v": FINGERPRINT_VERSION,
        "source_id": str(source_id),
        "stream_id": str(stream_id),
        "external_id": external_id,
        "event_time": event_time.isoformat(),
        "event_time_semantics": event_time_semantics,
        "payload": payload,
    }
    return hashlib.sha256(canonical_json(document).encode("utf-8")).hexdigest()
