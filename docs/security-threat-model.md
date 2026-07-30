# Browser security threat model

## Evaluation and pilot controls

Evaluation imports are hostile input. CSV/JSONL size, row count, domains, categories,
field lengths, and evidence are validated transactionally. Spreadsheet exports
neutralize formula-leading characters. Published ground truth is protected by
PostgreSQL triggers and API rules; corrections require an immutable successor
version with lineage.

Reviewer disagreements are excluded until adjudicated. Tuning accepts bounded
data-only weights and thresholds, never executable expressions. Activation and
review decisions require administrator authorization, CSRF validation, and audit
records. Pilots cannot exceed 10,000 domains; dispatch is capacity bounded and
dry-runs never dispatch tasks. Existing SSRF, browser isolation, secret handling,
locking, and prior-classification preservation controls remain authoritative.

Every URL, browser request, redirect, frame, popup, and downloaded resource is untrusted. Only HTTP and HTTPS destinations whose complete DNS answer set is globally routable are permitted. Loopback, private, link-local, reserved, carrier-grade NAT, multicast, unspecified, metadata, and internal destinations are denied.

Controls are layered: centralized DNS/IP validation, best-effort re-resolution immediately before route continuation, Playwright request interception, redirect/final-URL validation, bounded requests and bytes, disposable contexts, capability denial, process limits, and container network separation. This reduces but cannot perfectly eliminate DNS-rebinding windows or browser-engine vulnerabilities.

Playwright workers receive no session or administrator secrets, publish no ports, mount no host directories or Docker socket, drop capabilities, and use a read-only root with bounded temporary storage. The database credential is currently shared with the backend because this local architecture has one application database role; it is an acknowledged least-privilege limitation.

Rendered strings are stored and returned as data only. The frontend never uses raw HTML injection. Raw HTML is not retained. Screenshots are verified, bounded, private, authenticated, expiring artifacts.

CAPTCHA, login, paywall, access-control, bot-defense, and geographic-restriction bypasses are forbidden.
