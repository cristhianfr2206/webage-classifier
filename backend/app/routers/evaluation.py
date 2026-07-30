import csv
import hashlib
import io
import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.dependencies import AdminUser, Csrf, CurrentUser, Db
from app.evaluation import EvaluationInputError, csv_safe, parse_dataset, pilot_estimate
from app.evaluation_celery_app import evaluation_celery_app
from app.models import (
    AgePolicy,
    AuditLog,
    Category,
    ClassificationRun,
    ClassifierVersion,
    EvaluationDataset,
    EvaluationExample,
    EvaluationRun,
    EvaluationStatus,
    HumanLabel,
    ManualReviewCase,
    ManualReviewDecision,
    PilotItem,
    PilotRun,
    PilotStatus,
    PolicyVersion,
    QueueName,
    ReviewStatus,
    RulesetVersion,
    User,
    Website,
)
from app.queueing import dispatch_run
from app.schemas import (
    ClassifierVersionInput,
    DatasetImportInput,
    EvaluationRunInput,
    HumanLabelInput,
    PilotInput,
    ReviewDecisionInput,
    RulesetVersionInput,
)

router = APIRouter(prefix="/api", tags=["evaluation"])


def _dataset_dict(item: EvaluationDataset, count: int = 0) -> dict[str, object]:
    return {
        "id": item.id,
        "name": item.name,
        "version": item.version,
        "schema_version": item.schema_version,
        "prior_version_id": item.prior_version_id,
        "checksum": item.checksum,
        "source_format": item.source_format,
        "change_notes": item.change_notes,
        "created_by_id": item.created_by_id,
        "published": item.published,
        "published_at": item.published_at,
        "created_at": item.created_at,
        "example_count": count,
    }


@router.get("/evaluation/datasets")
async def list_datasets(_: CurrentUser, db: Db) -> list[dict[str, object]]:
    items = list(
        (
            await db.scalars(
                select(EvaluationDataset).order_by(EvaluationDataset.created_at.desc())
            )
        ).all()
    )
    return [
        _dataset_dict(
            item,
            int(
                await db.scalar(
                    select(func.count())
                    .select_from(EvaluationExample)
                    .where(EvaluationExample.dataset_id == item.id)
                )
                or 0
            ),
        )
        for item in items
    ]


@router.post("/evaluation/datasets/import", status_code=status.HTTP_201_CREATED)
async def import_dataset(
    payload: DatasetImportInput, _: Csrf, admin: AdminUser, db: Db
) -> dict[str, object]:
    try:
        rows, checksum = parse_dataset(payload.content.encode(), payload.source_format)
    except (EvaluationInputError, ValueError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    allowed = set((await db.scalars(select(Category.slug))).all())
    supplied = {
        category
        for row in rows
        for category in ([row.primary_category] if row.primary_category else [])
        + list(row.secondary_categories)
    }
    if supplied - allowed:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unsupported category label")
    prior = None
    if payload.prior_version_id:
        prior = await db.get(EvaluationDataset, payload.prior_version_id)
        if prior is None or prior.name != payload.name:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid dataset lineage")
    dataset = EvaluationDataset(
        name=payload.name,
        version=payload.version,
        prior_version_id=prior.id if prior else None,
        checksum=checksum,
        source_format=payload.source_format,
        change_notes=payload.change_notes,
        created_by_id=admin.id,
        published=False,
        published_at=None,
    )
    db.add(dataset)
    try:
        await db.flush()
        for row in rows:
            db.add(
                EvaluationExample(
                    dataset_id=dataset.id,
                    domain=row.domain,
                    primary_category=row.primary_category,
                    secondary_categories=list(row.secondary_categories),
                    expected_age=row.expected_age,
                    expected_rating=row.expected_rating,
                    expected_blocked=row.expected_blocked,
                    evidence=row.evidence,
                    adjudicated=row.adjudicated,
                )
            )
        await db.flush()
        if payload.publish:
            dataset.published = True
            dataset.published_at = datetime.now(UTC)
        db.add(
            AuditLog(
                actor_id=admin.id,
                action="evaluation.dataset.import",
                target_type="evaluation_dataset",
                target_id=str(dataset.id),
                details={
                    "version": dataset.version,
                    "checksum": checksum,
                    "published": dataset.published,
                    "prior_version_id": str(dataset.prior_version_id)
                    if dataset.prior_version_id
                    else None,
                    "rows": len(rows),
                },
            )
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Dataset version or content already exists"
        ) from exc
    await db.refresh(dataset)
    return _dataset_dict(dataset, len(rows))


@router.post("/evaluation/examples/{example_id}/labels", status_code=status.HTTP_201_CREATED)
async def add_human_label(
    example_id: uuid.UUID,
    payload: HumanLabelInput,
    _: Csrf,
    admin: AdminUser,
    db: Db,
) -> dict[str, object]:
    example = await db.get(EvaluationExample, example_id)
    if example is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evaluation example not found")
    dataset = await db.get(EvaluationDataset, example.dataset_id)
    if dataset is None or dataset.published:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Published ground truth is immutable; create a successor dataset version",
        )
    if await db.scalar(select(Category).where(Category.slug == payload.category)) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unsupported category label")
    label = HumanLabel(
        example_id=example.id,
        reviewer_id=admin.id,
        category=payload.category,
        expected_age=payload.expected_age,
        notes=payload.notes,
    )
    db.add(label)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Reviewer already labeled this example"
        ) from exc
    labels = list(
        (await db.scalars(select(HumanLabel).where(HumanLabel.example_id == example.id))).all()
    )
    if len(labels) >= 2:
        pairs = {(item.category, item.expected_age) for item in labels}
        example.adjudicated = len(pairs) == 1
        if example.adjudicated:
            agreed_category, agreed_age = next(iter(pairs))
            example.primary_category = agreed_category
            example.expected_age = agreed_age
    await db.commit()
    return {
        "id": label.id,
        "reviewer_id": label.reviewer_id,
        "category": label.category,
        "expected_age": label.expected_age,
        "notes": label.notes,
        "adjudicated": example.adjudicated,
    }


