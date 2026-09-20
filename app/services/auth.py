from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash
from pwdlib.exceptions import PwdlibError

from app.core.config import Settings
from app.models.user import User
from app.repositories.user import DuplicateEmailError, UserRepository
from app.schemas.auth import UserRegister

password_hash = PasswordHash.recommended()
dummy_password_hash = password_hash.hash("timing-only-password")


class InvalidTokenError(Exception):
    """Raised when an access token cannot identify an authenticated user."""


class AuthService:
    def __init__(self, repository: UserRepository, settings: Settings) -> None:
        self._repository = repository
        self._settings = settings

    @staticmethod
    def normalize_email(email: str) -> str:
        return email.strip().lower()

    def register(self, data: UserRegister) -> User:
        email = self.normalize_email(str(data.email))
        if self._repository.get_by_email(email) is not None:
            raise DuplicateEmailError

        return self._repository.create(
            full_name=data.full_name,
            email=email,
            hashed_password=password_hash.hash(data.password),
        )

    def authenticate(self, email: str, password: str) -> User | None:
        user = self._repository.get_by_email(self.normalize_email(email))
        try:
            password_is_valid = password_hash.verify(
                password,
                user.hashed_password if user is not None else dummy_password_hash,
            )
        except (PwdlibError, ValueError, TypeError):
            return None
        if user is None or not user.is_active or not password_is_valid:
            return None
        return user

    def create_access_token(self, user: User) -> str:
        now = datetime.now(UTC)
        expires_at = now + timedelta(minutes=self._settings.access_token_expire_minutes)
        return jwt.encode(
            {
                "sub": str(user.id),
                "type": "access",
                "iat": now,
                "exp": expires_at,
            },
            self._settings.jwt_secret_key.get_secret_value(),
            algorithm=self._settings.jwt_algorithm,
        )

    def get_user_from_token(self, token: str) -> User:
        try:
            payload = jwt.decode(
                token,
                self._settings.jwt_secret_key.get_secret_value(),
                algorithms=[self._settings.jwt_algorithm],
                options={"require": ["sub", "type", "iat", "exp"]},
            )
            if payload["type"] != "access":
                raise InvalidTokenError
            user_id = int(payload["sub"])
            if user_id <= 0:
                raise InvalidTokenError
        except (jwt.InvalidTokenError, KeyError, TypeError, ValueError) as exc:
            raise InvalidTokenError from exc

        user = self._repository.get_by_id(user_id)
        if user is None or not user.is_active:
            raise InvalidTokenError
        return user
