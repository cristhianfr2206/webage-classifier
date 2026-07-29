# Screenshot retention

Screenshots are off by default (`BROWSER_SCREENSHOT_ENABLED=false`). Enabling storage does not capture every page: policy must also select low confidence, manual review, a configured high-risk case, explicit administrator evidence, or configured failure evidence.

PNG artifacts have opaque server-generated identifiers and bounded dimensions and bytes. They live in the private `browser_artifacts` Docker volume, not PostgreSQL or a public static directory. Retrieval requires backend authentication and ownership/administrator authorization. Deletion is administrator-only, CSRF protected, and audited.

`cleanup_browser_artifacts` removes expired files and clears metadata. It also removes orphaned `.png` files that do not correspond to database metadata. Never accept client filenames or expose the volume through the frontend.
