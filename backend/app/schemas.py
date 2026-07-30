import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: EmailStr
    role: str


class CategoryInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=80)
    description: str = Field(default="", max_length=500)
    age_policy_id: uuid.UUID | None = None

    @field_validator("name", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class CategoryResponse(CategoryInput):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


class AgePolicyInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    minimum_age: int = Field(ge=0, le=120)
    maximum_age: int = Field(ge=0, le=120)
    description: str = Field(default="", max_length=500)
    is_active: bool = True
    rating: str = Field(default="unrated", max_length=40)
    blocked: bool = False
    review_required: bool = False
    priority: int = Field(default=0, ge=0, le=1000)

    @field_validator("name", "description")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    def validate_range(self) -> None:
        if self.minimum_age > self.maximum_age:
            raise ValueError("minimum_age must not exceed maximum_age")


class AgePolicyResponse(AgePolicyInput):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


class WebsiteCheckInput(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class WebsiteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    domain: str
    registrable_domain: str
    canonical_url: str
    tranco_rank: int | None


class ClassificationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    website_id: uuid.UUID
    status: str
    queue_name: str
    priority: int
    attempts: int
    max_attempts: int
    cancel_requested: bool
    error_code: str | None


class WebsiteCheckResponse(BaseModel):
    website: WebsiteResponse
    run: ClassificationRunResponse


class ClassificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    website_id: uuid.UUID
    run_id: uuid.UUID | None
    category_id: uuid.UUID
    age_policy_id: uuid.UUID | None
    source: str
    confidence: int
    evidence: list[dict[str, object]]
    title: str
    description: str
    final_url: str
    text_excerpt: str


class ClassificationExecutionResponse(BaseModel):
    run: ClassificationRunResponse
    classifications: list[ClassificationResponse]


class ManualOverrideInput(BaseModel):
    category_id: uuid.UUID
    age_policy_id: uuid.UUID | None = None
    reason: str = Field(min_length=3, max_length=500)


class BulkEnqueueInput(BaseModel):
    rank_start: int = Field(ge=1, le=1_000_000)
    rank_end: int = Field(ge=1, le=1_000_000)
    limit: int = Field(default=100, ge=1, le=10_000)

    def validate_range(self) -> None:
        if self.rank_start > self.rank_end:
            raise ValueError("rank_start must not exceed rank_end")


class BulkEnqueueResponse(BaseModel):
    created: int
    already_active: int
    enqueue_failed: int


class QueueStatusResponse(BaseModel):
    redis_ok: bool
    active_jobs: int
    queues: dict[str, int]


class WorkerHealthResponse(BaseModel):
    healthy: bool
    workers: list[str]


class BrowserInspectionRequest(BaseModel):
    capture_screenshot: bool = False


class BrowserInspectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    run_id: uuid.UUID
    status: str
    trigger: str
    attempts: int
    max_attempts: int
    cancel_requested: bool
    duration_ms: int | None
    rendered_final_url: str | None
    rendered_title: str
    rendered_text_sample: str
    request_count: int
    transferred_byte_count: int
    blocked_request_count: int
    failure_code: str | None
    artifact_id: str | None
    artifact_expires_at: datetime | None
    browser_version: str | None
    playwright_version: str | None


class BrowserRequestResponse(BaseModel):
    run: ClassificationRunResponse
    browser: BrowserInspectionResponse


class AIRequest(BaseModel):
    trigger: str = Field(default="admin", pattern=r"^(admin|manual_review)$")


class AIClassificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    run_id: uuid.UUID
    website_id: uuid.UUID
    status: str
    trigger: str
    provider: str
    model: str
    model_version: str
    confidence: int | None
    prompt_injection_suspected: bool
    validation_status: str
    failure_code: str | None
    evidence: list[str]
    duration_ms: int | None
    usage_metadata: dict[str, object]
    manual_review_required: bool
    promoted: bool


class AIRequestResponse(BaseModel):
    run: ClassificationRunResponse
    ai: AIClassificationResponse


class AISettingsResponse(BaseModel):
    enabled: bool
    provider: str
    model: str
    confidence_threshold: float
    conflict_threshold: float
    screenshot_enabled: bool
    retry_limit: int
    daily_request_limit: int
    monthly_cost_limit: float


class AISettingsUpdate(BaseModel):
    enabled: bool
    provider: str = Field(pattern=r"^[a-z0-9_-]{1,80}$")
    model: str = Field(max_length=120)
    confidence_threshold: float = Field(ge=0, le=1)
    conflict_threshold: float = Field(ge=0, le=1)
    screenshot_enabled: bool = False
    retry_limit: int = Field(ge=0, le=5)
    daily_request_limit: int = Field(ge=0, le=1_000_000)
    monthly_cost_limit: float = Field(ge=0)


class AIUsageResponse(BaseModel):
    requests: int
    estimated_cost_microunits: int
