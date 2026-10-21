# n8n Cloud + FastAPI + PostgreSQL Idempotency Demo

A webhook-processing demo that combines n8n Cloud, FastAPI, and PostgreSQL to demonstrate request validation, idempotent event processing, duplicate detection, payload-conflict handling, concurrency testing, and failure handling.

## Architecture

The demo uses the following flow:

**n8n Cloud → Secure HTTPS Tunnel → FastAPI → PostgreSQL**

- **n8n Cloud:** Workflow orchestration and webhook handling.
- **FastAPI:** Request validation, API authentication, event processing, and response handling.
- **PostgreSQL:** Persistent event records and demo business-effect records.
- **Concurrency tests:** Checks duplicate handling, unique events, payload conflicts, invalid inputs, and API metrics.

### Security note

n8n Cloud cannot access your computer's `localhost` directly. This demo uses a local FastAPI bridge to connect n8n Cloud to PostgreSQL.

Never expose PostgreSQL port `5432` publicly. Expose only the API through a securely configured HTTPS tunnel with appropriate access controls, or use a hosted database and API deployment for a production environment.

Keep API keys and database credentials private. Do not commit `.env` files, secrets, or real customer data.

## Project Files

| File | Purpose |
|---|---|
| `schema.sql` | Creates the database tables for event idempotency and demo business effects. |
| `api.py` | Implements the FastAPI endpoints, request validation, and transactional event processing. |
| `concurrent_test.py` | Tests concurrent duplicate requests, unique events, conflicts, invalid payloads, and metrics. |
| `dashboard.html` | Displays event metrics and recent processing results. |
| `n8n_cloud_webhook_workflow.json` | Workflow definition to import into n8n Cloud. |
| `requirements.txt` | Python dependencies. |
| `.env.example` | Example environment configuration without real secrets. |
| `.gitignore` | Excludes environment files and other local or sensitive artifacts. |

## 1. Create the Database and Schema

Create a PostgreSQL database named `n8n_demo`, then apply `schema.sql`.

Example commands:

```bash
createdb -U postgres n8n_demo
psql -U postgres -d n8n_demo -f schema.sql
```

These commands assume PostgreSQL command-line tools are installed and accessible. You can also create the database and run the schema using pgAdmin.

Create a dedicated database user, such as `demo_user`, with only the permissions needed by the application. Avoid using the PostgreSQL superuser for routine API operations.

## 2. Install and Start the FastAPI Application

Run these commands in PowerShell from the project directory.

Create a virtual environment:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Configure the database connection and API key:

```powershell
$env:DATABASE_URL="postgresql://demo_user:YOUR_PASSWORD@127.0.0.1:5432/n8n_demo"
$env:DEMO_API_KEY="REPLACE_WITH_A_LONG_RANDOM_SECRET"
```

Replace the database password and API key with your actual local configuration. Do not publish real values.

Start the API:

```powershell
uvicorn api:app --host 127.0.0.1 --port 8000
```

Check the health endpoint locally:

```text
http://127.0.0.1:8000/health
```

Keep the API process running while testing the workflow.

## 3. Securely Connect n8n Cloud to the API

Configure a secure HTTPS tunnel to expose the local API on port `8000`.

Requirements:

- Expose only the API, not PostgreSQL port `5432`.
- Configure tunnel access controls and any required service-token authentication.
- Ensure n8n Cloud can reach the API through the configured access policy.
- Keep the `DEMO_API_KEY` secret and rotate it after the demo if appropriate.
- Use verified TLS connections and proper authentication in production.

Do not use an unauthenticated public tunnel for a production system.

The measured concurrency results in this README were obtained through a Cloudflare Quick Tunnel in the demo environment. A temporary tunnel URL may change when the tunnel restarts.

## 4. Import and Configure the n8n Workflow

In n8n Cloud:

1. Open **Workflows**.
2. Select the workflow import option.
3. Import `n8n_cloud_webhook_workflow.json`.
4. Open the `Send to Local API` node.
5. Replace the placeholder URL with your configured tunnel URL followed by `/api/events`.
6. Configure the `X-Demo-Api-Key` header using the same secret configured as `DEMO_API_KEY`.
7. Save the workflow and configure its activation and webhook URL as required.

Use the production webhook URL when testing an activated workflow. Do not publish API keys, authorization headers, or private tunnel credentials in screenshots or recordings.

## 5. Run the Concurrency Tests

Configure the test script to use your tunnel's base URL, without the `/api/events` suffix.

In PowerShell:

```powershell
$env:DEMO_API_URL="https://YOUR-TUNNEL-HOST"
$env:DEMO_API_KEY="YOUR_CONFIGURED_DEMO_API_KEY"
python concurrent_test.py
```

The test script measures results such as:

- Concurrent requests using the same event ID.
- Requests using unique event IDs.
- Conflicting payloads for an existing event ID.
- Invalid payload handling.
- Request wall time, throughput, average client latency, and p95 client latency.
- API metrics after processing.

The results depend on the local machine, database, network, tunnel, and test conditions. They should not be treated as production capacity guarantees.

## 6. Verify Database Evidence

Run these SQL queries against the `n8n_demo` database.

Count events by status:

```sql
SELECT status, COUNT(*)
FROM webhook_events
GROUP BY status;
```

Count persisted demo business effects:

```sql
SELECT COUNT(*) AS durable_business_effects
FROM business_effects;
```

Inspect recent event records:

```sql
SELECT event_id, status, duration_ms, received_at
FROM webhook_events
ORDER BY received_at DESC
LIMIT 25;
```

For a same-ID concurrency test, verify the results for the specific test event ID. The expected invariant is one event record and one business-effect record for that ID, with the remaining matching requests reported as duplicates.

