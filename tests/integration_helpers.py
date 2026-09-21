from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.models.user import User, UserRole
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate
from app.services.user import UserService


def create_test_user(
    connection: Connection,
    *,
    full_name: str,
    email: str,
    password: str = "strong-password",
    role: UserRole = UserRole.ADMIN,
) -> User:
    with Session(bind=connection, expire_on_commit=False) as session:
        return UserService(UserRepository(session)).create(
            UserCreate(
                full_name=full_name,
                email=email,
                password=password,
                role=role,
            )
        )
