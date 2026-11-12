# Software Factory Engine: Technical Specification (MVP)

## 1. Purpose

Build a self-hosted, un-opinionated Software Factory engine. The system receives software tasks from GitHub, starts an isolated Docker sandbox, runs a configurable AI coding agent (such as Claude Code or OpenCode), verifies the code with automated tests, and opens a pull request.

The system must separate the orchestration engine from the repository rules so any software project can use the engine via a single `.factory.yaml` configuration file.

### Threat model (read first)

The issue body, the repository contents, and `.factory.yaml` are **untrusted input**. The agent executes arbitrary code chosen by an LLM that reads that input, so assume the sandbox is hostile. Every design decision below follows from one rule:

> **The sandbox container never holds a long-lived credential, and never has unrestricted network access while it holds any credential.**

## 2. System Architecture & Tech Stack

Versions are minimum floors (verified 2026-11-12). Pin exact versions in `uv.lock`.

| Component | Choice | Version floor |
|---|---|---|
| Language | Python | `>=3.14` |
| Package manager / lockfile | uv | latest, commit `uv.lock` |
| API gateway | FastAPI + Uvicorn | `fastapi>=0.162`, `uvicorn[standard]>=0.54` |
| Task queue | Celery + Redis | `celery[redis]>=5.7`, `redis>=8.1`, Redis server `8.10` |
| Workflow orchestration | LangGraph (1.x API: `StateGraph`) | `langgraph>=1.2` |
| Database | PostgreSQL + SQLAlchemy 2.x (`Mapped[]`, `select()`) + Alembic | Postgres `18`, `sqlalchemy>=2.1`, `alembic>=1.20`, `psycopg[binary]>=3.3` |
| Settings / validation | Pydantic v2 + pydantic-settings | `pydantic>=2.14`, `pydantic-settings>=2.16` |
| Sandbox execution | Docker SDK for Python | `docker>=7.2` |
| Sandbox runtime | gVisor (`runsc`) recommended; `runc` allowed for local dev only | — |
| GitHub integration | GitHub App (installation tokens) via `httpx` + `pyjwt` | `httpx>=0.28` |
| LLM routing | LiteLLM proxy (**required**, not optional) | `litellm>=1.109` |
| Default agent | Claude Code CLI, pinned version, model `claude-sonnet-5-5` | pin in `docker/agent.Dockerfile` |

### Component overview

```text
GitHub ──webhook──▶ FastAPI ──▶ Postgres (job row) ──▶ Celery (Redis)
                                                         │
                                                         ▼
                                                  Celery worker
                         (holds GitHub App key, talks to Docker via socket proxy)
                          │ clone / push / PR            │ create, exec, destroy
                          ▼                              ▼
                       GitHub                 Sandbox container (gVisor)
                                              - no GitHub credentials
                                              - LLM access only via LiteLLM
                                                proxy with a per-job virtual key
```

## 3. Directory Structure

Create the repository with this exact layout:

