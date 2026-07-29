# Browser security threat model

Every URL, browser request, redirect, frame, popup, and downloaded resource is untrusted. Only HTTP and HTTPS destinations whose complete DNS answer set is globally routable are permitted. Loopback, private, link-local, reserved, carrier-grade NAT, multicast, unspecified, metadata, and internal destinations are denied.

Controls are layered: centralized DNS/IP validation, best-effort re-resolution immediately before route continuation, Playwright request interception, redirect/final-URL validation, bounded requests and bytes, disposable contexts, capability denial, process limits, and container network separation. This reduces but cannot perfectly eliminate DNS-rebinding windows or browser-engine vulnerabilities.

Playwright workers receive no session or administrator secrets, publish no ports, mount no host directories or Docker socket, drop capabilities, and use a read-only root with bounded temporary storage. The database credential is currently shared with the backend because this local architecture has one application database role; it is an acknowledged least-privilege limitation.

Rendered strings are stored and returned as data only. The frontend never uses raw HTML injection. Raw HTML is not retained. Screenshots are verified, bounded, private, authenticated, expiring artifacts.

CAPTCHA, login, paywall, access-control, bot-defense, and geographic-restriction bypasses are forbidden.
