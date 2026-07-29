import uuid

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