```text
software-factory/
├── app/
│   ├── main.py                 # FastAPI app: webhook, jobs API, health endpoints
│   ├── config.py               # Environment settings (pydantic-settings BaseSettings)
│   ├── database.py             # PostgreSQL engine and session management
│   ├── models.py               # SQLAlchemy models (Tenant, Repository, Job, JobLog)
│   ├── schemas.py              # Pydantic schemas (webhook payloads, .factory.yaml, API responses)
│   ├── auth.py                 # Tenant API key verification for the jobs API
│   ├── worker.py               # Celery app, run_job task (try/finally cleanup), beat schedule
│   ├── orchestrator/
│   │   ├── graph.py            # LangGraph state machine (plan -> code -> verify -> PR)
│   │   ├── nodes.py            # Individual graph node functions
│   │   └── state.py            # Graph state TypedDict
│   ├── sandbox/
│   │   ├── runner.py           # Container lifecycle (create, exec, copy in/out, destroy)
│   │   ├── image_builder.py    # Builds derived image: user base image + agent layer
│   │   ├── network.py          # Per-job networks (setup egress vs. agent-only egress)
│   │   └── reaper.py           # Periodic removal of orphan containers and stale jobs
│   ├── github/
│   │   ├── app_auth.py         # GitHub App JWT and installation token minting
│   │   ├── webhooks.py         # Signature check, event filtering, delivery dedupe
│   │   └── git_ops.py          # Host-side clone, apply patch, commit, push, open PR, comment
│   ├── llm/
│   │   └── proxy.py            # LiteLLM virtual key create/delete with per-job budget
│   └── agents/
│       └── adapter.py          # CLI adapters: claude-code, opencode, custom, mock
├── alembic/                    # Migrations
├── docker/
│   ├── agent.Dockerfile        # Agent layer (pinned Claude Code / OpenCode binaries)
│   └── litellm.config.yaml     # LiteLLM proxy config
├── tests/
│   ├── unit/
│   │   ├── test_webhooks.py
│   │   ├── test_yaml_parser.py
│   │   ├── test_graph_routing.py
│   │   └── test_protected_paths.py
│   └── integration/            # Marked @pytest.mark.docker; needs a Docker daemon
│       ├── test_sandbox.py
│       └── test_retry_loop.py
├── docker-compose.yml          # api, worker, beat, redis, postgres, litellm, docker-socket-proxy
├── Dockerfile
├── pyproject.toml
├── uv.lock
└── .env.example
```

## 4. Declarative Repository Contract (`.factory.yaml`)

Every target repository can place a `.factory.yaml` file in its root directory to define its own build rules. Parse and validate this schema with Pydantic. Reject unknown keys (`extra="forbid"`). If the file does not exist, use the defaults shown below.

```yaml
version: "1.0"
agent:
  provider: "claude-code"          # Options: claude-code | opencode | custom
  model: "claude-sonnet-5-5"
  max_attempts: 3                  # TOTAL code+verify attempts (1 initial + 2 fixes)
  max_turns: 50                    # Agent tool-use turns per attempt
  budget_usd: 5.00                 # Hard LLM spend cap for the whole job (capped by tenant limit)
sandbox:
  image: "python:3.14-slim"        # Base image; must be glibc-based (Debian/Ubuntu)
  timeout_seconds: 900             # Per-command limit
  job_timeout_seconds: 3600        # Wall-clock limit for the whole job
  setup_commands:                  # Run WITH internet, WITHOUT any credentials
    - "pip install -r requirements.txt"
workflow:
  plan_step: true                  # Generate an implementation plan before editing files
  verify_command: "pytest"         # Command that must exit with code 0
  protected_paths:                 # Agent edits here fail the attempt
    - "tests/**"
  auto_pr: true                    # Open GitHub PR automatically on success
```

Rules:

* `budget_usd`, `max_attempts`, and `job_timeout_seconds` are clamped to tenant-level maximums from engine config. A repository cannot raise its own limits above the tenant limits.
* `.factory.yaml` and `.github/**` are always protected, even when `protected_paths` omits them.
* The repository controls `setup_commands`. Treat them as untrusted code (see Section 7).

## 5. Database Schema (`app/models.py`)

Design the database with multi-tenancy fields (`tenant_id`) from day one to support a future SaaS launch. All IDs are UUIDs. All timestamps are `timestamptz`.

1. **`tenants`**: `id`, `name` (String), `api_key_hash` (String, argon2), `max_budget_usd` (Numeric), `max_attempts` (Integer), `created_at`.
2. **`repositories`**: `id`, `tenant_id` (FK), `github_repo_full_name` (String, unique), `github_installation_id` (BigInteger), `default_branch` (String), `created_at`.
   * Do not store GitHub tokens. Mint short-lived installation tokens per job from the GitHub App private key.
   * Engine-level LLM keys live in the LiteLLM proxy config. Bring-your-own LLM keys, when added later, are encrypted with a key from KMS or a secret manager (envelope encryption), never with a key in `.env` committed alongside the data.
