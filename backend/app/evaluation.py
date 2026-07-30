import csv
import hashlib
import io
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

import idna

MAX_DATASET_BYTES = 5_000_000
MAX_DATASET_ROWS = 50_000
PILOT_SIZES = frozenset({100, 1_000, 10_000})


class EvaluationInputError(ValueError):
    pass


@dataclass(frozen=True)
class LabeledExample:
    domain: str
    primary_category: str | None
    secondary_categories: tuple[str, ...]
    expected_age: int | None
    expected_rating: str | None
    expected_blocked: bool | None
    evidence: dict[str, object]
    adjudicated: bool = True


def _domain(value: object) -> str:
    raw = str(value or "").strip().rstrip(".").lower()
    if not raw or "/" in raw or ":" in raw or len(raw) > 253:
        raise EvaluationInputError("invalid domain")
    try:
        result = idna.encode(raw, uts46=True).decode()
    except idna.IDNAError as exc:
        raise EvaluationInputError("invalid domain") from exc
    if "." not in result or any(not part for part in result.split(".")):
        raise EvaluationInputError("invalid domain")
    return result


def _boolean(value: object) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    raise EvaluationInputError("expected_blocked must be boolean")


def _integer(value: object, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _strings(value: object) -> set[str]:
    return {str(item) for item in value} if isinstance(value, list | tuple | set) else set()


def _row(value: dict[str, Any]) -> LabeledExample:
    primary = str(value.get("primary_category") or "").strip() or None
    secondary_raw = value.get("secondary_categories", [])
    if isinstance(secondary_raw, str):
        secondary = tuple(item.strip() for item in secondary_raw.split("|") if item.strip())
    elif isinstance(secondary_raw, list):
        secondary = tuple(str(item).strip() for item in secondary_raw if str(item).strip())
    else:
        raise EvaluationInputError("secondary_categories must be a list or pipe-separated string")
    if len(secondary) != len(set(secondary)) or primary in secondary:
        raise EvaluationInputError("categories must be unique")
    age_value = value.get("expected_age")
    age = None if age_value in (None, "") else _integer(age_value, -1)
    if age is not None and not 0 <= age <= 120:
        raise EvaluationInputError("expected_age must be between 0 and 120")
    evidence = value.get("evidence", {})
    if isinstance(evidence, str):
        evidence = {"text": evidence[:10_000]}
    if not isinstance(evidence, dict) or len(json.dumps(evidence)) > 20_000:
        raise EvaluationInputError("evidence must be a bounded object")
    return LabeledExample(
        domain=_domain(value.get("domain")),
        primary_category=primary,
        secondary_categories=secondary,
        expected_age=age,
        expected_rating=str(value.get("expected_rating") or "").strip() or None,
        expected_blocked=_boolean(value.get("expected_blocked")),
        evidence=evidence,
        adjudicated=bool(value.get("adjudicated", True)),
    )


def parse_dataset(content: bytes, source_format: str) -> tuple[list[LabeledExample], str]:
    if not content or len(content) > MAX_DATASET_BYTES:
        raise EvaluationInputError("dataset is empty or exceeds the size limit")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise EvaluationInputError("dataset must be UTF-8") from exc
    try:
        if source_format == "csv":
            raw_rows = list(csv.DictReader(io.StringIO(text)))
        elif source_format == "jsonl":
            raw_rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            raise EvaluationInputError("source_format must be csv or jsonl")
    except (csv.Error, json.JSONDecodeError) as exc:
        raise EvaluationInputError("malformed dataset") from exc
    if not raw_rows or len(raw_rows) > MAX_DATASET_ROWS:
        raise EvaluationInputError("dataset row count is outside allowed bounds")
    rows = [_row(dict(item)) for item in raw_rows]
    if len({item.domain for item in rows}) != len(rows):
        raise EvaluationInputError("duplicate domain in dataset")
    canonical = json.dumps(
        [
            {
                "domain": item.domain,
                "primary_category": item.primary_category,
                "secondary_categories": item.secondary_categories,
                "expected_age": item.expected_age,
                "expected_rating": item.expected_rating,
                "expected_blocked": item.expected_blocked,
                "evidence": item.evidence,
                "adjudicated": item.adjudicated,
            }
            for item in rows
        ],
        sort_keys=True,
        separators=(",", ":"),
    )
    return rows, hashlib.sha256(canonical.encode()).hexdigest()


def reviewer_agreement(labels: list[tuple[str, int]]) -> dict[str, object]:
    if len(labels) < 2:
        return {"rated_pairs": 0, "agreements": 0, "agreement_rate": None}
    pairs = 0
    agreements = 0
    for index, left in enumerate(labels):
        for right in labels[index + 1 :]:
            pairs += 1
            agreements += int(left == right)
    return {
        "rated_pairs": pairs,
        "agreements": agreements,
        "agreement_rate": round(agreements / pairs, 4) if pairs else None,
    }


def calculate_metrics(rows: list[dict[str, object]]) -> dict[str, object]:
    eligible = [row for row in rows if bool(row.get("adjudicated", True))]
    count = len(eligible)
    if not count:
        return {"evaluated": 0}
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    category_correct = secondary_correct = rating_correct = blocked_correct = 0
    category_policy_wrong = policy_category_wrong = 0
    unknown = manual = high_risk_fn = overrestrictive_fp = 0
    sources: Counter[str] = Counter()
    calibration: dict[int, list[int]] = defaultdict(list)
    durations: list[int] = []
    cost = 0
    for row in eligible:
        expected = str(row.get("expected_primary") or "unknown")
        predicted = str(row.get("predicted_primary") or "unknown")
        confusion[expected][predicted] += 1
        category_ok = expected == predicted
        category_correct += int(category_ok)
        expected_secondary = _strings(row.get("expected_secondary"))
        predicted_secondary = _strings(row.get("predicted_secondary"))
        secondary_correct += int(expected_secondary == predicted_secondary)
        rating_ok = row.get("expected_rating") == row.get("predicted_rating")
        blocked_ok = row.get("expected_blocked") == row.get("predicted_blocked")
        rating_correct += int(rating_ok)
        blocked_correct += int(blocked_ok)
        policy_ok = rating_ok and blocked_ok
        category_policy_wrong += int(category_ok and not policy_ok)
        policy_category_wrong += int(policy_ok and not category_ok)
        high_risk_fn += int(
            bool(row.get("expected_blocked")) and not bool(row.get("predicted_blocked"))
        )
        expected_age = row.get("expected_age")
        predicted_age = row.get("predicted_age")
        overrestrictive_fp += int(
            not bool(row.get("expected_blocked"))
            and bool(row.get("predicted_blocked"))
            or isinstance(expected_age, int)
            and isinstance(predicted_age, int)
            and predicted_age > expected_age
        )
        unknown += int(predicted == "unknown")
        manual += int(bool(row.get("manual_review")))
        source = str(row.get("source") or "unknown")
        sources[source] += 1
        confidence = _integer(row.get("confidence"))
        calibration[min(confidence // 10, 9) * 10].append(int(category_ok))
        durations.append(_integer(row.get("duration_ms")))
        cost += _integer(row.get("estimated_cost_microunits"))
    per_category: dict[str, dict[str, float | int]] = {}
    labels = sorted(set(confusion) | {key for values in confusion.values() for key in values})
    for label in labels:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in labels if other != label)
        fn = sum(confusion[label][other] for other in labels if other != label)
        tn = count - tp - fp - fn
        per_category[label] = {
            "support": sum(confusion[label].values()),
            "precision": round(tp / (tp + fp), 4) if tp + fp else 0,
            "recall": round(tp / (tp + fn), 4) if tp + fn else 0,
            "f1": round(2 * tp / (2 * tp + fp + fn), 4) if 2 * tp + fp + fn else 0,
            "accuracy": round((tp + tn) / count, 4),
        }
    calibration_bins = [
        {
            "minimum_confidence": key,
            "maximum_confidence": key + 9,
            "count": len(values),
            "accuracy": round(sum(values) / len(values), 4),
        }
        for key, values in sorted(calibration.items())
    ]
    brier = (
        sum(
            (
                (_integer(row.get("confidence")) / 100)
                - int(row.get("expected_primary") == row.get("predicted_primary"))
            )
            ** 2
            for row in eligible
        )
        / count
    )
    return {
        "evaluated": count,
        "primary_category_accuracy": round(category_correct / count, 4),
        "secondary_category_accuracy": round(secondary_correct / count, 4),
        "final_age_rating_accuracy": round(rating_correct / count, 4),
        "blocked_allowed_accuracy": round(blocked_correct / count, 4),
        "category_correct_policy_wrong": category_policy_wrong,
        "policy_correct_category_wrong": policy_category_wrong,
        "high_risk_false_negatives": high_risk_fn,
        "overrestrictive_false_positives": overrestrictive_fp,
        "unknown_rate": round(unknown / count, 4),
        "manual_review_rate": round(manual / count, 4),
        "source_usage_percent": {
            key: round(value * 100 / count, 2) for key, value in sorted(sources.items())
        },
        "duration_ms": {
            "total": sum(durations),
            "average": round(sum(durations) / count, 2),
            "maximum": max(durations),
        },
        "estimated_cost_microunits": cost,
        "confusion_matrix": {key: dict(value) for key, value in sorted(confusion.items())},
        "per_category": per_category,
        "calibration_bins": calibration_bins,
        "brier_score": round(brier, 6),
    }


def pilot_estimate(
    *,
    size: int,
    pending: int,
    capacity: int,
    browser_rate: float,
    ai_rate: float,
    static_ms: int,
    browser_ms: int,
    ai_ms: int,
    ai_cost_microunits: int,
) -> tuple[dict[str, object], str]:
    if size not in PILOT_SIZES:
        raise EvaluationInputError("pilot size must be 100, 1000, or 10000")
    selected = min(size, max(pending, 0))
    browser = round(selected * browser_rate)
    ai = round(selected * ai_rate)
    total_ms = selected * static_ms + browser * browser_ms + ai * ai_ms
    estimate: dict[str, object] = {
        "pending_domains": selected,
        "expected_static_inspections": selected,
        "expected_browser_fallbacks": browser,
        "expected_ai_calls": ai,
        "queue_capacity_impact": min(selected, capacity),
        "estimated_runtime_seconds": round(total_ms / max(capacity, 1) / 1000),
        "estimated_ai_cost_microunits": ai * ai_cost_microunits,
        "estimated_storage_bytes": selected * 2_048 + browser * 4_096 + ai * 1_024,
    }
    digest = hashlib.sha256(
        json.dumps(estimate, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return estimate, digest


def csv_safe(value: object) -> str:
    text = str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@", "\t", "\r")) else text
