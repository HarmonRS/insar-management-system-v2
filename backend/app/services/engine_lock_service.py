from __future__ import annotations

import asyncio
import hashlib
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from sqlalchemy import text

from .. import database


def _resource_lock_key(resource_name: str) -> int:
    normalized = str(resource_name or "").strip().lower().encode("utf-8")
    digest = hashlib.sha256(normalized).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


class EngineLockService:
    async def _ensure_engine(self):
        if database.engine is None:
            database.init_db()
        if database.engine is None:
            raise RuntimeError("Database engine is not initialized.")
        return database.engine

    @asynccontextmanager
    async def acquire(
        self,
        resource_name: str,
        *,
        poll_interval: float = 2.0,
        timeout_seconds: Optional[float] = None,
        cancel_event: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[None]:
        engine = await self._ensure_engine()
        lock_key = _resource_lock_key(resource_name)
        started = time.monotonic()
        conn = await engine.connect()

        try:
            acquired = False
            while not acquired:
                if cancel_event is not None and cancel_event.is_set():
                    raise RuntimeError(f"Lock acquisition cancelled: {resource_name}")

                result = await conn.execute(
                    text("SELECT pg_try_advisory_lock(:lock_key)"),
                    {"lock_key": lock_key},
                )
                acquired = bool(result.scalar())
                if acquired:
                    break

                if timeout_seconds is not None and (time.monotonic() - started) >= float(timeout_seconds):
                    raise TimeoutError(f"Timeout waiting for engine resource lock: {resource_name}")

                await asyncio.sleep(max(0.5, float(poll_interval)))

            try:
                yield
            finally:
                await conn.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": lock_key},
                )
        finally:
            await conn.close()


engine_lock_service = EngineLockService()
