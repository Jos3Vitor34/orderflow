import os

os.environ.setdefault(
    "JWT_SECRET_KEY",
    "test-only-secret-key-that-is-at-least-32-characters",
)