@router.post("/evaluation/datasets/{dataset_id}/publish")
async def publish_dataset(
    dataset_id: uuid.UUID, _: Csrf, admin: AdminUser, db: Db
) -> dict[str, object]:
    dataset = await db.scalar(
        select(EvaluationDataset).where(EvaluationDataset.id == dataset_id).with_for_update()
    )
    if dataset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evaluation dataset not found")
    if dataset.published:
        raise HTTPException(status.HTTP_409_CONFLICT, "Dataset is already published and immutable")
    unresolved = int(
        await db.scalar(
            select(func.count())
            .select_from(EvaluationExample)
            .where(
                EvaluationExample.dataset_id == dataset.id,
                EvaluationExample.adjudicated.is_(False),
            )
        )
        or 0
    )
    if unresolved:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Reviewer disagreements must be adjudicated before publication",
        )
    dataset.published = True
    dataset.published_at = datetime.now(UTC)
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="evaluation.dataset.publish",
            target_type="evaluation_dataset",
            target_id=str(dataset.id),
            details={"checksum": dataset.checksum},
        )
    )
    await db.commit()
    return _dataset_dict(dataset)


@router.get("/evaluation/datasets/{dataset_id}/agreement")
async def agreement(dataset_id: uuid.UUID, _: CurrentUser, db: Db) -> dict[str, object]:
    examples = list(
        (
            await db.scalars(
                select(EvaluationExample).where(EvaluationExample.dataset_id == dataset_id)
            )
        ).all()
    )
    compared = agreed = 0
    disagreements: list[str] = []
    for example in examples:
        labels = list(
            (await db.scalars(select(HumanLabel).where(HumanLabel.example_id == example.id))).all()
        )
        if len(labels) >= 2:
            compared += 1
            values = {(item.category, item.expected_age) for item in labels}
            if len(values) == 1:
                agreed += 1
            else:
                disagreements.append(str(example.id))
    return {
        "compared_examples": compared,
        "agreements": agreed,
        "agreement_rate": round(agreed / compared, 4) if compared else None,
        "disagreement_example_ids": disagreements,
    }


