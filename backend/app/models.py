import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


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
    run: Mapped[ClassificationRun] = relationship()


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
    status: Mapped[ReviewStatus] = mapped_column(
        Enum(ReviewStatus, name="review_status"), default=ReviewStatus.PENDING, index=True
    )
    reason: Mapped[str] = mapped_column(String(500))
    assigned_to_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    final_category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id"))
    final_age_policy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("age_policies.id"))
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    __table_args__ = (UniqueConstraint("pilot_id", "website_id", name="uq_pilot_item"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pilot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("pilot_runs.id", ondelete="CASCADE"), index=True
    )
    website_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("websites.id"))
    classification_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("classification_runs.id")
    )
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
