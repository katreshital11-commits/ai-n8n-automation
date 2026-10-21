"""
Concurrent test client for the local API exposed securely to n8n Cloud.
Install: pip install requests
Run: set DEMO_API_URL and DEMO_API_KEY, then python concurrent_test.py
Windows PowerShell:
  $env:DEMO_API_URL="https://YOUR-TUNNEL-HOST"
  $env:DEMO_API_KEY="same-secret-as-api"
  python concurrent_test.py
"""
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

BASE = os.environ["DEMO_API_URL"].rstrip("/")
KEY = os.environ["DEMO_API_KEY"]
HEADERS = {"X-Demo-Api-Key": KEY}
CONCURRENCY = int(os.getenv("CONCURRENCY", "20"))

def send(payload):
    start = time.perf_counter()
    try:
        r = requests.post(f"{BASE}/api/events", json=payload, headers=HEADERS, timeout=30)
        return r.status_code, r.json(), (time.perf_counter() - start) * 1000
    except Exception as exc:
        return 0, {"error": str(exc)}, (time.perf_counter() - start) * 1000

def event(event_id, amount=42.50):
    return {"event_id": event_id, "customer_id": "cust-demo-001",
            "amount": amount, "currency": "USD", "metadata": {"source": "load-test"}}

def run_batch(label, payloads):
    started = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = [pool.submit(send, p) for p in payloads]
        for f in as_completed(futures):
            results.append(f.result())
    elapsed = time.perf_counter() - started
    statuses, outcomes, latencies = {}, {}, []
    for code, body, ms in results:
        statuses[code] = statuses.get(code, 0) + 1
        outcome = body.get("outcome", body.get("detail", body.get("error", "unknown")))
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        latencies.append(ms)
    latencies.sort()
    p95 = latencies[min(len(latencies)-1, int(0.95 * len(latencies)))] if latencies else 0
    print(f"\n{label}")
    print({"requests": len(results), "wall_seconds": round(elapsed, 3),
           "requests_per_second": round(len(results)/elapsed, 2) if elapsed else None,
           "http_statuses": statuses, "outcomes": outcomes,
           "latency_avg_ms": round(sum(latencies)/len(latencies), 2) if latencies else None,
           "latency_p95_ms": round(p95, 2)})
    return results

if __name__ == "__main__":
    # 1) Concurrent duplicate race: one ID, identical payload, many requests.
    duplicate_id = "race-" + str(uuid.uuid4())
    run_batch("Concurrent same-event test", [event(duplicate_id) for _ in range(100)])

    # 2) Unique event load: 100 distinct IDs.
    run_batch("Unique event load", [event("unique-" + str(uuid.uuid4())) for _ in range(100)])

    # 3) Payload conflict: first call creates event; second reuses ID with changed amount.
    conflict_id = "conflict-" + str(uuid.uuid4())
    print("\nPayload conflict setup:", send(event(conflict_id)))
    print("Payload conflict expected HTTP 409:", send(event(conflict_id, amount=99.99)))

    # 4) Invalid input: amount negative should be rejected by API validation (422).
    print("\nInvalid payload expected HTTP 422:", send(event("invalid-" + str(uuid.uuid4()), amount=-1)))

    # Database/API failure test: stop the local API briefly or point a staging instance
    # at an unavailable DB, then verify clients receive 503 and no business_effect is added.
    print("\nMetrics endpoint:", requests.get(f"{BASE}/api/metrics", headers=HEADERS, timeout=30).json())
