from app.models.user import User, UserRole
from app.repositories.user import DuplicateEmailError, UserRepository
from app.schemas.user import UserCreate, UserListResponse
from app.services.auth import AuthService, hash_password


class UserNotFoundError(Exception):
    """Raised when a user identifier does not exist."""


class LastActiveAdminError(Exception):
    """Raised when a change would leave no active administrator."""


class UserService:
    def __init__(self, repository: UserRepository) -> None:
        self._repository = repository

    def create(self, data: UserCreate) -> User:
        email = AuthService.normalize_email(str(data.email))
        if self._repository.get_by_email(email) is not None:
            raise DuplicateEmailError
        return self._repository.create(
            full_name=data.full_name,
            email=email,
            hashed_password=hash_password(data.password),
            role=data.role,
        )

    def list(self, *, page: int, page_size: int) -> UserListResponse:
        users, total = self._repository.list_page(
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return UserListResponse(
            items=users,
            total=total,
            page=page,
            page_size=page_size,
            pages=(total + page_size - 1) // page_size,
        )

    def get(self, user_id: int) -> User:
        user = self._repository.get_by_id(user_id)
        if user is None:
            raise UserNotFoundError
        return user

    def update_role(self, user_id: int, role: UserRole) -> User:
        user = self.get(user_id)
        if user.role == UserRole.ADMIN and role != UserRole.ADMIN and user.is_active:
            admins = self._repository.lock_active_admins()
            if len(admins) <= 1:
                raise LastActiveAdminError
        return self._repository.update(user, {"role": role})

    def update_active(self, user_id: int, is_active: bool) -> User:
        user = self.get(user_id)
        if user.role == UserRole.ADMIN and user.is_active and not is_active:
            admins = self._repository.lock_active_admins()
            if len(admins) <= 1:
                raise LastActiveAdminError
        return self._repository.update(user, {"is_active": is_active})
