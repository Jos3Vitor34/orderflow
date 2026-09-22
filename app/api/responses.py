"""Shared OpenAPI response description typing."""

from typing import Any

# FastAPI declares its responses parameter with this invariant mapping type.
ResponseDescriptions = dict[int | str, dict[str, Any]]
