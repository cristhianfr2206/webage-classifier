import pytest
from pydantic import ValidationError

from app.schemas import AgePolicyInput, CategoryInput


def test_category_rejects_unsafe_slug() -> None:
    with pytest.raises(ValidationError):
        CategoryInput(name="Unsafe", slug="../unsafe")


def test_age_policy_rejects_invalid_bounds() -> None:
    policy = AgePolicyInput(name="Invalid", minimum_age=18, maximum_age=12)
    with pytest.raises(ValueError):
        policy.validate_range()
