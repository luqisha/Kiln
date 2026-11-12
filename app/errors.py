"""Exception hierarchy. Every error carries the FailureReason recorded on the job."""

from enum import StrEnum


class FailureReason(StrEnum):
    """Value stored in ``jobs.failure_reason`` and shown in the issue comment."""

    NO_CHANGES = "NO_CHANGES"
    PROTECTED_PATH_MODIFIED = "PROTECTED_PATH_MODIFIED"
    VERIFY_FAILED = "VERIFY_FAILED"
    CONFIG_INVALID = "CONFIG_INVALID"
    SANDBOX_ERROR = "SANDBOX_ERROR"
    GIT_ERROR = "GIT_ERROR"
    AGENT_ERROR = "AGENT_ERROR"
    LLM_PROXY_ERROR = "LLM_PROXY_ERROR"
    TIMEOUT = "TIMEOUT"
    WORKER_LOST = "WORKER_LOST"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class FactoryError(Exception):
    """Base for all engine errors. Messages must never contain secrets or issue content."""

    default_reason = FailureReason.INTERNAL_ERROR

    def __init__(self, message: str, *, reason: FailureReason | None = None) -> None:
        super().__init__(message)
        self.reason = reason or self.default_reason


class ConfigError(FactoryError):
    """Invalid engine settings or an invalid ``.factory.yaml``."""

    default_reason = FailureReason.CONFIG_INVALID


class SandboxError(FactoryError):
    """Container, network, or image operation failed."""

    default_reason = FailureReason.SANDBOX_ERROR


class GitOpsError(FactoryError):
    """Host-side clone, patch, push, or GitHub API call failed."""

    default_reason = FailureReason.GIT_ERROR


class AgentError(FactoryError):
    """The coding agent CLI crashed or returned unusable output."""

    default_reason = FailureReason.AGENT_ERROR


class LlmProxyError(FactoryError):
    """Creating or revoking a LiteLLM virtual key failed."""

    default_reason = FailureReason.LLM_PROXY_ERROR
