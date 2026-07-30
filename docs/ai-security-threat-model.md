# AI security threat model

Website and provider content are hostile. Inputs are minimized, cleaned, bounded, and hashed.
Outputs reject extra fields, policy fields, unknown/duplicate categories, invalid confidence, and
oversized evidence. Prior classifications remain current until a validated transaction commits.
AI can be wrong, never establishes legal suitability, never sets age policy, and stores no hidden
reasoning. Secrets, prompts, internal errors, raw HTML, and unrelated records are excluded.
