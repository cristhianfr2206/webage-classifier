# AI provider configuration

AI is off by default. `disabled` rejects all work, `fake` is deterministic and keyless, and any
other provider name selects the HTTPS JSON adapter. Enabled network mode requires an HTTPS endpoint,
model, and environment-only API key. Keys are never persisted or returned. Requests are JSON-only,
temperature defaults to zero, and input/output/time/retry limits are mandatory.
