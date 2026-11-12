# ADR-0001: Stack and security-architecture choices

* Status: Accepted
* Date: 2026-11-12

## Context

Kiln runs LLM-chosen code against untrusted repositories and issue text (PLAN.md §1, threat model). The stack must make the credential-isolation rule enforceable and the whole job lifecycle testable without spending LLM tokens.

## Decision

* Python 3.14, uv with committed `uv.lock`.
* FastAPI for the webhook and jobs API; Celery + Redis for the job queue; LangGraph `StateGraph` for the plan → code → verify → PR loop.
* PostgreSQL 18 with SQLAlchemy 2.x and Alembic. Multi-tenant (`tenant_id`) from day one.
* Docker SDK for sandboxes; gVisor (`runsc`) in production, `runc` for local dev only. The worker talks to Docker through `docker-socket-proxy`.
* GitHub App installation tokens, minted per job on the worker. Tokens never enter the sandbox.
* LiteLLM proxy is **required**: the sandbox only receives a per-job, budget-capped, expiring virtual key.
* PR creation is patch-based: diff out of the container, apply and push on the worker.
* No mid-run resume in the MVP; a lost worker means a `FAILED` job (`WORKER_LOST`).

## Consequences

* More moving parts (LiteLLM, socket proxy) than calling the provider directly, in exchange for no long-lived secret in the sandbox.
* A crashed worker loses the job's work; users re-trigger by re-labeling the issue.
* gVisor limits some syscalls; target repos whose verify step needs them will not run in production.
* Every limit a repository sets is clamped by tenant limits, so tenant config is the real policy surface.