@router.post("/evaluation/runs", status_code=status.HTTP_202_ACCEPTED)
async def create_evaluation_run(
    payload: EvaluationRunInput, _: Csrf, admin: AdminUser, db: Db
) -> dict[str, object]:
    dataset = await db.get(EvaluationDataset, payload.dataset_id)
    if dataset is None or not dataset.published:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Published dataset required")
    version = (
        await db.get(ClassifierVersion, payload.classifier_version_id)
        if payload.classifier_version_id
        else await db.scalar(select(ClassifierVersion).where(ClassifierVersion.is_active.is_(True)))
    )
    if version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No classifier version available")
    unresolved = int(
        await db.scalar(
            select(func.count())
            .select_from(EvaluationExample)
            .where(
                EvaluationExample.dataset_id == dataset.id,
                EvaluationExample.adjudicated.is_(False),
            )
        )
        or 0
    )
    total = int(
        await db.scalar(
            select(func.count())
            .select_from(EvaluationExample)
            .where(
                EvaluationExample.dataset_id == dataset.id,
                EvaluationExample.adjudicated.is_(True),
            )
        )
        or 0
    )
    run = EvaluationRun(
        dataset_id=dataset.id,
        classifier_version_id=version.id,
        requested_by_id=admin.id,
        total_count=total,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    task = evaluation_celery_app.send_task(
        "app.evaluation_tasks.run_evaluation",
        args=[str(run.id)],
        queue="evaluation",
        routing_key="evaluation",
    )
    run.task_id = task.id
    await db.commit()
    return {
        "id": run.id,
        "status": run.status,
        "total_count": total,
        "excluded_unresolved_disagreements": unresolved,
        "classifier_version_id": version.id,
    }


@router.get("/evaluation/runs")
async def list_evaluation_runs(_: CurrentUser, db: Db) -> list[dict[str, object]]:
    runs = list(
        (await db.scalars(select(EvaluationRun).order_by(EvaluationRun.created_at.desc()))).all()
    )
    return [
        {
            "id": run.id,
            "dataset_id": run.dataset_id,
            "classifier_version_id": run.classifier_version_id,
            "status": run.status,
            "total_count": run.total_count,
            "processed_count": run.processed_count,
            "failed_count": run.failed_count,
            "metrics": run.metrics,
        }
        for run in runs
    ]


@router.post("/evaluation/runs/{run_id}/{action}")
async def control_evaluation(
    run_id: uuid.UUID, action: str, _: Csrf, admin: AdminUser, db: Db
) -> dict[str, object]:
    run = await db.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evaluation run not found")
    if action == "pause" and run.status in {EvaluationStatus.PENDING, EvaluationStatus.RUNNING}:
        run.status = EvaluationStatus.PAUSED
    elif action == "resume" and run.status == EvaluationStatus.PAUSED:
        run.status = EvaluationStatus.PENDING
        evaluation_celery_app.send_task(
            "app.evaluation_tasks.run_evaluation", args=[str(run.id)], queue="evaluation"
        )
    elif action == "cancel" and run.status not in {
        EvaluationStatus.COMPLETED,
        EvaluationStatus.CANCELLED,
    }:
        run.cancel_requested = True
        run.status = EvaluationStatus.CANCELLED
    else:
        raise HTTPException(status.HTTP_409_CONFLICT, "Invalid state transition")
    db.add(
        AuditLog(
            actor_id=admin.id,
            action=f"evaluation.run.{action}",
            target_type="evaluation_run",
            target_id=str(run.id),
            details={},
        )
    )
    await db.commit()
    return {"id": run.id, "status": run.status}


@router.get("/evaluation/runs/{run_id}")
async def evaluation_run_detail(run_id: uuid.UUID, _: CurrentUser, db: Db) -> dict[str, object]:
    run = await db.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evaluation run not found")
    return {
        "id": run.id,
        "dataset_id": run.dataset_id,
        "classifier_version_id": run.classifier_version_id,
        "status": run.status,
        "total_count": run.total_count,
        "processed_count": run.processed_count,
        "failed_count": run.failed_count,
        "metrics": run.metrics,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }


@router.get("/evaluation/runs/{run_id}/metrics")
async def evaluation_metrics(run_id: uuid.UUID, _: CurrentUser, db: Db) -> dict[str, object]:
    run = await db.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evaluation run not found")
    return run.metrics


@router.get("/evaluation/runs/{run_id}/confusion-matrix")
async def evaluation_confusion_matrix(
    run_id: uuid.UUID, _: CurrentUser, db: Db
) -> dict[str, object]:
    run = await db.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evaluation run not found")
    value = run.metrics.get("confusion_matrix", {})
    return value if isinstance(value, dict) else {}


@router.get("/evaluation/runs/{run_id}/export")
async def export_evaluation(run_id: uuid.UUID, format: str, _: CurrentUser, db: Db) -> Response:
    run = await db.get(EvaluationRun, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evaluation run not found")
    if format == "json":
        return Response(
            json.dumps(run.metrics, sort_keys=True),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="evaluation-{run.id}.json"'},
        )
    if format != "csv":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "format must be csv or json")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["metric", "value"])
    for key, value in sorted(run.metrics.items()):
        writer.writerow([csv_safe(key), csv_safe(json.dumps(value, sort_keys=True))])
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="evaluation-{run.id}.csv"'},
    )


