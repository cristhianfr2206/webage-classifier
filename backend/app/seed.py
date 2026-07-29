import asyncio

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal, engine
from app.models import AgePolicy, Category, Role, User
from app.security import hash_password


async def seed() -> None:
    settings = get_settings()
    async with SessionLocal() as db:
        email = str(settings.initial_admin_email).lower()
        if await db.scalar(select(User).where(User.email == email)) is None:
            db.add(
                User(
                    email=email,
                    password_hash=hash_password(settings.initial_admin_password),
                    role=Role.ADMIN,
                )
            )
        if await db.scalar(select(Category).limit(1)) is None:
            db.add_all(
                [
                    Category(name="Education", slug="education", description="Learning resources"),
                    Category(
                        name="Entertainment", slug="entertainment", description="Media and games"
                    ),
                    Category(name="Social", slug="social", description="Social platforms"),
                ]
            )
        if await db.scalar(select(AgePolicy).limit(1)) is None:
            db.add_all(
                [
                    AgePolicy(name="Children", minimum_age=0, maximum_age=12),
                    AgePolicy(name="Teen", minimum_age=13, maximum_age=17),
                    AgePolicy(name="Adult", minimum_age=18, maximum_age=120),
                ]
            )
        await db.flush()
        policies = {policy.name: policy for policy in (await db.scalars(select(AgePolicy))).all()}
        category_policy = {
            "education": "Children",
            "entertainment": "Teen",
            "social": "Teen",
        }
        for category in (await db.scalars(select(Category))).all():
            policy = policies.get(category_policy.get(category.slug, ""))
            if category.age_policy_id is None and policy is not None:
                category.age_policy_id = policy.id
        await db.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
