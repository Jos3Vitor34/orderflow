from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.user import User, UserRole


class DuplicateEmailError(Exception):
    """Raised when a normalized e-mail already belongs to a user."""


class UserRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_by_id(self, user_id: int) -> User | None:
        return self._session.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        statement = select(User).where(func.lower(User.email) == email)
        return self._session.scalar(statement)

    def create(
        self,
        *,
        full_name: str,
        email: str,
        hashed_password: str,
        role: UserRole,
    ) -> User:
        user = User(
            full_name=full_name,
            email=email,
            hashed_password=hashed_password,
            role=role,
        )
        self._session.add(user)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise DuplicateEmailError from exc
        self._session.refresh(user)
        return user

    def list_page(self, *, offset: int, limit: int) -> tuple[list[User], int]:
        total = self._session.scalar(select(func.count()).select_from(User)) or 0
        statement = select(User).order_by(User.id.asc()).offset(offset).limit(limit)
        return list(self._session.scalars(statement).all()), total

    def lock_active_admins(self) -> list[User]:
        statement = (
            select(User)
            .where(User.role == UserRole.ADMIN, User.is_active.is_(True))
            .order_by(User.id.asc())
            .with_for_update()
        )
        return list(self._session.scalars(statement).all())

    def update(self, user: User, changes: dict[str, object]) -> User:
        for field, value in changes.items():
            setattr(user, field, value)
        self._session.commit()
        self._session.refresh(user)
        return user