@router.get("/classifier/versions")
async def classifier_versions(_: CurrentUser, db: Db) -> list[dict[str, object]]:
    versions = list(
        (
            await db.scalars(
                select(ClassifierVersion).order_by(ClassifierVersion.created_at.desc())
            )
        ).all()
    )
    return [
        {
            "id": item.id,
            "version": item.version,
            "ruleset_version_id": item.ruleset_version_id,
            "policy_version_id": item.policy_version_id,
            "is_active": item.is_active,
            "change_notes": item.change_notes,
            "activated_at": item.activated_at,
        }
        for item in versions
    ]


@router.post("/classifier/rulesets", status_code=status.HTTP_201_CREATED)
async def create_ruleset(
    payload: RulesetVersionInput, _: Csrf, admin: AdminUser, db: Db
) -> dict[str, object]:
    canonical = json.dumps(
        {"weights": payload.weights, "thresholds": payload.thresholds},
        sort_keys=True,
        separators=(",", ":"),
    )
    if len(canonical) > 100_000:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Ruleset is too large")
    allowed_categories = set((await db.scalars(select(Category.slug))).all())
    if not payload.weights or set(payload.weights) - allowed_categories:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Unsupported ruleset category")
    for category_rules in payload.weights.values():
        if not isinstance(category_rules, dict) or not category_rules:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid category rules")
        for term, value in category_rules.items():
            if (
                not isinstance(term, str)
                or not term.strip()
                or len(term) > 80
                or not isinstance(value, int)
                or not 0 <= value <= 100
            ):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid rule weight")
    for value in payload.thresholds.values():
        if not isinstance(value, int | float) or not 0 <= value <= 100:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Invalid threshold")
    item = RulesetVersion(
        version=payload.version,
        weights=payload.weights,
        thresholds=payload.thresholds,
        checksum=hashlib.sha256(canonical.encode()).hexdigest(),
        change_notes=payload.change_notes,
        created_by_id=admin.id,
    )
    db.add(item)
    await db.flush()
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="classifier.ruleset.create",
            target_type="ruleset_version",
            target_id=str(item.id),
            details={"checksum": item.checksum, "version": item.version},
        )
    )
    await db.commit()
    return {"id": item.id, "version": item.version, "checksum": item.checksum}


@router.post("/classifier/versions", status_code=status.HTTP_201_CREATED)
async def create_classifier_version(
    payload: ClassifierVersionInput, _: Csrf, admin: AdminUser, db: Db
) -> dict[str, object]:
    ruleset = await db.get(RulesetVersion, payload.ruleset_version_id)
    if ruleset is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Ruleset not found")
    categories = list((await db.scalars(select(Category).order_by(Category.slug))).all())
    snapshot: dict[str, object] = {}
    for category in categories:
        policy = await db.get(AgePolicy, category.age_policy_id) if category.age_policy_id else None
        snapshot[category.slug] = (
            {
                "policy_id": str(policy.id),
                "minimum_age": policy.minimum_age,
                "maximum_age": policy.maximum_age,
                "rating": policy.rating,
                "blocked": policy.blocked,
                "review_required": policy.review_required,
                "priority": policy.priority,
            }
            if policy
            else None
        )
    canonical = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
    checksum = hashlib.sha256(canonical.encode()).hexdigest()
    policy_version = await db.scalar(
        select(PolicyVersion).where(PolicyVersion.checksum == checksum)
    )
    if policy_version is None:
        policy_version = PolicyVersion(
            version=f"{payload.version}-policy",
            snapshot=snapshot,
            checksum=checksum,
            created_by_id=admin.id,
        )
        db.add(policy_version)
        await db.flush()
    item = ClassifierVersion(
        version=payload.version,
        ruleset_version_id=ruleset.id,
        policy_version_id=policy_version.id,
        change_notes=payload.change_notes,
        created_by_id=admin.id,
    )
    db.add(item)
    await db.flush()
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="classifier.version.create",
            target_type="classifier_version",
            target_id=str(item.id),
            details={
                "ruleset_version_id": str(ruleset.id),
                "policy_version_id": str(policy_version.id),
            },
        )
    )
    await db.commit()
    return {"id": item.id, "version": item.version, "is_active": item.is_active}


