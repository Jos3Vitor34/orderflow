"""Generate isolated local smoke credentials; never use these files for remote deploy."""

import argparse
import secrets
from pathlib import Path


def prepare(directory: Path) -> None:
    if directory.exists():
        raise SystemExit("Secret directory already exists; refusing to overwrite it")
    directory.mkdir(parents=True, mode=0o700)
    password = secrets.token_hex(32)
    redis_password = secrets.token_hex(32)
    redis_url = f"redis://orderflow:{redis_password}@redis:6379"
    values = {
        "postgres_password": password,
        "redis_password": redis_password,
        "redis_acl": f"user default off\nuser orderflow on >{redis_password} ~* &* +@all\n",
        "DATABASE_URL": f"postgresql+psycopg://orderflow:{password}@postgres:5432/orderflow",
        "REDIS_URL": redis_url + "/0",
        "CELERY_BROKER_URL": redis_url + "/0",
        "CELERY_RESULT_BACKEND": redis_url + "/1",
        "JWT_SECRET_KEY": secrets.token_hex(48),
        "STRIPE_SECRET_KEY": "",
        "STRIPE_WEBHOOK_SECRET": "",
        "LOCAL_ADMIN_PASSWORD": secrets.token_hex(24),
    }
    for name, value in values.items():
        path = directory / name
        path.write_text(value, encoding="utf-8")
        # Individual bind mounts must be readable by the non-root container UID.
        # The enclosing host directory is private (0700).
        path.chmod(0o644)
    print("Isolated local secrets created; no values printed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(".secrets/local-smoke"))
    prepare(parser.parse_args().directory)
