import uvicorn

from app.core.config import get_settings
from app.core.logging import configure_logging


def main() -> None:
    settings = get_settings()
    configure_logging(settings)
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # noqa: S104 - container listener; publishing is controlled by Compose
        port=8000,
        access_log=False,
        log_config=None,
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
