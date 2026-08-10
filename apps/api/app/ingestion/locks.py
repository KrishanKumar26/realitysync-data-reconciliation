"""PostgreSQL advisory locks.

One sync per source at a time. Two concurrent syncs over the same table would
read overlapping rows and race on the high-water mark, and while the
fingerprint constraint would keep the data correct, the run counters would be
nonsense and the work would be wasted.

PostgreSQL, not Redis. The lock must be consistent with the data it protects:
an advisory lock lives in the same server as the ``observations`` table, so
there is no window where the lock says one thing and the database another. A
Redis lock could be lost — eviction, failover, a split brain — while a sync was
still running, and Redis holds nothing authoritative in this architecture by
design.

Session-level rather than transaction-level, because a sync commits in batches
and a transaction-scoped lock would be released by the first commit.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)

#: Namespace for the first lock key, keeping RealitySync's advisory locks from
#: colliding with anything else using the same mechanism in this database.
_LOCK_NAMESPACE = 0x52535953  # "RSYS"


def advisory_key(source_id: uuid.UUID) -> tuple[int, int]:
    """Map a source id onto the (int4, int4) pair advisory locks take.

    The UUID is hashed rather than truncated: taking the first bytes of a
    version-4 UUID is fine, but hashing is stable for any UUID version, which
    matters if a future source id is ever derived rather than random.
    """
    digest = hashlib.sha256(str(source_id).encode("utf-8")).digest()
    # Signed 32-bit, because that is what pg_advisory_lock accepts.
    key = int.from_bytes(digest[:4], "big", signed=True)
    return _LOCK_NAMESPACE, key


class SyncAlreadyRunningError(RuntimeError):
    """Another sync holds this source's lock."""

    def __init__(self, source_id: uuid.UUID) -> None:
        super().__init__(f"A sync is already running for source {source_id}")
        self.source_id = source_id


@asynccontextmanager
async def source_sync_lock(db: AsyncSession, source_id: uuid.UUID) -> AsyncIterator[None]:
    """Hold the source's sync lock, or raise if someone else has it.

    ``pg_try_advisory_lock`` rather than ``pg_advisory_lock``: a second sync
    request should be told "one is already running" immediately, not queue
    behind it for an unbounded time holding an HTTP connection open.
    """
    namespace, key = advisory_key(source_id)

    acquired = await db.scalar(
        text("SELECT pg_try_advisory_lock(:ns, :key)"), {"ns": namespace, "key": key}
    )
    if not acquired:
        logger.info("sync.lock_contended", source_id=str(source_id))
        raise SyncAlreadyRunningError(source_id)

    try:
        yield
    finally:
        # Session-scoped locks outlive their transaction, so this must run even
        # when the sync failed — otherwise a crashed sync would block the
        # source until the connection was returned to the pool and reset.
        await db.execute(
            text("SELECT pg_advisory_unlock(:ns, :key)"), {"ns": namespace, "key": key}
        )
