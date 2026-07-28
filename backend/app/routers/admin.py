from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.dependencies import AdminUser, Csrf, CurrentUser, Db
from app.models import AgePolicy, AuditLog, Category
from app.schemas import (
    AgePolicyInput,
    AgePolicyResponse,
    CategoryInput,
    CategoryResponse,
)

router = APIRouter(prefix="/api", tags=["administration"])


@router.get("/categories", response_model=list[CategoryResponse])
async def categories(_: CurrentUser, db: Db) -> list[Category]:
    return list((await db.scalars(select(Category).order_by(Category.name))).all())


@router.post("/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category(payload: CategoryInput, _: Csrf, admin: AdminUser, db: Db) -> Category:
    category = Category(**payload.model_dump())
    try:
        db.add(category)
        await db.flush()
        db.add(
            AuditLog(
                actor_id=admin.id,
                action="category.create",
                target_type="category",
                target_id=str(category.id),
                details={"name": category.name, "slug": category.slug},
            )
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Category name or slug already exists"
        ) from exc
    await db.refresh(category)
    return category


@router.get("/age-policies", response_model=list[AgePolicyResponse])
async def age_policies(_: CurrentUser, db: Db) -> list[AgePolicy]:
    return list((await db.scalars(select(AgePolicy).order_by(AgePolicy.minimum_age))).all())


@router.post("/age-policies", response_model=AgePolicyResponse, status_code=status.HTTP_201_CREATED)
async def create_age_policy(
    payload: AgePolicyInput, _: Csrf, admin: AdminUser, db: Db
) -> AgePolicy:
    try:
        payload.validate_range()
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    policy = AgePolicy(**payload.model_dump())
    try:
        db.add(policy)
        await db.flush()
        db.add(
            AuditLog(
                actor_id=admin.id,
                action="age_policy.create",
                target_type="age_policy",
                target_id=str(policy.id),
                details={
                    "name": policy.name,
                    "minimum_age": policy.minimum_age,
                    "maximum_age": policy.maximum_age,
                },
            )
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Age policy name already exists") from exc
    await db.refresh(policy)
    return policy
