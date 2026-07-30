import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.evaluation import calculate_metrics
from app.models import (
    EvaluationExample,
    EvaluationResult,
    EvaluationRun,
    EvaluationStatus,
)


async def execute_evaluation(db: AsyncSession, run_id: uuid.UUID) -> dict[str, object]:
    run = await db.scalar(select(EvaluationRun).where(EvaluationRun.id == run_id).with_for_update())
    if run is None:
        raise ValueError("evaluation run not found")
    if run.status == EvaluationStatus.COMPLETED:
        return run.metrics
    if run.cancel_requested:
        run.status = EvaluationStatus.CANCELLED
        await db.commit()
        return {}
    run.status = EvaluationStatus.RUNNING
    run.started_at = run.started_at or datetime.now(UTC)
    await db.commit()
    examples = list(
        (
            await db.scalars(
                select(EvaluationExample)
                .where(
                    EvaluationExample.dataset_id == run.dataset_id,
                    EvaluationExample.adjudicated.is_(True),
                )
                .order_by(EvaluationExample.domain)
            )
        ).all()
    )
    result_rows: list[dict[str, object]] = []
    for example in examples:
        await db.refresh(run)
        if run.cancel_requested:
            run.status = EvaluationStatus.CANCELLED
            await db.commit()
            return {}
        existing = await db.scalar(
            select(EvaluationResult).where(
                EvaluationResult.run_id == run.id,
                EvaluationResult.example_id == example.id,
            )
        )
        prediction = example.evidence.get("prediction", {})
        if not isinstance(prediction, dict):
            prediction = {}
        if existing is None:
            existing = EvaluationResult(
                run_id=run.id,
                example_id=example.id,
                predicted_primary=str(prediction.get("primary_category") or "") or None,
                predicted_secondary=list(prediction.get("secondary_categories") or []),
                predicted_age=prediction.get("age")
                if isinstance(prediction.get("age"), int)
                else None,
                predicted_rating=str(prediction.get("rating") or "") or None,
                predicted_blocked=prediction.get("blocked")
                if isinstance(prediction.get("blocked"), bool)
                else None,
                confidence=int(prediction.get("confidence") or 0),
                source=str(prediction.get("source") or "unknown")[:20],
                duration_ms=int(prediction.get("duration_ms") or 0),
                estimated_cost_microunits=int(prediction.get("estimated_cost_microunits") or 0),
                manual_review=bool(prediction.get("manual_review")),
            )
            db.add(existing)
            run.processed_count += 1
        result_rows.append(
            {
                "adjudicated": example.adjudicated,
                "expected_primary": example.primary_category,
                "expected_secondary": example.secondary_categories,
                "expected_age": example.expected_age,
                "expected_rating": example.expected_rating,
                "expected_blocked": example.expected_blocked,
                "predicted_primary": existing.predicted_primary,
                "predicted_secondary": existing.predicted_secondary,
                "predicted_age": existing.predicted_age,
                "predicted_rating": existing.predicted_rating,
                "predicted_blocked": existing.predicted_blocked,
                "confidence": existing.confidence,
                "source": existing.source,
                "duration_ms": existing.duration_ms,
                "estimated_cost_microunits": existing.estimated_cost_microunits,
                "manual_review": existing.manual_review,
            }
        )
    run.metrics = calculate_metrics(result_rows)
    run.status = EvaluationStatus.COMPLETED
    run.completed_at = datetime.now(UTC)
    await db.commit()
    return run.metrics
