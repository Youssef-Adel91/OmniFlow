# Load test (webhook → reply)

**Never run against production.** Use a local or staging stack and a TEST tenant; each request creates a customer, a conversation and a (billable) AI reply — set a mock/low-cost LLM mode on staging.

1. `pip install locust` (not a project dependency).
2. `META_HMAC_SECRET=<same as the API's META_WEBHOOK_HMAC_SECRET> WA_PHONE_NUMBER_ID=<test tenant's phone_number_id> locust -f loadtest/locustfile.py --host http://127.0.0.1:8000 --headless -u 30 -r 5 -t 5m --csv loadtest/out`
3. After the run: `psql … -f loadtest/reply_latency.sql` for the reply leg.

## Goals (initial — revise after the first real run; they are targets, not measurements)
| Leg | Metric | Goal |
|---|---|---|
| Ingestion (Locust) | p95 latency of `POST /webhooks/whatsapp` | < 500 ms at 30 virtual users (~15 msg/s) |
| Ingestion | failure rate | < 1 % (all non-200 are failures) |
| Reply (SQL) | answered within the run | ≥ 99 % |
| Reply (SQL) | p95 webhook → SENT reply | < 20 s with the real LLM; report p50 too |
| Health during the run | `/health/lag` | status stays `ok` or `warn`; no `critical`; dead letters stay 0 |

**No results exist yet**: this was written without Docker/Kafka/an LLM, so it has never been executed. The first run's numbers should replace the goals above.
