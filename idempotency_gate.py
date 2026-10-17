
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

import psycopg
import redis
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg.types.json import Jsonb


# ---------------------------------------------------------
# Logging
# ---------------------------------------------------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

logger = logging.getLogger("OmniEngine-Edge")


# ---------------------------------------------------------
# Exceptions
# ---------------------------------------------------------

class PayloadConflict(Exception):
    """An event ID was reused with a different payload."""


class EventInProgress(Exception):
    """The event exists but is not yet completed."""


class IdempotencyConfigurationError(Exception):
    """Required database configuration is missing."""


# ---------------------------------------------------------
# Payload validation
# ---------------------------------------------------------

class LeadPayload(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )

    # Must uniquely identify one logical webhook event.
    # Use a provider event ID if available.
    call_sid: str = Field(min_length=1, max_length=255)
    phone: str
    intent: str | None = Field(default=None, max_length=100)

    @field_validator("call_sid")
    @classmethod
    def validate_event_id(cls, value: str) -> str:
        if not value or any(ord(ch) < 32 for ch in value):
            raise ValueError("call_sid contains invalid characters")
        return value

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        if not re.fullmatch(r"\+[1-9]\d{1,14}", value):
            raise ValueError(
                "phone must use E.164 format, e.g. +14155552671"
            )
        return value


# ---------------------------------------------------------
# Result
# ---------------------------------------------------------

@dataclass(frozen=True)
class ProcessResult:
    status: str
    event_id: str
    result: dict[str, Any] | None = None


# ---------------------------------------------------------
# Canonical payload hash
# ---------------------------------------------------------

def payload_fingerprint(payload: LeadPayload) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------
# PostgreSQL schema
# Run schema.sql separately during deployment.
# ---------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS webhook_events (
    event_id TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'processing'
        CHECK (status IN ('processing', 'completed')),
    attempts INTEGER NOT NULL DEFAULT 1,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lead_effects (
    event_id TEXT PRIMARY KEY
        REFERENCES webhook_events(event_id),
    phone TEXT NOT NULL,
    intent TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def initialize_schema(database_url: str) -> None:
    """Convenience for local development; deploy migrations separately."""
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
    logger.info("PostgreSQL idempotency schema initialized")


# ---------------------------------------------------------
# Optional Redis optimization
# Redis is NOT the authoritative idempotency store.
# ---------------------------------------------------------

class RedisIdempotencyGate:
    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: str | None = None,
        ttl_seconds: int = 60,
        socket_timeout: float = 1.5,
    ):
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be positive")

        self.ttl_seconds = ttl_seconds
        self.redis_client = redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password,
            decode_responses=True,
            socket_connect_timeout=socket_timeout,
            socket_timeout=socket_timeout,
            health_check_interval=30,
        )

    @staticmethod
    def _key(event_id: str) -> str:
        # Hash avoids unsafe/arbitrary event IDs in Redis key names.
        digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
        return f"omni:idempotency:{digest}"

    def claim(self, event_id: str) -> bool | None:
        """
        True: Redis claim acquired.
        False: claim already exists.
        None: Redis unavailable; caller should fall back to PostgreSQL.
        """
        try:
            return bool(
                self.redis_client.set(
                    self._key(event_id),
                    "claimed",
                    nx=True,
                    ex=self.ttl_seconds,
                )
            )
        except redis.RedisError:
            logger.warning(
                "Redis unavailable; falling back to PostgreSQL",
                exc_info=True,
            )
            return None

    def release(self, event_id: str) -> None:
        """
        Release is only an optimization.
        PostgreSQL remains responsible for correctness.
        """
        try:
            self.redis_client.delete(self._key(event_id))
        except redis.RedisError:
            logger.warning(
                "Redis release failed; TTL will expire the claim",
                exc_info=True,
            )


# ---------------------------------------------------------
# PostgreSQL authoritative idempotency
# ---------------------------------------------------------

