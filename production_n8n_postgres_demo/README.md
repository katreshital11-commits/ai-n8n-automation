# n8n Cloud + Local PostgreSQL idempotency demo

## Architecture note
n8n Cloud cannot reach your computer's `localhost` directly. Do NOT expose PostgreSQL port 5432 publicly.
This pack uses a small local FastAPI bridge that talks to PostgreSQL. Expose the HTTPS API only through a secure tunnel with access controls, or preferably use a hosted PostgreSQL service. Keep `X-Demo-Api-Key` enabled.

## Files
- `schema.sql`: tables for durable event idempotency and a demo business effect.
- `api.py`: local API that atomically claims event IDs and writes the business effect in one transaction.
- `concurrent_test.py`: concurrent duplicate, unique load, conflict, invalid-payload, and metrics tests.
- `n8n_cloud_webhook_workflow.json`: import into n8n Cloud; after import set the HTTP Request URL and API key header.

## 1. Create database and schema
In pgAdmin or psql, create database `n8n_demo`, then run `schema.sql` against it.

Example psql:
  createdb -U postgres n8n_demo
  psql -U postgres -d n8n_demo -f schema.sql

## 2. Start the local API (Windows PowerShell)
  py -m venv .venv
  .\.venv\Scripts\Activate.ps1
  pip install -r requirements.txt
  $env:DATABASE_URL="postgresql://demo_user:YOUR_PASSWORD@127.0.0.1:5432/n8n_demo"
  $env:DEMO_API_KEY="replace-with-a-long-random-secret"
  uvicorn api:app --host 127.0.0.1 --port 8000

Create `demo_user` with least privilege rather than using the postgres superuser. Grant it access to the two demo tables.

Check locally: http://127.0.0.1:8000/health

## 3. Securely expose the API to n8n Cloud
Use a secure HTTPS tunnel with access controls (e.g. Cloudflare Tunnel + Cloudflare Access or an authenticated tunnel provider). Expose only local port 8000; never expose port 5432. Configure the access policy so n8n Cloud can reach the API. If the tunnel provider requires an extra Access service token, configure it in the n8n HTTP Request node too. Use a random API key and rotate it after the demo.
Do not use an unauthenticated public tunnel for a production system.

## 4. Import n8n workflow
In n8n Cloud: Workflows → menu → Import from File → choose `n8n_cloud_webhook_workflow.json`.
Open node `Send to Local API`:
- URL: replace `https://YOUR-TUNNEL-HOST/api/events` with your secure tunnel URL + `/api/events`.
- Header `X-Demo-Api-Key`: set the same value as `DEMO_API_KEY`.
Save and activate the workflow. Copy the Production URL from the Webhook node. The workflow is set up to call the local API and return its JSON response.

## 5. Test API concurrency
Set `DEMO_API_URL` to your tunnel base URL (without `/api/events`) and `DEMO_API_KEY` to the same secret. Then run:
  python concurrent_test.py

The test prints measured wall time, requests/second, average latency and p95 latency. Results are from your actual environment; they will vary.

## 6. Verify database evidence
  SELECT status, COUNT(*) FROM webhook_events GROUP BY status;
  SELECT COUNT(*) AS durable_business_effects FROM business_effects;
  SELECT event_id, status, duration_ms, received_at FROM webhook_events ORDER BY received_at DESC LIMIT 25;

For the 100-request same-ID test, expected invariant: exactly one row in `webhook_events` and one row in `business_effects` for that ID; other responses should be `duplicate`. Check the printed results and database—do not claim the test passed until you run it.

## Limitations / production notes
- This demo's "business effect" is a PostgreSQL row, not a real payment/order/email/API action.
- The event claim and business effect share a transaction. If any write fails, both roll back.
- The example returns HTTP 503 for transient processing failures but does not persist failed attempts because the transaction rolls back. For production, add a separate failure/audit transaction or durable ingress queue, retry policy with backoff, dead-letter handling, authentication/authorization, TLS verification, secrets management, rate limits, and observability.
- `attempt_count` is schema groundwork and is not incremented by this starter implementation.
- Do not put real customer data or production credentials in the demo.

## 7. Verified Test Results

The following checks were completed in the current demo environment.

| Test | Observed result | Status |
|---|---|---|
| Local FastAPI request with API key | HTTP 200, processed | PASS |
| n8n webhook authorization | Valid request accepted | PASS |
| n8n to FastAPI authentication | HTTP 200, processed | PASS |
| New event processing | `outcome: processed` | PASS |
| Same event ID and same payload | `outcome: duplicate`, HTTP 200 | PASS |
| Same event ID and changed payload | HTTP 409 Conflict | PASS |
| Backend/tunnel failure and recovery | Failure paths and subsequent recovery tested | TESTED |
| Dashboard event count vs PostgreSQL | 10 vs 10 | MATCH |
| Dashboard business effects vs PostgreSQL | 10 vs 10 | MATCH |
| Successful / failed event counts | 10 / 0 in both | MATCH |
| Average processing duration | 87.90 ms in both | MATCH |
| Maximum processing duration | 210 ms in both | MATCH |

### Current Database Snapshot

At the time of verification:
- Unique event records: 10
- Business-effect rows: 10
- Events with `succeeded` status: 10
- Events with `failed` status: 0
- Average processing duration: 87.90 ms
- Maximum processing duration: 210 ms

These are environment-specific snapshot results, not performance guarantees.

### Tests Not Yet Confirmed

The 100-request concurrency test has been completed. See Section 8 for the measured results.

The business effect in this demo is a PostgreSQL record; it does not represent a real external payment, order, email, or API action.

## 8. Verified Concurrency Test Results

The concurrency test was run against the API through the active Cloudflare Quick Tunnel.

| Test | Observed result | Status |
|---|---|---|
| 100 concurrent requests with the same event ID and payload | 100 HTTP 200; 1 processed, 99 duplicates | PASS |
| 100 unique event requests | 100 HTTP 200; all processed | PASS |
| Existing event ID with changed payload | HTTP 409 Conflict | PASS |
| Negative amount validation | HTTP 422 | PASS |
| Metrics after tests | 112 unique event IDs; 112 business effects | MATCH |

### Measured Load Results

| Metric | Same-event requests | Unique-event requests |
|---|---:|---:|
| Requests | 100 | 100 |
| Wall time | 3.461 s | 3.128 s |
| Throughput | 28.90 req/s | 31.97 req/s |
| Average client latency | 655.60 ms | 560.51 ms |
| p95 client latency | 1282.37 ms | 705.94 ms |

These measurements describe this test environment only. They are not production capacity or performance guarantees.

The metrics snapshot after the run reported 112 unique event IDs, 112 business-effect rows, 112 succeeded events, 0 failed events, 109.2 ms average processing duration, and 288 ms maximum processing duration.

The business effect is a PostgreSQL row, not a real external action. The load test does not establish production readiness or test sustained load. A separate controlled database-failure test was completed using an isolated API instance with an unreachable database; the event endpoint returned HTTP 503 as expected.