3. **`jobs`**: `id`, `tenant_id` (FK, denormalized for tenant-scoped queries), `repo_id` (FK), `issue_number` (Integer), `issue_title` (String), `issue_body` (Text), `github_delivery_id` (String, unique), `status` (Enum: `QUEUED`, `RUNNING`, `FAILED`, `COMPLETED`, `CANCELLED`), `failure_reason` (String, nullable), `attempt_count` (Integer), `base_branch` (String), `base_commit_sha` (String, nullable), `branch_name` (String, nullable), `pr_url` (String, nullable), `llm_cost_usd` (Numeric, nullable), `created_at`, `started_at` (nullable), `completed_at` (nullable).
   * Partial unique index on `(repo_id, issue_number) WHERE status IN ('QUEUED', 'RUNNING')`. This allows at most one active job per issue.
4. **`job_logs`**: `id`, `job_id` (FK), `step_name` (String), `attempt` (Integer), `command` (Text), `stdout` (Text), `stderr` (Text), `stdout_truncated` (Boolean), `stderr_truncated` (Boolean), `exit_code` (Integer, nullable), `duration_ms` (Integer), `timestamp`.
   * Store at most the last 64 KB of each stream. Set the `*_truncated` flags when cutting.
   * Before storing, redact any value that matches a known secret for that job (for example, the virtual LLM key).

## 6. Execution Workflow

### 6.1 Celery task wrapper (`app/worker.py`)

`run_job(job_id)` owns the container lifecycle. Cleanup is **not** a graph node, because a graph node does not run if the graph raises.

```python
def run_job(job_id):
    # Atomic claim: UPDATE jobs SET status='RUNNING' WHERE id=:id AND status='QUEUED'
    # If 0 rows updated -> duplicate delivery, return.
    ctx = JobContext(job_id)
    try:
        graph.invoke(initial_state, config={"configurable": {"ctx": ctx}})
    except Exception as e:
        mark_failed(job_id, reason=repr(e))
        comment_on_issue_failure(job_id)
    finally:
        ctx.destroy_container()  # container.remove(force=True)
        ctx.destroy_networks()
        ctx.revoke_llm_key()
        ctx.remove_host_workdir()
```

Celery settings:

* `task_acks_late=True`, `worker_prefetch_multiplier=1`.
* Redis broker `visibility_timeout` greater than the maximum `job_timeout_seconds` plus a 10-minute margin. Otherwise Redis re-delivers long jobs, and the atomic claim above is the second line of defense.
* `soft_time_limit = job_timeout_seconds`, `time_limit = job_timeout_seconds + 120`.
* MVP has no mid-run resume. The sandbox dies with the worker, so a crashed job is marked `FAILED` by the reaper and can be re-triggered by re-labeling the issue.

### 6.2 LangGraph state machine (`app/orchestrator/graph.py`)

Implement these exact nodes and edges:

```text
START -> init_sandbox -> [plan_step?] -> generate_plan -> execute_code -> verify_code
                                     \-------------------> execute_code
verify_code --pass--> create_pr -> END
verify_code --fail, attempt < max_attempts--> execute_code   (attempt += 1)
verify_code --fail, attempt >= max_attempts--> fail -> END
any node raising -> handled by run_job wrapper
```

1. **`init_sandbox`**
   * Mint a GitHub installation token on the **worker** (host side). Clone the repository into a temporary worker directory at the default branch. Record `base_commit_sha`.
   * Remove the remote and all credentials from the clone (`git remote remove origin`). Make sure no token remains in `.git/config`.
   * Read and validate `.factory.yaml` from the clone (use defaults if missing). Clamp limits to tenant maximums.
   * Build (or reuse, keyed by base image digest and agent version) a derived image: `FROM <sandbox.image>`, then install `git` and `ca-certificates` via `apt-get` and `COPY --from=factory-agent` the pinned agent binaries.
   * Create the container with the limits from Section 7. Copy the repository in with `put_archive`. Create branch `factory/issue-{issue_number}-{job_id[:8]}` inside the container.
   * Attach the container to the **setup network** (internet egress, no credentials in env). Run all `setup_commands`.
   * Disconnect the setup network. Attach the **agent network** (egress only to the LiteLLM proxy).

