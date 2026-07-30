# Evaluation datasets and reports

Milestone 6 evaluates the existing static, rules, browser, and optional AI pipeline.
Datasets use UTF-8 CSV or JSONL with a domain, primary category, optional secondary
categories, expected age/rating/blocked policy, and bounded fixture evidence.

Published versions are immutable. Each records creator, checksum, publication time,
change notes, and optional prior-version lineage. Correct labels by publishing a new
linked version. Existing results retain their original ground truth.

Two reviewers may label selected examples independently. Category and expected age
must agree before an example is adjudicated. Disagreements are reported and excluded
from evaluation ground truth until explicitly resolved.

Reports separate primary/secondary category accuracy from final-rating and
blocked/allowed accuracy. They include cross-correctness cases, high-risk false
negatives, over-restrictive false positives, calibration, source use, runtime, and
estimated cost. Run deterministic tests with `make evaluation-test`.
