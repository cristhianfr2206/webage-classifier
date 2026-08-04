from __future__ import annotations

import enum
import json
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    inspect,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
)
from sqlalchemy.orm import (
    Session as SyncSession,
)
from sqlalchemy.types import JSON

from app.taxonomy import (
    AssessmentDisposition,
    AssessmentLabelRole,
    AssessmentLabelSource,
    FeedHandlingMode,
    ReviewDecisionState,
    ReviewDisposition,
    ReviewLabelRole,
    TaxonomyDimension,
    TaxonomyInvariantError,
    TaxonomyLabelStatus,
    TaxonomyVersionStatus,
    validate_label_assignment,
)


class Base(DeclarativeBase):
    pass


class Role(str, enum.Enum):
    ADMIN = "admin"
    VIEWER = "viewer"


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    RETRYING = "retrying"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class QueueName(str, enum.Enum):
    REALTIME = "realtime"
    STANDARD = "standard"
    MAINTENANCE = "maintenance"
    BROWSER_REALTIME = "browser_realtime"
    BROWSER = "browser"
    AI_REALTIME = "ai_realtime"
    AI = "ai"


class ClassificationSource(str, enum.Enum):
    RULES = "rules"
    RENDERED = "rendered"
    SCREENSHOT = "screenshot"
    MANUAL = "manual"
    AI = "ai"


class AIStatus(str, enum.Enum):
    PENDING = "pending"
    RETRYING = "retrying"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REVIEW_REQUIRED = "review_required"


class BrowserStatus(str, enum.Enum):
    PENDING = "pending"
    RETRYING = "retrying"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EvaluationStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewStatus(str, enum.Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    DISAGREEMENT = "disagreement"
    RESOLVED = "resolved"
    REJECTED = "rejected"
    LOCKED = "locked"


class PilotStatus(str, enum.Enum):
    DRAFT = "draft"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[Role] = mapped_column(Enum(Role, name="role"), default=Role.VIEWER)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user: Mapped[User] = relationship()


class Category(Base):
    __tablename__ = "categories"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(String(500), default="")
    age_policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("age_policies.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgePolicy(Base):
    __tablename__ = "age_policies"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    minimum_age: Mapped[int] = mapped_column(Integer)
    maximum_age: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(500), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    rating: Mapped[str] = mapped_column(String(40), default="unrated")
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str] = mapped_column(String(80))
    target_id: Mapped[str] = mapped_column(String(100))
    details: Mapped[dict[str, object]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),  # type: ignore[no-untyped-call]
        default=dict,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Website(Base):
    __tablename__ = "websites"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    domain: Mapped[str] = mapped_column(String(253), unique=True, index=True)
    registrable_domain: Mapped[str] = mapped_column(String(253), index=True)
    canonical_url: Mapped[str] = mapped_column(String(2048))
    tranco_rank: Mapped[int | None] = mapped_column(Integer, index=True)
    pilot_eligible: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TrancoImport(Base):
    __tablename__ = "tranco_imports"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_name: Mapped[str] = mapped_column(String(255))
    requested_limit: Mapped[int | None] = mapped_column(Integer)
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClassificationRun(Base):
    __tablename__ = "classification_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    website_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("websites.id", ondelete="CASCADE"), index=True
    )
    requested_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    classifier_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("classifier_versions.id"),
        server_default="00000000-0000-4000-8000-000000000603",
    )
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status"), default=RunStatus.PENDING, index=True
    )
    queue_name: Mapped[QueueName] = mapped_column(
        Enum(QueueName, name="queue_name"), default=QueueName.STANDARD
    )
    priority: Mapped[int] = mapped_column(Integer, default=5)
    task_id: Mapped[str | None] = mapped_column(String(50), unique=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=4)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    website: Mapped[Website] = relationship()


