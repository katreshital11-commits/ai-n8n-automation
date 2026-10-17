"""PostgreSQL-backed idempotency gate for webhook-driven lead processing.

Correctness guarantees:
- PostgreSQL's PRIMARY KEY on event_id arbitrates concurrent deliveries.
- The idempotency row, database business effect, and completed result commit
  in one transaction. If any step fails, PostgreSQL rolls the whole transaction
  back, allowing a later delivery to retry safely.
- A reused event ID with a different validated payload is rejected.

Important:
- `call_sid` must identify one logical webhook event. If the provider offers a
  dedicated webhook/event ID, use that instead and rename the field accordingly.
- This example writes a database-only effect to `lead_effects`. Do not perform
  external HTTP/API side effects inside this transaction. For CRM, SMS, or other
  external work, write a transactional outbox row in this transaction and have a
  separate worker deliver it with downstream idempotency and retry handling.
- Run schema changes as reviewed/versioned migrations in production. The
  initialize_schema() helper is intended for local development and smoke tests.

Environment:
    DATABASE_URL=postgresql://user:password@localhost:5432/database
    LOG_LEVEL=INFO  # optional

Dependencies:
    pip install "psycopg[binary]>=3.1,<4" "pydantic>=2.5,<3"
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import psycopg
from pydantic import BaseModel, ConfigDict, Field, field_validator
from psycopg.types.json import Jsonb


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("idempotency_gate")


# ---------------------------------------------------------------------------
# Exceptions and return type
# ---------------------------------------------------------------------------

class PayloadConflict(Exception):
    """Raised when an event ID is reused with a different payload."""


class IdempotencyConfigurationError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class ProcessResult:
    """Result of processing one webhook delivery."""

    status: str  # "processed" or "duplicate"
    event_id: str
    result: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class LeadPayload(BaseModel):
    """Fields used by this example. Unknown fields are rejected deliberately."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    # Prefer the provider's unique event ID if one is available.
    # CallSid is appropriate only when one CallSid maps to one logical event.
    call_sid: str = Field(min_length=1, max_length=255)
    phone: str = Field(min_length=3, max_length=16)
    intent: str | None = Field(default=None, max_length=100)

    @field_validator("call_sid")
    @classmethod
    def validate_event_id(cls, value: str) -> str:
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("call_sid contains invalid control characters")
        return value

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        # E.164: '+' followed by 2-15 digits; first digit cannot be zero.
        if not re.fullmatch(r"\+[1-9]\d{1,14}", value):
            raise ValueError("phone must use E.164 format, e.g. +14155552671")
        return value

    @field_validator("intent")
    @classmethod
    def normalize_intent(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None


# ---------------------------------------------------------------------------
# Stable payload fingerprint
# ---------------------------------------------------------------------------

def payload_fingerprint(payload: LeadPayload) -> str:
    """Return a deterministic SHA-256 fingerprint of the validated payload."""
    canonical_json = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# PostgreSQL schema
# ---------------------------------------------------------------------------

# Keep statements separate for compatibility with psycopg's parameterized /-
# prepared execution behavior. Use versioned migrations for deployed systems.
SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS webhook_events (
        event_id TEXT PRIMARY KEY,
        payload_hash CHAR(64) NOT NULL,
        status TEXT NOT NULL
            CHECK (status IN ('processing', 'completed')),
        attempts INTEGER NOT NULL DEFAULT 1 CHECK (attempts >= 1),
        result JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CHECK (
            (status = 'processing' AND result IS NULL)
            OR status = 'completed'
        )
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS lead_effects (
        event_id TEXT PRIMARY KEY
            REFERENCES webhook_events(event_id),
        phone TEXT NOT NULL,
        intent TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
)


def initialize_schema(database_url: str) -> None:
    """Create the example tables. Prefer migrations outside local development."""
    if not database_url or not database_url.strip():
        raise IdempotencyConfigurationError("DATABASE_URL is required")

    with psycopg.connect(database_url, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            for statement in SCHEMA_STATEMENTS:
                cur.execute(statement)
    logger.info("Idempotency schema initialized")


# ---------------------------------------------------------------------------
# Idempotency gate
# ---------------------------------------------------------------------------

class PostgresIdempotencyGate:
    """Use PostgreSQL as the authoritative idempotency store."""

    def __init__(self, database_url: str, *, connect_timeout: int = 5) -> None:
        if not database_url or not database_url.strip():
            raise IdempotencyConfigurationError("DATABASE_URL is required")
        if connect_timeout < 1:
            raise ValueError("connect_timeout must be at least 1 second")
        self.database_url = database_url
        self.connect_timeout = connect_timeout

    def process(self, payload: LeadPayload) -> ProcessResult:
        """Process a validated payload exactly once for DB effects.

        Concurrent duplicate requests serialize on the unique event_id key.
        The event row, lead_effects row, and completed result are committed
        atomically. Exceptions cause the transaction to roll back.
        """
        event_id = payload.call_sid
        fingerprint = payload_fingerprint(payload)

        with psycopg.connect(
            self.database_url,
            connect_timeout=self.connect_timeout,
        ) as conn:
            with conn.transaction():
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO webhook_events
                            (event_id, payload_hash, status, attempts)
                        VALUES (%s, %s, 'processing', 1)
                        ON CONFLICT (event_id) DO NOTHING
                        RETURNING event_id
                        """,
                        (event_id, fingerprint),
                    )
                    inserted = cur.fetchone() is not None

                    if not inserted:
                        # A conflicting INSERT waits for the concurrent
                        # transaction. Under READ COMMITTED, this SELECT sees
                        # the committed row and locks it for this transaction.
                        cur.execute(
                            """
                            SELECT payload_hash, status, result
                            FROM webhook_events
                            WHERE event_id = %s
                            FOR UPDATE
                            """,
                            (event_id,),
                        )
                        existing = cur.fetchone()
                        if existing is None:
                            # Fail closed if an administrator concurrently
                            # deleted the event row.
                            raise RuntimeError(
                                "Idempotency event row disappeared during processing"
                            )

                        stored_hash, status, stored_result = existing
                        if stored_hash.strip() != fingerprint:
                            logger.warning(
                                "Payload conflict detected for event_id=%s",
                                event_id,
                            )
                            raise PayloadConflict(
                                "The same event ID was received with a different payload"
                            )

                        if status == "completed":
                            cur.execute(
                                """
                                UPDATE webhook_events
                                SET attempts = attempts + 1,
                                    updated_at = NOW()
                                WHERE event_id = %s
                                """,
                                (event_id,),
                            )
                            logger.info("Duplicate event ignored: %s", event_id)
                            return ProcessResult(
                                status="duplicate",
                                event_id=event_id,
                                result=stored_result,
                            )

                        # This implementation creates and completes the event
                        # in one transaction. A committed processing row means
                        # data was left by an older/different implementation;
                        # do not repeat work without an explicit recovery policy.
                        raise RuntimeError(
                            f"Event {event_id!r} has unexpected status {status!r}; "
                            "manual recovery or a migration is required"
                        )

                    # Example database-only business effect. Replace/add your
                    # actual database writes here, in this same transaction.
                    cur.execute(
                        """
                        INSERT INTO lead_effects (event_id, phone, intent)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (event_id) DO NOTHING
                        """,
                        (event_id, payload.phone, payload.intent),
                    )

                    result: dict[str, Any] = {
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

                    logger.info("Webhook event committed: %s", event_id)
                    return ProcessResult(
                        status="processed",
                        event_id=event_id,
                        result=result,
                    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def process_webhook(raw_payload: dict[str, Any]) -> ProcessResult:
    """Validate and process one webhook payload.

    Raises:
        pydantic.ValidationError: input validation failed.
        IdempotencyConfigurationError: DATABASE_URL is missing.
        PayloadConflict: event ID was reused with a different payload.
        psycopg.Error: PostgreSQL operation failed; caller must not ACK success.
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise IdempotencyConfigurationError(
            "Set DATABASE_URL before processing webhooks"
        )

    payload = LeadPayload.model_validate(raw_payload)
    return PostgresIdempotencyGate(database_url).process(payload)


# ---------------------------------------------------------------------------
# Local smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_database_url = os.getenv("DATABASE_URL")
    if not test_database_url:
        raise SystemExit(
            "Set DATABASE_URL to a dedicated TEST PostgreSQL database first."
        )

    initialize_schema(test_database_url)
    sample_payload = {
        "call_sid": "local-smoke-test-event-001",
        "phone": "+14155552671",
        "intent": "site_visit",
    }

    first = process_webhook(sample_payload)
    second = process_webhook(sample_payload)
    print("First delivery:", first)
    print("Second delivery:", second)

    if first.status != "processed" or second.status != "duplicate":
        raise SystemExit("Smoke test failed: expected processed then duplicate")
    print("Smoke test passed")