@router.post("/classifier/versions/{version_id}/activate")
async def activate_classifier_version(
    version_id: uuid.UUID, _: Csrf, admin: AdminUser, db: Db
) -> dict[str, object]:
    item = await db.get(ClassifierVersion, version_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Classifier version not found")
    active = list(
        (
            await db.scalars(select(ClassifierVersion).where(ClassifierVersion.is_active.is_(True)))
        ).all()
    )
    for current in active:
        current.is_active = False
    await db.flush()
    item.is_active = True
    item.activated_by_id = admin.id
    item.activated_at = datetime.now(UTC)
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="classifier.version.activate",
            target_type="classifier_version",
            target_id=str(item.id),
            details={"replaced": [str(current.id) for current in active]},
        )
    )
    await db.commit()
    return {"id": item.id, "version": item.version, "is_active": True}


@router.post("/pilots", status_code=status.HTTP_201_CREATED)
async def create_pilot(payload: PilotInput, _: Csrf, admin: AdminUser, db: Db) -> dict[str, object]:
    if payload.size not in {100, 1_000, 10_000}:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Pilot size is not permitted")
    version = (
        await db.get(ClassifierVersion, payload.classifier_version_id)
        if payload.classifier_version_id
        else await db.scalar(select(ClassifierVersion).where(ClassifierVersion.is_active.is_(True)))
    )
    if version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No classifier version available")
    available = int(
        await db.scalar(
            select(func.count())
            .select_from(Website)
            .where(
                Website.tranco_rank >= payload.rank_start,
                Website.tranco_rank < payload.rank_start + payload.size,
            )
        )
        or 0
    )
    estimate, digest = pilot_estimate(
        size=payload.size,
        pending=available,
        capacity=payload.capacity_limit,
        browser_rate=0.20,
        ai_rate=0.05,
        static_ms=500,
        browser_ms=15_000,
        ai_ms=5_000,
        ai_cost_microunits=2_000,
    )
    pilot = PilotRun(
        requested_by_id=admin.id,
        classifier_version_id=version.id,
        size=payload.size,
        rank_start=payload.rank_start,
        dry_run=payload.dry_run,
        capacity_limit=payload.capacity_limit,
        status=PilotStatus.COMPLETED if payload.dry_run else PilotStatus.DRAFT,
        estimate=estimate,
        estimate_hash=digest,
        completed_at=datetime.now(UTC) if payload.dry_run else None,
    )
    db.add(pilot)
    await db.flush()
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="pilot.dry_run" if payload.dry_run else "pilot.create",
            target_type="pilot_run",
            target_id=str(pilot.id),
            details={"size": pilot.size, "estimate_hash": digest},
        )
    )
    await db.commit()
    return {
        "id": pilot.id,
        "status": pilot.status,
        "dry_run": pilot.dry_run,
        "estimate": estimate,
        "estimate_hash": digest,
        "jobs_dispatched": 0,
    }


@router.get("/pilots")
async def list_pilots(_: CurrentUser, db: Db) -> list[dict[str, object]]:
    pilots = list((await db.scalars(select(PilotRun).order_by(PilotRun.created_at.desc()))).all())
    return [
        {
            "id": item.id,
            "size": item.size,
            "rank_start": item.rank_start,
            "dry_run": item.dry_run,
            "status": item.status,
            "capacity_limit": item.capacity_limit,
            "queued_count": item.queued_count,
            "processed_count": item.processed_count,
            "failed_count": item.failed_count,
            "estimate": item.estimate,
            "estimate_hash": item.estimate_hash,
        }
        for item in pilots
    ]