class WebsiteClassification(Base):
    __tablename__ = "website_classifications"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    website_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("websites.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("classification_runs.id", ondelete="SET NULL"), index=True
    )
    category_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("categories.id"))
    age_policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("age_policies.id"))
    source: Mapped[ClassificationSource] = mapped_column(
        Enum(ClassificationSource, name="classification_source")
    )
    confidence: Mapped[int] = mapped_column(Integer)
    evidence: Mapped[list[dict[str, object]]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),  # type: ignore[no-untyped-call]
        default=list,
    )
    title: Mapped[str] = mapped_column(String(500), default="")
    description: Mapped[str] = mapped_column(String(1000), default="")
    final_url: Mapped[str] = mapped_column(String(2048), default="")
    text_excerpt: Mapped[str] = mapped_column(String(2000), default="")
    overridden_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class BrowserInspection(Base):
    __tablename__ = "browser_inspections"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classification_runs.id", ondelete="CASCADE"), unique=True, index=True
    )
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("classification_runs.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[BrowserStatus] = mapped_column(
        Enum(BrowserStatus, name="browser_status"), default=BrowserStatus.PENDING, index=True
    )
    trigger: Mapped[str] = mapped_column(String(40))
    task_id: Mapped[str | None] = mapped_column(String(50), unique=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    rendered_final_url: Mapped[str | None] = mapped_column(String(2048))
    rendered_title: Mapped[str] = mapped_column(String(500), default="")
    rendered_text_sample: Mapped[str] = mapped_column(String(50000), default="")
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    transferred_byte_count: Mapped[int] = mapped_column(Integer, default=0)
    blocked_request_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    artifact_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    artifact_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    browser_version: Mapped[str | None] = mapped_column(String(80))
    playwright_version: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    run: Mapped[ClassificationRun] = relationship(foreign_keys=[run_id])


class AIClassification(Base):
    __tablename__ = "ai_classifications"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classification_runs.id", ondelete="CASCADE"), unique=True, index=True
    )
    website_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("websites.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[AIStatus] = mapped_column(
        Enum(AIStatus, name="ai_status"), default=AIStatus.PENDING, index=True
    )
    trigger: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(80), index=True)
    model: Mapped[str] = mapped_column(String(120))
    model_version: Mapped[str] = mapped_column(String(80), default="")
    task_id: Mapped[str | None] = mapped_column(String(50), unique=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    request_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    input_hash: Mapped[str | None] = mapped_column(String(64))
    input_character_count: Mapped[int] = mapped_column(Integer, default=0)
    input_token_count: Mapped[int | None] = mapped_column(Integer)
    output_token_count: Mapped[int | None] = mapped_column(Integer)
    provider_request_id: Mapped[str | None] = mapped_column(String(120))
    confidence: Mapped[int | None] = mapped_column(Integer)
    prompt_injection_suspected: Mapped[bool] = mapped_column(Boolean, default=False)
    validation_status: Mapped[str] = mapped_column(String(40), default="pending")
    failure_code: Mapped[str | None] = mapped_column(String(80))
    failure_message: Mapped[str | None] = mapped_column(String(300))
    usage_metadata: Mapped[dict[str, object]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),  # type: ignore[no-untyped-call]
        default=dict,
    )
    estimated_cost_microunits: Mapped[int | None] = mapped_column(Integer)
    primary_category: Mapped[str | None] = mapped_column(String(80))
    secondary_categories: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),  # type: ignore[no-untyped-call]
        default=list,
    )
    evidence: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),  # type: ignore[no-untyped-call]
        default=list,
    )
    intended_audience: Mapped[str] = mapped_column(String(200), default="")
    uncertainty_reason: Mapped[str] = mapped_column(String(500), default="")
    manual_review_required: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    promoted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    run: Mapped[ClassificationRun] = relationship()


