import pytest

from app.errors import (
    AgentError,
    ConfigError,
    FactoryError,
    FailureReason,
    GitOpsError,
    LlmProxyError,
    SandboxError,
)


@pytest.mark.parametrize(
    ("error_type", "expected_reason"),
    [
        (FactoryError, FailureReason.INTERNAL_ERROR),
        (ConfigError, FailureReason.CONFIG_INVALID),
        (SandboxError, FailureReason.SANDBOX_ERROR),
        (GitOpsError, FailureReason.GIT_ERROR),
        (AgentError, FailureReason.AGENT_ERROR),
        (LlmProxyError, FailureReason.LLM_PROXY_ERROR),
    ],
)
def test_error_carries_its_default_failure_reason(
    error_type: type[FactoryError], expected_reason: FailureReason
) -> None:
    assert error_type("boom").reason is expected_reason


def test_explicit_reason_overrides_default() -> None:
    error = SandboxError("exec timed out", reason=FailureReason.TIMEOUT)
    assert error.reason is FailureReason.TIMEOUT


def test_all_errors_are_factory_errors() -> None:
    assert issubclass(GitOpsError, FactoryError)