class PostgresIdempotencyGate:
    def __init__(self, database_url: str):
        if not database_url:
            raise IdempotencyConfigurationError(
                "DATABASE_URL environment variable is required"
            )
        self.database_url = database_url

    def process(
        self,
        payload: LeadPayload,
    ) -> ProcessResult:
        """
        Atomically:
          1. Reserve the event ID.
          2. Validate duplicate payload identity.
          3. Apply the database business effect.
          4. Mark the event completed.

        All four steps commit or roll back together.

        Never perform external HTTP/API calls inside this transaction.
        Use a transactional outbox for external side effects.
        """
        event_id = payload.call_sid
        fingerprint = payload_fingerprint(payload)

        with psycopg.connect(
            self.database_url,
            connect_timeout=5,
        ) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO webhook_events
                            (event_id, payload_hash, status)
                        VALUES (%s, %s, 'processing')
                        ON CONFLICT (event_id) DO NOTHING
                        RETURNING event_id
                        """,
                        (event_id, fingerprint),
                    )
                    inserted = cur.fetchone() is not None

                    if not inserted:
                        cur.execute(
                            """
                            SELECT payload_hash, status, result
                            FROM webhook_events
                            WHERE event_id = %s
                            FOR UPDATE
                            """,
                            (event_id,),
                        )
                        row = cur.fetchone()

                        if row is None:
                            # Defensive handling for unusual concurrent
                            # deletion or administrative intervention.
                            raise RuntimeError(
                                "Event record disappeared during claim"
                            )

                        stored_hash, status, stored_result = row

                        if stored_hash != fingerprint:
                            raise PayloadConflict(
                                "Same event ID received with a different "
                                "payload; investigate provider/event mapping"
                            )

                        if status == "completed":
                            logger.info(
                                "Duplicate event ignored: %s",
                                event_id,
                            )
                            return ProcessResult(
                                status="duplicate",
                                event_id=event_id,
                                result=stored_result,
                            )

                        # The normal implementation commits 'processing'
                        # and 'completed' together, so this state should
                        # not normally be visible. Do not silently re-run it.
                        raise EventInProgress(
                            f"Event {event_id!r} is not completed"
                        )

                    # Database business operation uses this transaction.
                    cur.execute(
                        """
                        INSERT INTO lead_effects
                            (event_id, phone, intent)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (event_id) DO NOTHING
                        """,
                        (event_id, payload.phone, payload.intent),
                    )

                    result = {
                        "event_id": event_id,
                        "saved": True,
                    }

                    cur.execute(
                        """
                        UPDATE webhook_events
                        SET status = 'completed',
                            result = %s,
                            updated_at = NOW()
                        WHERE event_id = %s
                        """,
                        (Jsonb(result), event_id),
                    )

                    logger.info("Event committed: %s", event_id)

                    return ProcessResult(
                        status="processed",
                        event_id=event_id,
                        result=result,
                    )


# ---------------------------------------------------------
# Combined edge
# ---------------------------------------------------------

class OmniEngineIdempotency:
    def __init__(
        self,
        database_url: str,
        redis_gate: RedisIdempotencyGate | None = None,
    ):
        self.postgres_gate = PostgresIdempotencyGate(database_url)
        self.redis_gate = redis_gate

    def process(self, raw_payload: dict[str, Any]) -> ProcessResult:
        # Validate before accessing either datastore.
        payload = LeadPayload.model_validate(raw_payload)

        # Redis is only an optimization. Do NOT reject an event solely
        # because a Redis claim already exists: it could be a stale claim
        # from a request whose PostgreSQL transaction never committed.
        if self.redis_gate is not None:
            self.redis_gate.claim(payload.call_sid)

        try:
            # PostgreSQL determines whether the event is new or duplicate.
            return self.postgres_gate.process(payload)
        finally:
            if self.redis_gate is not None:
                self.redis_gate.release(payload.call_sid)


# ---------------------------------------------------------
# Local smoke test
# ---------------------------------------------------------

if __name__ == "__main__":
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise SystemExit(
            "Set DATABASE_URL to your PostgreSQL test database first."
        )

    # Development convenience only. Production should use migrations.
    initialize_schema(database_url)

    redis_gate = None
    if os.getenv("REDIS_ENABLED", "false").lower() == "true":
        redis_gate = RedisIdempotencyGate(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD") or None,
            ttl_seconds=60,
        )

    engine = OmniEngineIdempotency(
        database_url=database_url,
        redis_gate=redis_gate,
    )

    # Synthetic example; never commit real customer phone numbers.
    sample = {
        "call_sid": "test-event-001",
        "phone": "+14155552671",
        "intent": "site_visit",
    }

    first = engine.process(sample)
    second = engine.process(sample)

    logger.info("First request: %s", first)
    logger.info("Retry request: %s", second)