2. **`generate_plan`** (only when `workflow.plan_step == true`)
   * Create the per-job LiteLLM virtual key (`max_budget = budget_usd`, expiry = `job_timeout_seconds`) if it does not exist yet.
   * Run the agent read-only. For Claude Code: `claude -p "<prompt>" --permission-mode plan --model <model> --max-turns <max_turns> --output-format json`.
   * The prompt contains the issue title, the issue body (wrapped in clearly delimited `<untrusted_issue>` tags), and the repository file tree.
   * Store the plan in graph state.

3. **`execute_code`**
   * Run the agent headless inside the container. For Claude Code: `claude -p "<prompt>" --allowedTools "Read,Edit,Write,Bash" --model <model> --max-turns <max_turns> --output-format json`.
   * Env vars passed to this exec only: `ANTHROPIC_BASE_URL=<litellm proxy url>`, `ANTHROPIC_API_KEY=<per-job virtual key>`. Never pass the real provider key or any GitHub token.
   * On a retry (`attempt > 1`), include the previous verify output in the prompt: the **last 200 lines of combined stdout and stderr**, because test runners such as pytest print failures to stdout. Also include any protected-path violation message.
   * Record the agent's reported cost in `jobs.llm_cost_usd`.

4. **`verify_code`**
   * Run `git add -A && git diff --cached --name-only <base_commit_sha>` inside the container.
     * If the diff is empty, the attempt fails with reason `NO_CHANGES`.
     * If any changed path matches a protected path, the attempt fails with reason `PROTECTED_PATH_MODIFIED` and lists the paths.
   * Otherwise run `workflow.verify_command` inside the container (agent network, no credentials in env).
   * Record `exit_code`, `stdout`, and `stderr` in `job_logs` for every attempt.
   * **Edge routing:**
     * Pass (exit code 0 and no violations): go to `create_pr`.
     * Fail and `attempt < max_attempts`: increment `attempt` and loop back to `execute_code`.
     * Fail and `attempt >= max_attempts`: go to `fail`.

5. **`create_pr`** (only when `workflow.auto_pr == true`; otherwise mark `COMPLETED` with the patch stored)
   * Inside the container: `git diff --cached --binary <base_commit_sha>` and stream the patch out to the worker.
   * On the worker: apply the patch to the host clone with `git apply --index`, then commit `feat: resolve #{issue_number}` as the GitHub App bot identity.
   * Mint a fresh installation token. Push the branch from the worker.
   * Call the GitHub REST API to open a pull request with `Closes #{issue_number}` in the body, plus the plan and an attempt summary.
   * Set `jobs.status = COMPLETED` and save `pr_url`.

6. **`fail`**
   * Set `jobs.status = FAILED` and `failure_reason`.
   * Comment on the issue with the reason, the attempt count, and the last verify output (truncated to 50 lines).

### 6.3 Reaper (`app/sandbox/reaper.py`, Celery beat, every 5 minutes)

* Remove every container and network labeled `factory.job_id` whose job is not `RUNNING`, or whose age exceeds `job_timeout_seconds + 10 minutes`.
* Mark jobs that stay `RUNNING` past `job_timeout_seconds + 10 minutes` as `FAILED` with reason `WORKER_LOST`, and revoke their LiteLLM keys.

## 7. Security & Isolation Requirements

* **No credentials in the sandbox.** GitHub tokens never enter the container. Clone, push, and the PR API call happen on the worker. The only secret in the container is the per-job LiteLLM virtual key: budget-capped, expiring, and revoked in `finally`.
* **Network phases.**
  * Setup phase: internet egress allowed, no secrets present.
  * Agent and verify phases: an `internal` Docker network whose only reachable host is the LiteLLM proxy. No general internet.
