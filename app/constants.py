"""Fixed protocol values. Tunables belong in ``app.config.Settings``, not here."""

from typing import Final

# Last N bytes kept per stdout/stderr stream in job_logs (PLAN.md §5).
LOG_STREAM_MAX_BYTES: Final = 64 * 1024

# Verify output fed back to the agent on retry; pytest prints failures to stdout (PLAN.md §6.2).
RETRY_FEEDBACK_TAIL_LINES: Final = 200

# Verify output included in the failure comment on the GitHub issue (PLAN.md §6.2).
FAILURE_COMMENT_TAIL_LINES: Final = 50

# Issue label that triggers a job (PLAN.md §7, webhook check 2).
TRIGGER_LABEL: Final = "factory"

# Docker labels the reaper uses to find resources that leaked from a job (PLAN.md §6.3).
DOCKER_LABEL_JOB_ID: Final = "factory.job_id"
DOCKER_LABEL_TENANT_ID: Final = "factory.tenant_id"

# Protected even when .factory.yaml omits them, so a repo cannot loosen its own rules (PLAN.md §4).
ALWAYS_PROTECTED_PATHS: Final = (".factory.yaml", ".github/**")

# Extra time past job_timeout_seconds before the reaper declares a job lost (PLAN.md §6.3).
REAPER_GRACE_SECONDS: Final = 10 * 60

# Replaces every known per-job secret in stored output and logs.
REDACTION_PLACEHOLDER: Final = "[REDACTED]"
