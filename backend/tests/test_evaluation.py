import json
from pathlib import Path

import pytest

from app.evaluation import (
    EvaluationInputError,
    calculate_metrics,
    csv_safe,
    parse_dataset,
    pilot_estimate,
    reviewer_agreement,
)
from app.evaluation_celery_app import evaluation_celery_app


def test_csv_and_jsonl_import_are_canonical_and_versionable() -> None:
    csv_rows, csv_hash = parse_dataset(
        b"domain,primary_category,secondary_categories,expected_age,expected_rating,"
        b"expected_blocked\nlearn.example,education,,8,children,false\n",
        "csv",
    )
    json_rows, json_hash = parse_dataset(
        (
            json.dumps(
                {
                    "domain": "learn.example",
                    "primary_category": "education",
                    "secondary_categories": [],
                    "expected_age": 8,
                    "expected_rating": "children",
                    "expected_blocked": False,
                }
            )
            + "\n"
        ).encode(),
        "jsonl",
    )
    assert csv_rows == json_rows
    assert csv_hash == json_hash


def test_checked_in_csv_and_jsonl_fixtures_are_equivalent() -> None:
    fixture_root = Path(__file__).parent / "fixtures"
    csv_rows, csv_hash = parse_dataset((fixture_root / "evaluation-v1.csv").read_bytes(), "csv")
    jsonl_rows, jsonl_hash = parse_dataset(
        (fixture_root / "evaluation-v1.jsonl").read_bytes(), "jsonl"
    )
    assert csv_rows == jsonl_rows
    assert csv_hash == jsonl_hash


@pytest.mark.parametrize(
    "content",
    [
        b"domain,primary_category\nlocalhost,education\n",
        b"domain,primary_category\nhttps://example.com,education\n",
        b"domain,primary_category\nsame.example,education\nsame.example,social\n",
    ],
)
def test_dataset_rejects_unsafe_or_duplicate_domains(content: bytes) -> None:
    with pytest.raises(EvaluationInputError):
        parse_dataset(content, "csv")


def test_category_policy_metrics_are_reported_separately() -> None:
    metrics = calculate_metrics(
        [
            {
                "expected_primary": "education",
                "expected_secondary": ["social"],
                "expected_age": 8,
                "expected_rating": "children",
                "expected_blocked": False,
                "predicted_primary": "education",
                "predicted_secondary": ["social"],
                "predicted_age": 18,
                "predicted_rating": "adult",
                "predicted_blocked": True,
                "confidence": 90,
                "source": "rules",
                "duration_ms": 100,
            },
            {
                "expected_primary": "social",
                "expected_secondary": [],
                "expected_age": 13,
                "expected_rating": "teen",
                "expected_blocked": False,
                "predicted_primary": "education",
                "predicted_secondary": [],
                "predicted_age": 13,
                "predicted_rating": "teen",
                "predicted_blocked": False,
                "confidence": 60,
                "source": "ai",
                "duration_ms": 300,
                "manual_review": True,
            },
        ]
    )
    assert metrics["primary_category_accuracy"] == 0.5
    assert metrics["secondary_category_accuracy"] == 1.0
    assert metrics["final_age_rating_accuracy"] == 0.5
    assert metrics["blocked_allowed_accuracy"] == 0.5
    assert metrics["category_correct_policy_wrong"] == 1
    assert metrics["policy_correct_category_wrong"] == 1
    assert metrics["overrestrictive_false_positives"] == 1
    assert metrics["source_usage_percent"] == {"ai": 50.0, "rules": 50.0}


def test_high_risk_false_negative_calibration_and_unresolved_exclusion() -> None:
    metrics = calculate_metrics(
        [
            {
                "expected_primary": "adult",
                "expected_blocked": True,
                "predicted_primary": "unknown",
                "predicted_blocked": False,
                "confidence": 20,
                "source": "browser",
            },
            {
                "adjudicated": False,
                "expected_primary": "social",
                "predicted_primary": "social",
                "confidence": 100,
            },
        ]
    )
    assert metrics["evaluated"] == 1
    assert metrics["high_risk_false_negatives"] == 1
    assert metrics["unknown_rate"] == 1.0
    assert metrics["brier_score"] == 0.04


def test_reviewer_agreement_and_disagreement() -> None:
    assert reviewer_agreement([("social", 13), ("social", 13)])["agreement_rate"] == 1.0
    assert reviewer_agreement([("social", 13), ("education", 8)])["agreement_rate"] == 0.0


def test_pilot_dry_run_is_bounded_reproducible_and_dispatch_free() -> None:
    first, first_hash = pilot_estimate(
        size=1000,
        pending=800,
        capacity=50,
        browser_rate=0.2,
        ai_rate=0.05,
        static_ms=500,
        browser_ms=15000,
        ai_ms=5000,
        ai_cost_microunits=2000,
    )
    second, second_hash = pilot_estimate(
        size=1000,
        pending=800,
        capacity=50,
        browser_rate=0.2,
        ai_rate=0.05,
        static_ms=500,
        browser_ms=15000,
        ai_ms=5000,
        ai_cost_microunits=2000,
    )
    assert first == second
    assert first_hash == second_hash
    assert first["pending_domains"] == 800
    assert first["expected_browser_fallbacks"] == 160
    assert first["expected_ai_calls"] == 40
    with pytest.raises(EvaluationInputError):
        pilot_estimate(
            size=1_000_000,
            pending=1_000_000,
            capacity=100,
            browser_rate=0,
            ai_rate=0,
            static_ms=1,
            browser_ms=1,
            ai_ms=1,
            ai_cost_microunits=0,
        )


def test_csv_export_neutralizes_formulas() -> None:
    assert csv_safe('=HYPERLINK("bad")').startswith("'")
    assert csv_safe("@SUM(A1:A2)").startswith("'")
    assert csv_safe("ordinary") == "ordinary"


def test_evaluation_task_registry_and_routes_are_isolated() -> None:
    routes = evaluation_celery_app.conf.task_routes
    assert routes["app.evaluation_tasks.run_evaluation"]["queue"] == "evaluation"
    assert routes["app.evaluation_tasks.recover_evaluations"]["queue"] == "evaluation_maintenance"
    assert routes["app.evaluation_tasks.advance_pilots"]["queue"] == "evaluation_maintenance"
    assert "app.tasks.classify_website" not in routes
    assert "app.browser_tasks.inspect_browser" not in routes
    assert "app.ai_tasks.classify_ai" not in routes
    assert evaluation_celery_app.conf.accept_content == ["json"]
