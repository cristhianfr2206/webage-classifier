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
