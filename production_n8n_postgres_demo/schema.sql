-- Run this after creating/selecting your demo database.
CREATE TABLE IF NOT EXISTS webhook_events (
    event_id       TEXT PRIMARY KEY,
    payload_hash   TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('processing', 'succeeded', 'failed')),
    received_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at   TIMESTAMPTZ,
    duration_ms    INTEGER,
    attempt_count  INTEGER NOT NULL DEFAULT 1,
    error_message  TEXT,
    result         JSONB
);

CREATE TABLE IF NOT EXISTS business_effects (
    event_id    TEXT PRIMARY KEY REFERENCES webhook_events(event_id),
    customer_id TEXT NOT NULL,
    amount      NUMERIC(14,2) NOT NULL CHECK (amount >= 0),
    currency    CHAR(3) NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload     JSONB NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_status ON webhook_events(status);
CREATE INDEX IF NOT EXISTS idx_webhook_events_received ON webhook_events(received_at DESC);
