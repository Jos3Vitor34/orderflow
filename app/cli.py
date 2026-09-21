import argparse
import getpass
import logging
import os

from pydantic import ValidationError

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import SessionLocal
from app.models.user import UserRole
from app.repositories.user import DuplicateEmailError, UserRepository
from app.schemas.user import UserCreate
from app.services.user import UserService

logger = logging.getLogger(__name__)


def bootstrap_admin(*, email: str, full_name: str) -> int:
    password = os.environ.get("ORDERFLOW_ADMIN_PASSWORD")
    if password is None:
        configured_password = get_settings().orderflow_admin_password
        password = (
            configured_password.get_secret_value() if configured_password else None
        )
    if not password:
        password = getpass.getpass("Administrator password: ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            logger.error("Administrator passwords do not match")
            return 2

    try:
        data = UserCreate(
            full_name=full_name,
            email=email,
            password=password,
            role=UserRole.ADMIN,
        )
    except ValidationError:
        logger.error(
            "Invalid administrator data; use a valid e-mail and a password of 8-128 characters"
        )
        return 2

    with SessionLocal() as session:
        service = UserService(UserRepository(session))
        try:
            service.create(data)
        except DuplicateEmailError:
            logger.error("An account with this e-mail already exists")
            return 1

    logger.info("Administrator created successfully")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser(
        "bootstrap-admin",
        help="Create the first administrator without public signup",
    )
    bootstrap.add_argument("--email", required=True)
    bootstrap.add_argument("--full-name", required=True)
    return parser


def main() -> int:
    configure_logging(get_settings())
    args = build_parser().parse_args()
    if args.command == "bootstrap-admin":
        return bootstrap_admin(email=args.email, full_name=args.full_name)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