@router.get("/pilots/{pilot_id}")
async def pilot_progress(pilot_id: uuid.UUID, _: CurrentUser, db: Db) -> dict[str, object]:
    item = await db.get(PilotRun, pilot_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pilot not found")
    count_rows = (
        await db.execute(
            select(PilotItem.status, func.count())
            .where(PilotItem.pilot_id == item.id)
            .group_by(PilotItem.status)
        )
    ).all()
    counts: dict[str, int] = {str(row[0]): int(row[1]) for row in count_rows}
    return {
        "id": item.id,
        "status": item.status,
        "size": item.size,
        "dry_run": item.dry_run,
        "capacity_limit": item.capacity_limit,
        "progress": counts,
        "estimate": item.estimate,
        "estimate_hash": item.estimate_hash,
    }


async def _dispatch_pilot_batch(db: Db, pilot: PilotRun, admin: User) -> int:
    existing_ids = select(PilotItem.website_id).where(PilotItem.pilot_id == pilot.id)
    websites = list(
        (
            await db.scalars(
                select(Website)
                .where(
                    Website.tranco_rank >= pilot.rank_start,
                    Website.tranco_rank < pilot.rank_start + pilot.size,
                    Website.id.not_in(existing_ids),
                )
                .order_by(Website.tranco_rank)
                .limit(pilot.capacity_limit)
            )
        ).all()
    )
    runs: list[ClassificationRun] = []
    for website in websites:
        run = ClassificationRun(
            website_id=website.id,
            requested_by_id=admin.id,
            queue_name=QueueName.STANDARD,
            priority=3,
            task_id=str(uuid.uuid4()),
            classifier_version_id=pilot.classifier_version_id,
        )
        db.add(run)
        await db.flush()
        db.add(
            PilotItem(
                pilot_id=pilot.id,
                website_id=website.id,
                classification_run_id=run.id,
                status="queued",
            )
        )
        runs.append(run)
    pilot.queued_count += len(runs)
    pilot.status = PilotStatus.RUNNING
    pilot.started_at = pilot.started_at or datetime.now(UTC)
    await db.commit()
    for run in runs:
        await dispatch_run(run)
    return len(runs)


@router.post("/pilots/{pilot_id}/{action}")
async def control_pilot(
    pilot_id: uuid.UUID, action: str, _: Csrf, admin: AdminUser, db: Db
) -> dict[str, object]:
    pilot = await db.scalar(select(PilotRun).where(PilotRun.id == pilot_id).with_for_update())
    if pilot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pilot not found")
    if pilot.dry_run:
        raise HTTPException(status.HTTP_409_CONFLICT, "Dry-runs dispatch no jobs")
    dispatched = 0
    if action in {"start", "resume"} and pilot.status in {
        PilotStatus.DRAFT,
        PilotStatus.PAUSED,
        PilotStatus.QUEUED,
    }:
        dispatched = await _dispatch_pilot_batch(db, pilot, admin)
    elif action == "pause" and pilot.status == PilotStatus.RUNNING:
        pilot.status = PilotStatus.PAUSED
        await db.commit()
    elif action == "cancel" and pilot.status not in {
        PilotStatus.CANCELLED,
        PilotStatus.COMPLETED,
    }:
        pilot.status = PilotStatus.CANCELLED
        pending = list(
            (
                await db.scalars(
                    select(PilotItem).where(
                        PilotItem.pilot_id == pilot.id,
                        PilotItem.status.in_(("pending", "queued")),
                    )
                )
            ).all()
        )
        for item in pending:
            item.status = "cancelled"
            if item.classification_run_id:
                run = await db.get(ClassificationRun, item.classification_run_id)
                if run:
                    run.cancel_requested = True
        await db.commit()
    else:
        raise HTTPException(status.HTTP_409_CONFLICT, "Invalid pilot state transition")
    db.add(
        AuditLog(
            actor_id=admin.id,
            action=f"pilot.{action}",
            target_type="pilot_run",
            target_id=str(pilot.id),
            details={"dispatched": dispatched},
        )
    )
    await db.commit()
    return {"id": pilot.id, "status": pilot.status, "dispatched": dispatched}


@router.get("/pilots/{pilot_id}/export")
async def export_pilot(pilot_id: uuid.UUID, _: CurrentUser, db: Db) -> Response:
    pilot = await db.get(PilotRun, pilot_id)
    if pilot is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Pilot not found")
    payload = {
        "id": str(pilot.id),
        "size": pilot.size,
        "rank_start": pilot.rank_start,
        "dry_run": pilot.dry_run,
        "status": pilot.status.value,
        "classifier_version_id": str(pilot.classifier_version_id),
        "estimate": pilot.estimate,
        "estimate_hash": pilot.estimate_hash,
    }
    return Response(
        json.dumps(payload, sort_keys=True),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="pilot-{pilot.id}.json"'},
    )


@router.get("/manual-reviews")
async def list_manual_reviews(_: CurrentUser, db: Db) -> list[dict[str, object]]:
    cases = list(
        (
            await db.scalars(
                select(ManualReviewCase).order_by(ManualReviewCase.created_at.desc()).limit(500)
            )
        ).all()
    )
    return [
        {
            "id": item.id,
            "website_id": item.website_id,
            "example_id": item.example_id,
            "classification_run_id": item.classification_run_id,
            "status": item.status,
            "reason": item.reason,
            "assigned_to_id": item.assigned_to_id,
            "final_category_id": item.final_category_id,
            "final_age_policy_id": item.final_age_policy_id,
            "locked": item.locked,
            "created_at": item.created_at,
        }
        for item in cases
    ]


@router.post("/manual-reviews/{case_id}/claim")
async def claim_manual_review(
    case_id: uuid.UUID, _: Csrf, admin: AdminUser, db: Db
) -> dict[str, object]:
    item = await db.scalar(
        select(ManualReviewCase).where(ManualReviewCase.id == case_id).with_for_update()
    )
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review case not found")
    if item.locked or item.status not in {ReviewStatus.PENDING, ReviewStatus.DISAGREEMENT}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Review case cannot be claimed")
    item.assigned_to_id = admin.id
    item.status = ReviewStatus.ASSIGNED
    await db.commit()
    return {"id": item.id, "status": item.status, "assigned_to_id": item.assigned_to_id}


@router.post("/manual-reviews/{case_id}/decision")
async def decide_manual_review(
    case_id: uuid.UUID,
    payload: ReviewDecisionInput,
    _: Csrf,
    admin: AdminUser,
    db: Db,
) -> dict[str, object]:
    item = await db.scalar(
        select(ManualReviewCase).where(ManualReviewCase.id == case_id).with_for_update()
    )
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review case not found")
    if item.locked:
        raise HTTPException(status.HTTP_409_CONFLICT, "Review case is locked")
    if payload.action != "reject" and payload.category_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Category is required")
    if payload.category_id and await db.get(Category, payload.category_id) is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Category not found")
    decision = ManualReviewDecision(
        case_id=item.id,
        reviewer_id=admin.id,
        action=payload.action,
        category_id=payload.category_id,
        age_policy_id=payload.age_policy_id,
        notes=payload.notes,
    )
    db.add(decision)
    item.final_category_id = payload.category_id
    item.final_age_policy_id = payload.age_policy_id
    item.status = ReviewStatus.REJECTED if payload.action == "reject" else ReviewStatus.RESOLVED
    item.resolved_at = datetime.now(UTC)
    db.add(
        AuditLog(
            actor_id=admin.id,
            action=f"manual_review.{payload.action}",
            target_type="manual_review_case",
            target_id=str(item.id),
            details={
                "category_id": str(payload.category_id) if payload.category_id else None,
                "age_policy_id": str(payload.age_policy_id) if payload.age_policy_id else None,
            },
        )
    )
    await db.commit()
    return {"id": item.id, "status": item.status}


@router.post("/manual-reviews/{case_id}/lock")
async def lock_manual_review(
    case_id: uuid.UUID, _: Csrf, admin: AdminUser, db: Db
) -> dict[str, object]:
    item = await db.get(ManualReviewCase, case_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review case not found")
    if item.status not in {ReviewStatus.RESOLVED, ReviewStatus.REJECTED}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only decided cases can be locked")
    item.locked = True
    item.status = ReviewStatus.LOCKED
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="manual_review.lock",
            target_type="manual_review_case",
            target_id=str(item.id),
            details={},
        )
    )
    await db.commit()
    return {"id": item.id, "status": item.status, "locked": True}


@router.post("/manual-reviews/{case_id}/reopen")
async def reopen_manual_review(
    case_id: uuid.UUID, _: Csrf, admin: AdminUser, db: Db
) -> dict[str, object]:
    item = await db.get(ManualReviewCase, case_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Review case not found")
    item.locked = False
    item.status = ReviewStatus.PENDING
    item.assigned_to_id = None
    db.add(
        AuditLog(
            actor_id=admin.id,
            action="manual_review.reopen",
            target_type="manual_review_case",
            target_id=str(item.id),
            details={},
        )
    )
    await db.commit()
    return {"id": item.id, "status": item.status, "locked": False}
