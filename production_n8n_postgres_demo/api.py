
"""
Local PostgreSQL bridge for an n8n Cloud demo.

Responsibilities:
- Load configuration from .env
- Authenticate requests with X-Demo-Api-Key
- Atomically claim event IDs
- Create one durable business effect per unique event
- Return metrics from PostgreSQL
"""

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import psycopg
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field


# Load .env from the same directory as this api.py file.
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DEMO_API_KEY = os.environ.get("DEMO_API_KEY", "").strip()

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is missing. Check the .env file beside api.py."
    )

if not DEMO_API_KEY:
    raise RuntimeError(
        "DEMO_API_KEY is missing. Check the .env file beside api.py."
    )

app = FastAPI(
    title="n8n PostgreSQL Idempotency Demo",
    version="1.0.0",
)


class EventPayload(BaseModel):
    event_id: str = Field(min_length=1, max_length=200)
    customer_id: str = Field(min_length=1, max_length=200)
    amount: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)
    metadata: dict[str, Any] = Field(default_factory=dict)


def canonical_hash(payload: dict) -> str:
    """Generate a stable hash for equivalent JSON payloads."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verify_api_key(supplied_key: str | None) -> None:
    """Reject requests without the configured demo API key."""
    if not supplied_key or supplied_key != DEMO_API_KEY:
        raise HTTPException(
            status_code=401,
            detail="invalid demo API key",
        )


@app.get("/health")
def health():
    """Check whether the API can connect to PostgreSQL."""
    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
            conn.execute("SELECT 1")

        return {
            "ok": True,
            "database": "reachable",
        }

    except Exception as exc:
        # Keep credentials and database error details out of the response.
        print(f"Health check failed: {type(exc).__name__}: {exc}")
        raise HTTPException(
            status_code=503,
            detail="database unavailable",
        )


@app.post("/api/events")
def process_event(
    payload: EventPayload,
    x_demo_api_key: str | None = Header(default=None),
):
    verify_api_key(x_demo_api_key)

    data = payload.model_dump()
    digest = canonical_hash(data)
    started = time.perf_counter()

    try:
        # Claim event, create the business effect, and mark success
        # in one database transaction.
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
            with conn.transaction():
                inserted = conn.execute(
                    """
                    INSERT INTO webhook_events
                        (event_id, payload_hash, status)
                    VALUES (%s, %s, 'processing')
                    ON CONFLICT (event_id) DO NOTHING
                    RETURNING event_id
                    """,
                    (payload.event_id, digest),
                ).fetchone()

                if not inserted:
                    existing = conn.execute(
                        """
                        SELECT payload_hash, status
                        FROM webhook_events
                        WHERE event_id = %s
                        """,
                        (payload.event_id,),
                    ).fetchone()

                    if existing is None:
                        raise HTTPException(
                            status_code=503,
                            detail="event claim raced; retry safely",
                        )

                    old_hash, old_status = existing

                    if old_hash != digest:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                "event_id already exists with "
                                "a different payload"
                            ),
                        )

                    elapsed = round(
                        (time.perf_counter() - started) * 1000
                    )

                    return {
                        "outcome": "duplicate",
                        "event_id": payload.event_id,
                        "existing_status": old_status,
                        "duration_ms": elapsed,
                    }

                # Demo business effect: one durable row per event ID.
                conn.execute(
                    """
                    INSERT INTO business_effects
                        (event_id, customer_id, amount, currency, payload)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        payload.event_id,
                        payload.customer_id,
                        payload.amount,
                        payload.currency,
                        Jsonb(data),
                    ),
                )

                elapsed = round(
                    (time.perf_counter() - started) * 1000
                )

                result = {
                    "effect": "created",
                    "event_id": payload.event_id,
                }

                conn.execute(
                    """
                    UPDATE webhook_events
                    SET status = 'succeeded',
                        processed_at = NOW(),
                        duration_ms = %s,
                        result = %s
                    WHERE event_id = %s
                    """,
                    (
                        elapsed,
                        Jsonb(result),
                        payload.event_id,
                    ),
                )

        return {
            "outcome": "processed",
            "event_id": payload.event_id,
            "duration_ms": elapsed,
        }

    except HTTPException:
        raise

    except Exception as exc:
        # The transaction rolls back both the event claim and effect
        # if any write fails. Never return false success.
        print(
            f"Event processing failed for {payload.event_id}: "
            f"{type(exc).__name__}: {exc}"
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "temporary processing failure; "
                "retry with the same event_id"
            ),
        )


@app.get("/api/metrics")
def metrics(
    x_demo_api_key: str | None = Header(default=None),
):
    verify_api_key(x_demo_api_key)

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
            rows = conn.execute(
                """
                SELECT
                    status,
                    COUNT(*) AS events,
                    ROUND(AVG(duration_ms)::numeric, 2) AS avg_ms,
                    MAX(duration_ms) AS max_ms
                FROM webhook_events
                GROUP BY status
                ORDER BY status
                """
            ).fetchall()

            effects = conn.execute(
                "SELECT COUNT(*) FROM business_effects"
            ).fetchone()[0]

            total = conn.execute(
                "SELECT COUNT(*) FROM webhook_events"
            ).fetchone()[0]

        return {
            "total_unique_event_ids": total,
            "business_effects": effects,
            "by_status": [
                {
                    "status": row[0],
                    "events": row[1],
                    "avg_ms": (
                        float(row[2]) if row[2] is not None else None
                    ),
                    "max_ms": row[3],
                }
                for row in rows
            ],
        }

    except Exception as exc:
        print(f"Metrics query failed: {type(exc).__name__}: {exc}")
        raise HTTPException(
            status_code=503,
            detail="metrics temporarily unavailable",
        )



@app.get("/dashboard", include_in_schema=False)
def dashboard():
    """Serve the local monitoring dashboard."""
    from fastapi.responses import FileResponse

    dashboard_path = Path(__file__).resolve().parent / "dashboard.html"
    if not dashboard_path.is_file():
        raise HTTPException(status_code=404, detail="Dashboard file not found")

    return FileResponse(str(dashboard_path), media_type="text/html")


@app.get("/api/events/recent")
def recent_events(
    x_demo_api_key: str | None = Header(default=None),
):
    """Return the latest 50 events for the dashboard."""
    verify_api_key(x_demo_api_key)

    try:
        with psycopg.connect(DATABASE_URL, connect_timeout=5) as conn:
            rows = conn.execute(
                """
                SELECT event_id, status, received_at, error_message
                FROM webhook_events
                ORDER BY received_at DESC
                LIMIT 50
                """
            ).fetchall()

        return {
            "events": [
                {
                    "event_id": row[0],
                    "status": row[1],
                    "received_at": row[2],
                    "error_message": row[3],
                }
                for row in rows
            ]
        }
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Could not load recent events",
        )
