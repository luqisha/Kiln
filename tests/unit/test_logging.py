from collections.abc import Iterator

import pytest
import structlog

from app.log import configure_logging, job_secrets

SECRET = "sk-litellm-virtual-key-0123456789"


@pytest.fixture(autouse=True)
def _logging() -> Iterator[None]:
    configure_logging()
    yield
    structlog.reset_defaults()


def test_secret_in_message_is_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    with job_secrets(SECRET):
        structlog.get_logger().info(f"calling proxy with {SECRET}")
    assert SECRET not in capsys.readouterr().out


def test_secret_in_nested_field_is_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    with job_secrets(SECRET):
        structlog.get_logger().info("exec", env={"ANTHROPIC_API_KEY": SECRET}, argv=[SECRET])
    assert SECRET not in capsys.readouterr().out


def test_secret_in_exception_traceback_is_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    with job_secrets(SECRET):
        try:
            raise RuntimeError(SECRET)
        except RuntimeError:
            structlog.get_logger().exception("failed")
    assert SECRET not in capsys.readouterr().out


def test_secrets_are_unbound_after_block(capsys: pytest.CaptureFixture[str]) -> None:
    with job_secrets(SECRET):
        pass
    structlog.get_logger().info("not-a-secret-anymore")
    assert "not-a-secret-anymore" in capsys.readouterr().out


def test_non_string_fields_pass_through(capsys: pytest.CaptureFixture[str]) -> None:
    with job_secrets(SECRET):
        structlog.get_logger().info("exec", exit_code=0, duration_ms=12)
    assert '"exit_code": 0' in capsys.readouterr().out
