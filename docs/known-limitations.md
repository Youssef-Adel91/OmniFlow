# Known limitations (honest list)

## Never exercised against the real service
- **Meta** (WhatsApp, Instagram, Messenger, comments, Private Reply, Embedded Signup, webhooks signature with a real app secret): fakes only. Webhook field names follow Meta docs but have not been replayed from real deliveries.
- **LLM**: answer quality, cost, latency and the output scanner (`eval_bot --live`) never run with a real model.
- **Moyasar**: sandbox provider is a local fake; real checkout, 3-D Secure, webhooks and refunds are untested.
- **ZATCA**: nothing sent or validated by the official validator; e-invoice flag off.
- **Sentry**: SDK wired, never seen in a real project.

## Not implemented
- Telegram channel; a common `ChannelAdapter` interface.
- Property photos in listings (map link only); Qdrant-based listing match (SQL matcher is used).
- Calendar page for viewings; sales report UI (API exists).
- Prometheus metrics / dashboard; recorded load-test result.
- Phone OTP for new companies.

## Behaviours to know
- Existing Messenger conversations stored as `instagram` are relabelled lazily on the customer's next message.
- No client-side 7-day check for Private Reply; Meta's rejection is handled as a failed send.
- CI runs on a single self-hosted Windows runner; Linux-only tests are skipped there.
