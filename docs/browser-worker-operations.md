# Browser worker operations

Browser jobs use a separate Celery broker database and three queues:

- `browser_realtime` for explicit interactive administrator work.
- `browser` for automatic HTTP-first fallback.
- `maintenance` for stale-run and artifact reconciliation.

Inspect them with:

```bash
docker compose logs -f worker-browser-realtime worker-browser worker-browser-maintenance
make browser-worker-status
docker compose exec redis redis-cli -n 1 --scan
```

Workers are non-root, have no published/debug ports, and are bounded by Compose CPU, memory, PID, shared-memory, task, context, and concurrency limits. Do not add `--no-sandbox`, host mounts, the Docker socket, persistent Chromium profiles, or broad `.env` injection.

Cancellation is cooperative and checked before result persistence. Late acknowledgements, worker-loss rejection, limited retries, domain locks, soft/hard limits, and stale-run recovery make redelivery safe. A previous valid classification remains untouched when browser work fails.