* **No host mounts.** Do not bind-mount host directories into sandbox containers. Move code in and out only with `put_archive` and `get_archive`/exec stdout.
* **Docker socket.** The worker reaches Docker through `docker-socket-proxy`, which allows only the container, image, network, exec, and build endpoints. In production, run sandboxes on a dedicated host, separate from the API and the database.
* **Container hardening.** gVisor runtime (`runtime="runsc"`) in production. `nano_cpus=2e9`, `mem_limit="4g"`, `memswap_limit="4g"`, `pids_limit=512`, `cap_drop=["ALL"]`, `security_opt=["no-new-privileges"]`, non-root user, plus a writable tmpfs for `/tmp`. Labels: `factory.job_id`, `factory.tenant_id`.
* **Secret handling.** Never write keys to disk or logs. Redact known per-job secrets from all captured output before storing it.
* **Webhook verification** (`POST /webhooks/github`), in this order:
  1. Validate `X-Hub-Signature-256` with a constant-time compare over the **raw** request body.
  2. Accept only `X-GitHub-Event: issues` with `action == "labeled"` and `label.name == "factory"`. Return 204 for all other events.
  3. Check that the sender has `write` or higher permission on the repository (GitHub collaborator permission API).
  4. Dedupe on `X-GitHub-Delivery` (unique column). Return 200 for a duplicate.
  5. Insert the job row (`QUEUED`), enqueue it, and return 202 within GitHub's 10-second limit.
* **Jobs API auth.** `GET /jobs/{job_id}` requires `Authorization: Bearer <tenant api key>`. Return 404 (not 403) when the job belongs to another tenant.
* **Prompt injection.** Wrap issue content in delimited untrusted tags and tell the agent to treat it as a task description only. This reduces the risk but does not remove it. The controls above are the real defense.

## 8. Implementation Milestones for the Coding Agent

Build and verify the system in this exact order:

* **Step 1:** Initialize the project with uv (`pyproject.toml`, `uv.lock`), `docker-compose.yml` (api, worker, beat, redis, postgres, litellm, docker-socket-proxy), config, and the SQLAlchemy models with the first Alembic migration.
* **Step 2:** Implement `app/sandbox/runner.py`, `network.py`, and `image_builder.py`. Write integration tests (`@pytest.mark.docker`) that confirm:
  * a container starts with the Section 7 limits;
  * it runs a shell command and captures `stdout` and `stderr` separately;
  * files copy in and out without host mounts;
  * the agent network blocks internet access but reaches the LiteLLM proxy;
  * the container is destroyed cleanly, including when the test raises midway.
* **Step 3:** Implement `app/agents/adapter.py` with headless `claude-code` support and a `mock` agent. The mock agent applies scripted file edits per attempt, so tests run without spending LLM tokens. Implement `app/llm/proxy.py` (virtual key create, revoke, budget).
* **Step 4:** Build the LangGraph state machine (`state.py`, `graph.py`, `nodes.py`) and the `run_job` wrapper. Test with the mock agent:
  * fail on attempt 1, pass on attempt 2;
  * fail on every attempt, end in `FAILED` after exactly `max_attempts`;
  * an empty diff, which gives `NO_CHANGES`;
  * a protected-path edit, which gives `PROTECTED_PATH_MODIFIED`;
  * an exception inside a node, after which the container, networks, and LLM key are still cleaned up.
* **Step 5:** Implement GitHub App auth, host-side `git_ops.py`, and the webhook endpoint with all five checks from Section 7. Unit-test signature failure, a wrong event or label, an insufficient sender permission, and a duplicate delivery.
* **Step 6:** Add the authenticated `GET /jobs/{job_id}` endpoint (status, attempt count, failure reason, PR URL, cost, logs), and the reaper beat task with tests.