class AIConfiguration(Base):
    __tablename__ = "ai_configuration"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    provider: Mapped[str] = mapped_column(String(80), default="disabled")
    model: Mapped[str] = mapped_column(String(120), default="")
    confidence_threshold: Mapped[int] = mapped_column(Integer, default=75)
    conflict_threshold: Mapped[int] = mapped_column(Integer, default=10)
    screenshot_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_limit: Mapped[int] = mapped_column(Integer, default=2)
    daily_request_limit: Mapped[int] = mapped_column(Integer, default=100)
    monthly_cost_limit_microunits: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RulesetVersion(Base):
    __tablename__ = "ruleset_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(80), unique=True)
    weights: Mapped[dict[str, object]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),  # type: ignore[no-untyped-call]
        default=dict,
    )
    thresholds: Mapped[dict[str, object]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),  # type: ignore[no-untyped-call]
        default=dict,
    )
    checksum: Mapped[str] = mapped_column(String(64), unique=True)
    change_notes: Mapped[str] = mapped_column(String(1000), default="")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PolicyVersion(Base):
    __tablename__ = "policy_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(80), unique=True)
    snapshot: Mapped[dict[str, object]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),  # type: ignore[no-untyped-call]
        default=dict,
    )
    checksum: Mapped[str] = mapped_column(String(64), unique=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClassifierVersion(Base):
    __tablename__ = "classifier_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(80), unique=True)
    ruleset_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ruleset_versions.id"))
    policy_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("policy_versions.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    change_notes: Mapped[str] = mapped_column(String(1000), default="")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    activated_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaxonomyVersion(Base):
    """An immutable published taxonomy snapshot."""

    __tablename__ = "taxonomy_versions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(80), unique=True)
    status: Mapped[TaxonomyVersionStatus] = mapped_column(
        Enum(TaxonomyVersionStatus, name="taxonomy_version_status"),
        default=TaxonomyVersionStatus.DRAFT,
        index=True,
    )
    checksum: Mapped[str] = mapped_column(String(64), unique=True)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("taxonomy_versions.id", ondelete="RESTRICT"), index=True
    )
    change_notes: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parent_version: Mapped[TaxonomyVersion | None] = relationship(remote_side=[id])


class TaxonomyLabel(Base):
    __tablename__ = "taxonomy_labels"
    __table_args__ = (
        UniqueConstraint("taxonomy_version_id", "slug", name="uq_taxonomy_label_version_slug"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    taxonomy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("taxonomy_versions.id", ondelete="CASCADE"), index=True
    )
    dimension: Mapped[TaxonomyDimension] = mapped_column(
        Enum(TaxonomyDimension, name="taxonomy_dimension"), index=True
    )
    slug: Mapped[str] = mapped_column(String(80))
    display_name: Mapped[str] = mapped_column(String(120))
    definition: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[TaxonomyLabelStatus] = mapped_column(
        Enum(TaxonomyLabelStatus, name="taxonomy_label_status"),
        default=TaxonomyLabelStatus.ACTIVE,
        index=True,
    )
    default_review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    default_block_recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    taxonomy_version: Mapped[TaxonomyVersion] = relationship()


class FeedLabelMapping(Base):
    """A source-category mapping scoped to one immutable taxonomy snapshot.

    Feed mappings deliberately do not carry a policy, security verdict, or
    enforcement result. They are a provenance-preserving input for later
    assessment work only.
    """

    __tablename__ = "feed_label_mappings"
    __table_args__ = (
        UniqueConstraint(
            "taxonomy_version_id",
            "source_name",
            "source_category",
            "mapping_version",
            name="uq_feed_label_mapping_version_source_category",
        ),
        CheckConstraint("confidence BETWEEN 0 AND 100", name="ck_feed_label_mapping_confidence"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    taxonomy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("taxonomy_versions.id", ondelete="CASCADE"), index=True
    )
    source_name: Mapped[str] = mapped_column(String(80), index=True)
    source_category: Mapped[str] = mapped_column(String(120))
    target_label_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("taxonomy_labels.id", ondelete="RESTRICT"), index=True
    )
    handling_mode: Mapped[FeedHandlingMode] = mapped_column(
        Enum(FeedHandlingMode, name="feed_handling_mode"), index=True
    )
    confidence: Mapped[int] = mapped_column(Integer)
    review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    mapping_version: Mapped[str] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    taxonomy_version: Mapped[TaxonomyVersion] = relationship()
    target_label: Mapped[TaxonomyLabel | None] = relationship()


class ClassificationAssessment(Base):
    """A future normalized projection of one classification run.

    Phase 1 intentionally does not populate this table or alter legacy
    ``WebsiteClassification`` records.
    """

    __tablename__ = "classification_assessments"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classification_runs.id", ondelete="CASCADE"), unique=True, index=True
    )
    website_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("websites.id", ondelete="CASCADE"), index=True
    )
    taxonomy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("taxonomy_versions.id", ondelete="RESTRICT"), index=True
    )
    terminal_disposition: Mapped[AssessmentDisposition] = mapped_column(
        Enum(AssessmentDisposition, name="assessment_disposition"),
        default=AssessmentDisposition.UNRESOLVED,
        index=True,
    )
    primary_content_label_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("taxonomy_labels.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    taxonomy_version: Mapped[TaxonomyVersion] = relationship()
    primary_content_label: Mapped[TaxonomyLabel | None] = relationship(
        foreign_keys=[primary_content_label_id]
    )


class ClassificationAssessmentLabel(Base):
    __tablename__ = "classification_assessment_labels"
    __table_args__ = (
        UniqueConstraint("assessment_id", "label_id", name="uq_assessment_label"),
        CheckConstraint("confidence BETWEEN 0 AND 100", name="ck_assessment_label_confidence"),
        Index(
            "uq_assessment_primary_content_label",
            "assessment_id",
            unique=True,
            postgresql_where=text("role = 'PRIMARY'"),
            sqlite_where=text("role = 'PRIMARY'"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classification_assessments.id", ondelete="CASCADE"), index=True
    )
    label_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("taxonomy_labels.id", ondelete="RESTRICT")
    )
    dimension: Mapped[TaxonomyDimension] = mapped_column(
        Enum(TaxonomyDimension, name="taxonomy_dimension")
    )
    role: Mapped[AssessmentLabelRole] = mapped_column(
        Enum(AssessmentLabelRole, name="assessment_label_role"),
        default=AssessmentLabelRole.EVIDENCE,
    )
    confidence: Mapped[int] = mapped_column(Integer)
    source: Mapped[AssessmentLabelSource] = mapped_column(
        Enum(AssessmentLabelSource, name="assessment_label_source")
    )
    evidence: Mapped[str] = mapped_column(String(2000), default="")
    provenance: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    assessment: Mapped[ClassificationAssessment] = relationship()
    label: Mapped[TaxonomyLabel] = relationship()


class AssessmentPolicyDecision(Base):
    __tablename__ = "assessment_policy_decisions"
    __table_args__ = (
        CheckConstraint(
            "minimum_age IS NULL OR minimum_age BETWEEN 0 AND 120",
            name="ck_assessment_policy_minimum_age",
        ),
        CheckConstraint(
            "maximum_age IS NULL OR maximum_age BETWEEN 0 AND 120",
            name="ck_assessment_policy_maximum_age",
        ),
        CheckConstraint(
            "minimum_age IS NULL OR maximum_age IS NULL OR minimum_age <= maximum_age",
            name="ck_assessment_policy_age_range",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classification_assessments.id", ondelete="CASCADE"), unique=True, index=True
    )
    minimum_age: Mapped[int | None] = mapped_column(Integer)
    maximum_age: Mapped[int | None] = mapped_column(Integer)
    rating: Mapped[str | None] = mapped_column(String(40))
    review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    block_recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="RESTRICT"), index=True
    )
    reasons: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    assessment: Mapped[ClassificationAssessment] = relationship()


def _taxonomy_version_for_label(
    session: SyncSession, label: TaxonomyLabel
) -> TaxonomyVersion | None:
    if label.taxonomy_version is not None:
        return label.taxonomy_version
    return session.get(TaxonomyVersion, label.taxonomy_version_id)


def _taxonomy_version_for_feed_mapping(
    session: SyncSession, mapping: FeedLabelMapping
) -> TaxonomyVersion | None:
    if mapping.taxonomy_version is not None:
        return mapping.taxonomy_version
    return session.get(TaxonomyVersion, mapping.taxonomy_version_id)


@event.listens_for(SyncSession, "before_flush")
def enforce_taxonomy_orm_invariants(
    session: SyncSession, _flush_context: object, _instances: object
) -> None:
    """Mirror the PostgreSQL triggers for model-level and SQLite test safety."""

    for version in session.deleted:
        if (
            isinstance(version, TaxonomyVersion)
            and version.status is TaxonomyVersionStatus.PUBLISHED
        ):
            raise TaxonomyInvariantError("published_taxonomy_version_is_immutable")

    for version in session.dirty:
        if not isinstance(version, TaxonomyVersion):
            continue
        status_history = inspect(version).attrs.status.history
        if (
            TaxonomyVersionStatus.PUBLISHED in status_history.deleted
            or TaxonomyVersionStatus.PUBLISHED in status_history.unchanged
        ):
            raise TaxonomyInvariantError("published_taxonomy_version_is_immutable")

    for label in (*session.new, *session.dirty, *session.deleted):
        if not isinstance(label, TaxonomyLabel):
            continue
        taxonomy_version = _taxonomy_version_for_label(session, label)
        if (
            taxonomy_version is not None
            and taxonomy_version.status is TaxonomyVersionStatus.PUBLISHED
        ):
            raise TaxonomyInvariantError("published_taxonomy_labels_are_immutable")

    for mapping in (*session.new, *session.dirty, *session.deleted):
        if not isinstance(mapping, FeedLabelMapping):
            continue
        taxonomy_version = _taxonomy_version_for_feed_mapping(session, mapping)
        if (
            taxonomy_version is not None
            and taxonomy_version.status is TaxonomyVersionStatus.PUBLISHED
        ):
            raise TaxonomyInvariantError("published_feed_mappings_are_immutable")

    for mapping in (*session.new, *session.dirty):
        if not isinstance(mapping, FeedLabelMapping):
            continue
        target_label = mapping.target_label
        if target_label is None and mapping.target_label_id is not None:
            target_label = session.get(TaxonomyLabel, mapping.target_label_id)
        requires_target = mapping.handling_mode in {
            FeedHandlingMode.FINAL_CANDIDATE,
            FeedHandlingMode.SUPPORTING_EVIDENCE,
            FeedHandlingMode.HIGH_RISK_EVIDENCE,
        }
        if requires_target and target_label is None:
            raise TaxonomyInvariantError("feed_mapping_target_required")
        if not requires_target and target_label is not None:
            raise TaxonomyInvariantError("feed_mapping_target_forbidden")
        if target_label is None:
            continue
        if target_label.taxonomy_version_id != mapping.taxonomy_version_id:
            raise TaxonomyInvariantError("feed_mapping_target_taxonomy_mismatch")
        if target_label.dimension is not TaxonomyDimension.CONTENT:
            raise TaxonomyInvariantError("feed_mapping_target_must_be_content")

    for assessment in (*session.new, *session.dirty):
        if not isinstance(assessment, ClassificationAssessment):
            continue
        primary_label = assessment.primary_content_label
        if primary_label is None and assessment.primary_content_label_id is not None:
            primary_label = session.get(TaxonomyLabel, assessment.primary_content_label_id)
        if primary_label is None:
            continue
        if primary_label.dimension is not TaxonomyDimension.CONTENT:
            raise TaxonomyInvariantError("assessment_primary_label_must_be_content")
        if primary_label.taxonomy_version_id != assessment.taxonomy_version_id:
            raise TaxonomyInvariantError("assessment_primary_label_taxonomy_mismatch")

    for association in (*session.new, *session.dirty):
        if not isinstance(association, ClassificationAssessmentLabel):
            continue
        label = association.label
        if label is None:
            label = session.get(TaxonomyLabel, association.label_id)
        if label is None:
            continue
        validate_label_assignment(
            label_dimension=label.dimension,
            assignment_dimension=association.dimension,
            role=association.role,
        )
        assessment = association.assessment
        if assessment is None:
            assessment = session.get(ClassificationAssessment, association.assessment_id)
        if assessment is not None and label.taxonomy_version_id != assessment.taxonomy_version_id:
            raise TaxonomyInvariantError("assessment_label_taxonomy_mismatch")

    for snapshot in (*session.dirty, *session.deleted):
        if not isinstance(snapshot, ManualReviewEvidenceSnapshot):
            continue
        if inspect(snapshot).persistent:
            raise TaxonomyInvariantError("review_evidence_snapshot_is_immutable")

    for snapshot in session.new:
        if not isinstance(snapshot, ManualReviewEvidenceSnapshot):
            continue
        encoded = json.dumps(snapshot.payload, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode("utf-8")) != snapshot.payload_size_bytes:
            raise TaxonomyInvariantError("review_evidence_snapshot_size_mismatch")
        if len(encoded.encode("utf-8")) > 65_536:
            raise TaxonomyInvariantError("review_evidence_snapshot_too_large")

    for case in (*session.new, *session.dirty):
        if not isinstance(case, ManualReviewCase):
            continue
        # Legacy rows and SQLAlchemy's Python-side defaults can present as
        # ``None`` until their first flush; the database default is 0.
        if case.revision is not None and case.revision < 0:
            raise TaxonomyInvariantError("review_revision_must_be_nonnegative")
        if case.locked and case.disposition is ReviewDisposition.IN_REVIEW:
            raise TaxonomyInvariantError("locked_review_case_cannot_be_in_review")
        locked_history = inspect(case).attrs.locked.history
        was_locked = bool(locked_history.deleted and locked_history.deleted[0])
        if was_locked and not case.locked:
            allowed = session.info.get("review_reopen_case_ids", set())
            if case.id not in allowed:
                raise TaxonomyInvariantError("locked_review_case_requires_privileged_reopen")
        if inspect(case).persistent and was_locked and case.locked:
            changed = {attr.key for attr in inspect(case).attrs if attr.history.has_changes()}
            if changed - {"updated_at"}:
                raise TaxonomyInvariantError("locked_review_case_is_immutable")

    for decision in (*session.new, *session.dirty):
        if not isinstance(decision, ManualReviewLabelDecision):
            continue
        label = session.get(TaxonomyLabel, decision.taxonomy_label_id)
        case = session.get(ManualReviewCase, decision.review_case_id)
        if label is None or case is None:
            continue
        if case.locked:
            raise TaxonomyInvariantError("locked_review_case_is_immutable")
        if case.taxonomy_version_id != decision.taxonomy_version_id:
            raise TaxonomyInvariantError("review_decision_taxonomy_mismatch")
        if label.taxonomy_version_id != decision.taxonomy_version_id:
            raise TaxonomyInvariantError("review_label_taxonomy_mismatch")
        if label.dimension is not decision.dimension:
            raise TaxonomyInvariantError("review_label_dimension_mismatch")
        if decision.role is ReviewLabelRole.PRIMARY:
            if decision.state is not ReviewDecisionState.ACCEPTED:
                raise TaxonomyInvariantError("review_primary_must_be_accepted")
            if label.dimension is not TaxonomyDimension.CONTENT:
                raise TaxonomyInvariantError("review_primary_must_be_active_content")
            if label.status is not TaxonomyLabelStatus.ACTIVE:
                raise TaxonomyInvariantError("review_primary_must_be_active_content")


class EvaluationDataset(Base):
    __tablename__ = "evaluation_datasets"
    __table_args__ = (UniqueConstraint("name", "version", name="uq_evaluation_dataset_version"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), index=True)
    version: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(20), default="1")
    prior_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("evaluation_datasets.id", ondelete="RESTRICT")
    )
    checksum: Mapped[str] = mapped_column(String(64), unique=True)
    source_format: Mapped[str] = mapped_column(String(10))
    change_notes: Mapped[str] = mapped_column(String(1000), default="")
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    published: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluationExample(Base):
    __tablename__ = "evaluation_examples"
    __table_args__ = (
        UniqueConstraint("dataset_id", "domain", name="uq_evaluation_example_domain"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_datasets.id", ondelete="CASCADE"), index=True
    )
    domain: Mapped[str] = mapped_column(String(253))
    primary_category: Mapped[str | None] = mapped_column(String(80))
    secondary_categories: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),  # type: ignore[no-untyped-call]
        default=list,
    )
    expected_age: Mapped[int | None] = mapped_column(Integer)
    expected_rating: Mapped[str | None] = mapped_column(String(40))
    expected_blocked: Mapped[bool | None] = mapped_column(Boolean)
    evidence: Mapped[dict[str, object]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),  # type: ignore[no-untyped-call]
        default=dict,
    )
    adjudicated: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HumanLabel(Base):
    __tablename__ = "human_labels"
    __table_args__ = (
        UniqueConstraint("example_id", "reviewer_id", name="uq_human_label_reviewer"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    example_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_examples.id", ondelete="CASCADE"), index=True
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    category: Mapped[str] = mapped_column(String(80))
    expected_age: Mapped[int] = mapped_column(Integer)
    notes: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    dataset_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_datasets.id"), index=True)
    classifier_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("classifier_versions.id"), index=True
    )
    requested_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[EvaluationStatus] = mapped_column(
        Enum(EvaluationStatus, name="evaluation_status"),
        default=EvaluationStatus.PENDING,
        index=True,
    )
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    metrics: Mapped[dict[str, object]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),  # type: ignore[no-untyped-call]
        default=dict,
    )
    task_id: Mapped[str | None] = mapped_column(String(50), unique=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (UniqueConstraint("run_id", "example_id", name="uq_evaluation_result"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evaluation_runs.id", ondelete="CASCADE"), index=True
    )
    example_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("evaluation_examples.id"))
    predicted_primary: Mapped[str | None] = mapped_column(String(80))
    predicted_secondary: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),  # type: ignore[no-untyped-call]
        default=list,
    )
    predicted_age: Mapped[int | None] = mapped_column(Integer)
    predicted_rating: Mapped[str | None] = mapped_column(String(40))
    predicted_blocked: Mapped[bool | None] = mapped_column(Boolean)
    confidence: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20), default="unknown")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_microunits: Mapped[int] = mapped_column(Integer, default=0)
    manual_review: Mapped[bool] = mapped_column(Boolean, default=False)
    failure_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ManualReviewCase(Base):
    __tablename__ = "manual_review_cases"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    website_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("websites.id"), index=True)
    example_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("evaluation_examples.id"))
    classification_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("classification_runs.id")
    )
    # Phase 1A fields are nullable for legacy cases.  New cases are created
    # through review_service with a taxonomy version and frozen evidence.
    taxonomy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("taxonomy_versions.id", ondelete="RESTRICT"), index=True
    )
    source_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("classification_assessments.id", ondelete="SET NULL"), index=True
    )
    evidence_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("manual_review_evidence_snapshots.id", ondelete="SET NULL"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    disposition: Mapped[ReviewDisposition | None] = mapped_column(
        Enum(ReviewDisposition, name="review_disposition"), index=True
    )
    conflict_flags: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),  # type: ignore[no-untyped-call]
        default=list,
    )
    priority: Mapped[int] = mapped_column(Integer, default=0, nullable=False, index=True)
    status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, name="review_status"), default=ReviewStatus.PENDING, index=True
    )
    reason: Mapped[str] = mapped_column(String(500))
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    claimed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    final_category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id"))
    final_age_policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("age_policies.id"))
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    locked_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lock_reason: Mapped[str] = mapped_column(String(500), default="")
    reopened_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopen_reason: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ManualReviewDecision(Base):
    __tablename__ = "manual_review_decisions"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("manual_review_cases.id", ondelete="CASCADE"), index=True
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(30))
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id"))
    age_policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("age_policies.id"))
    notes: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ManualReviewEvidenceSnapshot(Base):
    """A frozen, sanitized evidence view for one explicitly routed review case."""

    __tablename__ = "manual_review_evidence_snapshots"
    __table_args__ = (
        CheckConstraint("length(evidence_checksum) = 64", name="ck_review_snapshot_checksum"),
        CheckConstraint("payload_size_bytes BETWEEN 2 AND 65536", name="ck_review_snapshot_size"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("manual_review_cases.id", ondelete="CASCADE"), unique=True, index=True
    )
    source_classification_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("classification_runs.id", ondelete="SET NULL"), index=True
    )
    browser_inspection_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("browser_inspections.id", ondelete="SET NULL"), index=True
    )
    feed_evidence_reference: Mapped[str] = mapped_column(String(500), default="")
    source_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("classification_assessments.id", ondelete="SET NULL"), index=True
    )
    payload: Mapped[dict[str, object]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),  # type: ignore[no-untyped-call]
        default=dict,
    )
    payload_size_bytes: Mapped[int] = mapped_column(Integer)
    evidence_checksum: Mapped[str] = mapped_column(String(64), unique=True)
    provenance: Mapped[str] = mapped_column(String(1000), default="")
    payload_schema_version: Mapped[str] = mapped_column(String(20), default="1")
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ManualReviewLabelDecision(Base):
    """A reviewer-owned, version-scoped taxonomy-label decision."""

    __tablename__ = "manual_review_label_decisions"
    __table_args__ = (
        CheckConstraint(
            "reviewer_confidence BETWEEN 0 AND 100", name="ck_review_decision_confidence"
        ),
        Index(
            "uq_review_active_primary_content_decision",
            "review_case_id",
            unique=True,
            postgresql_where=text(
                "state = 'ACCEPTED' AND role = 'PRIMARY' AND superseded_at IS NULL"
            ),
            sqlite_where=text("state = 'ACCEPTED' AND role = 'PRIMARY' AND superseded_at IS NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    review_case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("manual_review_cases.id", ondelete="CASCADE"), index=True
    )
    taxonomy_label_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("taxonomy_labels.id", ondelete="RESTRICT"), index=True
    )
    taxonomy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("taxonomy_versions.id", ondelete="RESTRICT"), index=True
    )
    dimension: Mapped[TaxonomyDimension] = mapped_column(
        Enum(TaxonomyDimension, name="taxonomy_dimension")
    )
    role: Mapped[ReviewLabelRole] = mapped_column(Enum(ReviewLabelRole, name="review_label_role"))
    state: Mapped[ReviewDecisionState] = mapped_column(
        Enum(ReviewDecisionState, name="review_decision_state"), index=True
    )
    reviewer_confidence: Mapped[int] = mapped_column(Integer)
    rationale: Mapped[str] = mapped_column(String(2000), default="")
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class PilotRun(Base):
    __tablename__ = "pilot_runs"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    requested_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    classifier_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("classifier_versions.id"))
    size: Mapped[int] = mapped_column(Integer)
    rank_start: Mapped[int] = mapped_column(Integer, default=1)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[PilotStatus] = mapped_column(
        Enum(PilotStatus, name="pilot_status"), default=PilotStatus.DRAFT, index=True
    )
    capacity_limit: Mapped[int] = mapped_column(Integer, default=100)
    queued_count: Mapped[int] = mapped_column(Integer, default=0)
    processed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    estimate: Mapped[dict[str, object]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"),  # type: ignore[no-untyped-call]
        default=dict,
    )
    estimate_hash: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PilotItem(Base):
    __tablename__ = "pilot_items"
    __table_args__ = (
        UniqueConstraint("pilot_id", "website_id", name="uq_pilot_item"),
        UniqueConstraint("pilot_id", "selection_order", name="uq_pilot_item_selection_order"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pilot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pilot_runs.id", ondelete="CASCADE"), index=True
    )
    website_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("websites.id"))
    selection_order: Mapped[int] = mapped_column(Integer)
    original_tranco_rank: Mapped[int] = mapped_column(Integer)
    classification_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("classification_runs.id")
    )
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