Always verify the actual test output and database records before claiming a test passed.

## 7. Verified Functional Test Results

The following checks were completed in the documented demo environment.

| Test | Observed result | Status |
|---|---|---|
| Local FastAPI request with API key | HTTP 200, processed | PASS |
| n8n webhook authorization | Valid request accepted | PASS |
| n8n-to-FastAPI authentication | HTTP 200, processed | PASS |
| New event processing | `outcome: processed` | PASS |
| Same event ID and same payload | `outcome: duplicate`, HTTP 200 | PASS |
| Same event ID and changed payload | HTTP 409 Conflict | PASS |
| Backend/tunnel failure and recovery | Failure paths and subsequent recovery tested | TESTED |
| Dashboard event count versus PostgreSQL | 10 versus 10 | MATCH |
| Dashboard business effects versus PostgreSQL | 10 versus 10 | MATCH |
| Successful/failed event counts | 10/0 in both | MATCH |
| Average processing duration | 87.90 ms in both | MATCH |
| Maximum processing duration | 210 ms in both | MATCH |

### Initial Verification Snapshot

At the time of the initial verification:

- Unique event records: **10**
- Business-effect rows: **10**
- Events with `succeeded` status: **10**
- Events with `failed` status: **0**
- Average processing duration: **87.90 ms**
- Maximum processing duration: **210 ms**

These values represent the initial verification snapshot. They are not the later post-concurrency metrics and are not performance guarantees.

### Additional Test Notes

The concurrency tests and a separate controlled database-failure test were completed in the demo environment. The detailed results are documented in the following section.

The demo business effect is a PostgreSQL record. It does not represent a real external payment, order, email, or API action.

## 8. Verified Concurrency Test Results

The concurrency tests were run against the API through the active Cloudflare Quick Tunnel.

| Test | Observed result | Status |
|---|---|---|
| 100 concurrent requests with the same event ID and payload | 100 HTTP 200 responses; 1 processed, 99 duplicates | PASS |
| 100 unique event requests | 100 HTTP 200 responses; all processed | PASS |
| Existing event ID with changed payload | HTTP 409 Conflict | PASS |
| Negative amount validation | HTTP 422 | PASS |
| Metrics after tests | 112 unique event IDs; 112 business-effect rows | MATCH |

### Measured Load Results

| Metric | Same-event requests | Unique-event requests |
|---|---:|---:|
| Requests | 100 | 100 |
| Wall time | 3.461 s | 3.128 s |
| Throughput | 28.90 requests/s | 31.97 requests/s |
| Average client latency | 655.60 ms | 560.51 ms |
| p95 client latency | 1282.37 ms | 705.94 ms |

These measurements describe the specific demo environment and test run only. They do not establish production capacity, sustained-load performance, or production readiness.

### Post-Concurrency Metrics Snapshot

After the concurrency tests, the reported metrics were:

- Unique event IDs: **112**
- Business-effect rows: **112**
- Successful events: **112**
- Failed events: **0**
- Average processing duration: **109.2 ms**
- Maximum processing duration: **288 ms**

The initial verification and post-concurrency metrics are different snapshots taken at different stages of testing.

### Controlled Database-Failure Test

A separate controlled test used an isolated API instance configured with an unreachable database. The event endpoint returned HTTP `503`, as expected.

This confirms the observed response for that test scenario. It does not prove that every possible infrastructure failure is automatically recovered or that failed attempts are durably recorded.

## 9. Idempotency and Transaction Behavior

The demo is designed to demonstrate the following behaviors:

- **New event:** A previously unseen event ID is processed.
- **Duplicate event:** Repeating the same event ID with the same payload is identified as a duplicate.
- **Payload conflict:** Reusing an event ID with a different payload returns HTTP `409 Conflict`.
- **Validation error:** Invalid input, such as a negative amount, is rejected with HTTP `422`.
- **Database failure:** The tested event endpoint returns HTTP `503` when the configured database is unreachable.

The event claim and demo business-effect write use a shared database transaction. If a write fails, the transaction rolls back.

The starter implementation does not persist failed attempts in a separate audit transaction. The `attempt_count` field is schema groundwork and is not incremented by this implementation.

## 10. Limitations and Production Considerations

This repository is a technical demo, not a claim of production readiness.

- The business effect is a PostgreSQL row, not a real external payment, order, email, or API operation.
- Failed transactions roll back, so failed attempts are not durably recorded by a separate failure-audit mechanism.
- The starter implementation does not increment `attempt_count`.
- The concurrency tests do not establish sustained-load capacity or production performance.
- The demo has not established production readiness through a full security, resilience, and operational review.

Before adapting this pattern for production, consider:

- Durable ingress queues and explicit retry policies with exponential backoff.
- Dead-letter handling and persistent failure auditing.
- Strong authentication and authorization.
- Verified TLS, secure secret storage, and credential rotation.
- Rate limiting and request-size limits.
- Structured logging, monitoring, tracing, and alerting.
- Database least-privilege permissions, backups, and recovery procedures.
- Integration tests for downstream side effects and failure scenarios.
- Load tests representative of expected production traffic.

## 11. Summary

This project demonstrates a webhook-processing pattern using n8n Cloud, FastAPI, and PostgreSQL, with evidence for:

- Request validation and API authentication.
- Idempotent handling of duplicate event IDs.
- Conflict detection when an existing event ID has a changed payload.
- Concurrent request testing.
- Database-backed event and business-effect verification.
- Observed failure responses in controlled test scenarios.
- Metrics and documented implementation limitations.

The measured results are specific to the demo environment. They should not be interpreted as a production performance benchmark or a guarantee of production reliability.

**Repository:** https://github.com/katreshital11-commits/ai-n8n-automation

