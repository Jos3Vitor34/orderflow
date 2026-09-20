from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.user import User


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

    def create(self, *, full_name: str, email: str, hashed_password: str) -> User:
        user = User(
            full_name=full_name,
            email=email,
            hashed_password=hashed_password,
        )
        self._session.add(user)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise DuplicateEmailError from exc
        self._session.refresh(user)
        return user
