# Classifier versioning and tuning

Rulesets, thresholds, policy snapshots, and classifier compositions are immutable.
Migration 0006 seeds `milestone-5-baseline`; evaluation and pilot records pin a
version. Administrator tuning creates a validated successor instead of editing
history. Arbitrary code and expressions are rejected, and activation or rollback is
audited.

PostgreSQL age policies remain authoritative. Policy snapshots provide
reproducibility; they do not allow classifiers to override age policy.
