import json
import logging
from datetime import datetime

from app.core.context import correlation_id_var, task_id_var
from app.core.logging import JsonFormatter


def render(record: logging.LogRecord) -> dict[str, object]:
    return json.loads(
        JsonFormatter(service="orderflow-test", environment="test").format(record)
    )


def test_json_log_has_utc_timestamp_level_context_and_extras() -> None:
    correlation_token = correlation_id_var.set("request-123")
    task_token = task_id_var.set("task-456")
    try:
        record = logging.LogRecord(
            "orderflow.test",
            logging.WARNING,
            __file__,
            1,
            "processed %s",
            ("event",),
            None,
        )
        record.http_status = 202
        event = render(record)
    finally:
        task_id_var.reset(task_token)
        correlation_id_var.reset(correlation_token)

    assert event["level"] == "WARNING"
    assert event["logger"] == "orderflow.test"
    assert event["message"] == "processed event"
    assert event["service"] == "orderflow-test"
    assert event["environment"] == "test"
    assert event["correlation_id"] == "request-123"
    assert event["task_id"] == "task-456"
    assert event["http_status"] == 202
    assert (
        datetime.fromisoformat(str(event["timestamp"])).utcoffset().total_seconds() == 0
    )


def test_json_log_redacts_sensitive_keys_tokens_urls_and_exceptions() -> None:
    try:
        raise RuntimeError(
            "Bearer abc.def.ghi password=hunter2 "
            "postgresql://user:db-password@database/orderflow"
        )
    except RuntimeError:
        record = logging.LogRecord(
            "orderflow.test",
            logging.ERROR,
            __file__,
            1,
            "failed with sk_test_supersecret and whsec_supersecret",
            (),
            exc_info=__import__("sys").exc_info(),
        )
    record.authorization = "Bearer another.token.value"
    record.hashed_password = "$argon2$secret"
    event = render(record)
    serialized = json.dumps(event)

    assert event["authorization"] == "[REDACTED]"
    assert event["hashed_password"] == "[REDACTED]"
    assert event["exception_type"] == "RuntimeError"
    for secret in (
        "abc.def.ghi",
        "hunter2",
        "db-password",
        "sk_test_supersecret",
        "whsec_supersecret",
        "$argon2$secret",
    ):
        assert secret not in serialized
