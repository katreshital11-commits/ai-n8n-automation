# ⚡ Reliable AI Automation & Event-Driven Systems

Production-oriented AI automation workflows, event-driven integrations, data validation pipelines, and custom Python components built with n8n, PostgreSQL, Redis, APIs, and LLM-powered processing.

This repository demonstrates practical engineering patterns for reliable automation, including input validation, duplicate-event prevention, transactional database writes, structured AI outputs, and resilient workflow design.

## 📊 Core Engineering Capabilities

- **Event-Driven Processing:** Webhook-based automation workflows for routing events and coordinating downstream business operations.
- **Idempotency & Duplicate Prevention:** PostgreSQL-backed idempotency gate using unique event IDs, payload fingerprints, and atomic database transactions.
- **AI Data Validation:** Structured outputs, schema validation, and explicit checks around LLM-generated data.
- **Workflow Orchestration:** n8n integrations with external APIs, CRM systems, databases, and business applications.
- **Data Transformation:** Python, SQL, and validation logic for normalizing, transforming, and processing business data.
- **Resilient Integrations:** Design patterns for retries, failure visibility, and safe downstream processing.

> Performance figures and accuracy metrics should be added here only when supported by reproducible benchmarks, test reports, or production measurements.

## 📁 Repository Map

### 🤖 1. Enterprise Multi-Agent & Automation Workflows

- **`OmniEngine-Production-Final-GoogleCalendar-Redis-HubSpot-Postgres.json`** — Workflow integrating Google Calendar, Redis, HubSpot, and PostgreSQL for coordinated business operations.
- **`Shopify WhatsApp Abandoned Cart Recovery — AI Automation.json`** — Workflow for processing abandoned-cart events and coordinating WhatsApp recovery communications.
- **`whatsapp-auto-reply-bot.json`** — WhatsApp automation workflow.
- **`notion-todoist-2way-sync.json`** — Two-way synchronization workflow for Notion and Todoist.
- **`website-data-splitter-workflow.json`** — Workflow for splitting website data into manageable units.

### 🛡️ 2. Validation & Data Processing

- **`AI Powered_ Autonomous Real Estate NLP & Intent Engine -.json`** — AI-powered intent extraction for real-estate requests, including informal Roman Hinglish inputs such as *"kal dopahar 3 baje site visit"*. 
- **`Ai Powerd Zoho to WhatsApp Billing Engine (2).json`** — Invoice and billing automation connecting Zoho-related data processing with WhatsApp workflows.

### 🔐 3. Python Idempotency Gate

- **`idempotency_gate.py`** — PostgreSQL-backed webhook idempotency example featuring:
  - Pydantic-based payload validation.
  - SHA-256 payload fingerprinting.
  - A PostgreSQL primary key on `event_id` to arbitrate duplicate deliveries.
  - Atomic writes for the event record, example business effect, and completion result.
  - Rejection of reused event IDs with different payloads.
  - Duplicate detection and stored-result retrieval.

**Important implementation boundary:** The example demonstrates atomicity for database effects. External CRM, WhatsApp, SMS, or other API calls are not made transactionally by this file. For reliable external delivery, use a transactional outbox, retry handling, and downstream idempotency.

### 🧰 4. Additional Resources

- **`README.md`** — Project overview, repository structure, and engineering principles.
- **`LearnTube-Certificate-sheetal-katre.jpg`** — Assessment certificate image.

## ⚙️ Engineering Principles

1. **Validate at system boundaries:** Reject malformed or unexpected inputs before business processing.
2. **Use deterministic identity:** Choose a stable event ID that identifies one logical event.
3. **Make database changes atomic:** Keep related database writes in one transaction.
4. **Prevent duplicate processing:** Enforce idempotency with database constraints instead of relying solely on application-level checks.
5. **Handle external side effects explicitly:** Use transactional outbox patterns and downstream idempotency where required.
6. **Make failures observable:** Use structured logs, clear exceptions, and actionable error handling.
7. **Verify performance claims:** Measure throughput, latency, failure rates, and data quality using repeatable tests.

## 🧪 Running the Python Idempotency Example

### Requirements

- Python 3.10+
- PostgreSQL
- `psycopg` 3
- Pydantic 2

Install dependencies:

```bash
pip install "psycopg[binary]>=3.1,<4" "pydantic>=2.5,<3"
```

Configure the database connection:

```bash
# Set this in your local environment; do not commit credentials.
DATABASE_URL=postgresql://user:password@localhost:5432/database
```

Run the example against a dedicated development or test database:

```bash
python idempotency_gate.py
```

The smoke test expects the first delivery to return `processed` and the repeated delivery to return `duplicate`.

**Testing status:** Running the smoke test verifies the basic database flow. Concurrent delivery, payload-conflict, rollback, and failure-recovery behavior should be covered by additional integration tests before describing the implementation as production-tested.

## 🚀 Future Improvements

- Automated PostgreSQL integration and concurrency tests.
- GitHub Actions for repeatable test execution.
- Versioned database migrations.
- Transactional outbox and retry worker for external API delivery.
- Reproducible throughput and latency benchmarks.
- Monitoring for duplicate events, processing failures, and retry rates.
