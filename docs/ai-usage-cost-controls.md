# AI usage and cost controls

Per-minute administrator, domain, and global Redis windows complement PostgreSQL daily request and
monthly estimated-cost ceilings. Inputs, outputs, retries, timeouts, and concurrency are bounded.
Provider rate limits and transient failures retry with bounded exponential backoff and jitter;
authentication, schema, model/request, and validation failures do not retry.
