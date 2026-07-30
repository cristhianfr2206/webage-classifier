# AI worker operations

AI tasks use identifier-only JSON messages on `ai_realtime`, `ai`, and `ai_maintenance`. Workers
have separate concurrency, Redis database, task registry, resource limits, late acknowledgement,
worker-loss rejection, domain locks, cancellation, and stale recovery. Inspect with
`make ai-worker-status`; run security checks with `make ai-security-test`.
