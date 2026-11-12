# Engineering Practices

Companion to `PLAN.md`. `PLAN.md` defines **what** to build. This file defines **how** to write, test, review, and ship it. Every rule here is enforced by a tool where possible; a rule with no enforcement is marked *(review)*.

## 1. Principles

1. **Security invariants are tests, not comments.** Every rule in `PLAN.md` §1 and §7 (no credentials in sandbox, no host mounts, network phases, redaction) has at least one test that fails if the rule breaks.
2. **Fail closed.** On doubt (bad signature, unknown YAML key, unknown event, missing permission), reject. Never "log and continue".
3. **Small, typed, pure where possible.** Decisions (edge routing, protected-path matching, limit clamping, redaction) live in pure functions with no I/O, so they are cheap to test exhaustively.
4. **Every external call has a timeout and a cleanup path.** Docker, GitHub, LiteLLM, Postgres, Redis.
5. **Boring and explicit over clever.** No metaprogramming, no global mutable state, no import-time side effects.

## 2. Toolchain

All tools run through `uv` and are pinned in `uv.lock` (dev dependency group).

| Concern | Tool | Gate |
|---|---|---|
| Format | `ruff format` | CI fails on diff |
| Lint | `ruff check` (rule set below) | CI fails on any error |
| Types | `mypy --strict` + `pydantic.mypy` plugin | CI fails on any error |
| Layer boundaries | `import-linter` (contracts in §3 Clean code → Modules) | CI fails on forbidden import |
| Tests | `pytest`, `pytest-cov`, `pytest-xdist`, `pytest-socket` | see §4 |
| HTTP mocking | `respx` (for `httpx`) | — |
| Time | `time-machine` | — |
| Property tests | `hypothesis` (parsers, path matching, redaction) | — |
| DB in tests | `testcontainers[postgres]` (real Postgres 18, never SQLite) | — |
| Migrations | `alembic check` | CI fails if models and migrations drift |
| Dependency vulns | `pip-audit` on exported lock | CI fails on known vuln |
| Secret scan | `gitleaks` | pre-commit + CI |
| Image scan | `trivy` on `Dockerfile` and `docker/agent.Dockerfile` images | CI fails on HIGH/CRITICAL with a fix |
| Hooks | `pre-commit` (ruff, ruff-format, mypy, gitleaks, end-of-file, check-yaml) | local |

### `pyproject.toml` baseline

```toml
[tool.ruff]
target-version = "py314"
line-length = 100

[tool.ruff.lint]
select = [
  "E", "W", "F",    # pycodestyle, pyflakes
  "I",              # isort
  "N",              # pep8-naming
  "C90",            # mccabe complexity
  "FBT",            # no boolean flag arguments
  "B",              # bugbear
  "UP",             # pyupgrade
  "S",              # bandit (security)
  "ASYNC",          # async pitfalls
  "DTZ",            # naive datetimes
  "PTH",            # pathlib over os.path
  "RET", "SIM",     # simpler control flow
  "T20",            # no print()
  "ERA",            # no commented-out code
  "PL",             # pylint subset
  "RUF",
]
ignore = ["PLR0913"]  # many-args is normal for node/adapter signatures

[tool.ruff.lint.mccabe]
max-complexity = 10

[tool.ruff.lint.pylint]
max-branches = 10
max-statements = 40
max-returns = 5
max-nested-blocks = 3

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "PLR2004", "FBT"]  # assert, magic numbers, bool params allowed in tests
"alembic/versions/**" = ["N", "ERA"]      # generated migration names and comments

[tool.mypy]
strict = true
plugins = ["pydantic.mypy"]
warn_unreachable = true

[tool.pytest.ini_options]
addopts = "-ra --strict-markers --strict-config --disable-socket --allow-unix-socket"
markers = [
  "docker: needs a Docker daemon (integration)",
  "db: needs Postgres via testcontainers",
]
filterwarnings = ["error"]

[tool.coverage.run]
branch = true
source = ["app"]

[tool.coverage.report]
fail_under = 85
show_missing = true
```

## 3. Code Conventions

### Structure and layering

* **API layer** (`app/main.py`, route functions): parse, authenticate, call a service, return a schema. No business logic, no raw SQL.
* **Orchestrator nodes** (`app/orchestrator/nodes.py`): get every dependency (Docker runner, Git ops, LLM proxy, DB session factory) from the injected `JobContext`. A node never constructs its own clients. This is what lets Step 4 tests swap in the mock agent and fake runner.
* **Pure decision functions**: `route_after_verify(state) -> str`, `match_protected(paths, patterns) -> list[str]`, `clamp_limits(cfg, tenant) -> cfg`, `redact(text, secrets) -> str`. No I/O. Unit-tested in isolation.
* **Adapters** (`agents/adapter.py`, `sandbox/runner.py`, `github/*`, `llm/proxy.py`): define a `Protocol` per adapter. Production and fake implementations both satisfy it. Tests depend on the `Protocol`, not the concrete class.

### Typing

* Full annotations everywhere. `Any` only at true external boundaries (raw JSON), converted to a Pydantic model immediately.
* Pydantic models for all data crossing a boundary (webhook payloads, `.factory.yaml`, API responses, LiteLLM/GitHub responses). `model_config = ConfigDict(extra="forbid", frozen=True)` unless there is a written reason.
* Secrets typed as `pydantic.SecretStr`. Call `.get_secret_value()` only at the point of use.
* Use `enum.StrEnum` for `JobStatus` and `FailureReason`. No magic strings for statuses or reasons.

### Configuration

* Only `app/config.py` reads environment variables (`pydantic-settings`). Everything else receives a `Settings` object. Ruff ban on `os.environ` / `os.getenv` outside `config.py` via `flake8-tidy-imports` banned-api.
* No defaults for secrets. Missing secret = startup failure.

### Errors

* One exception hierarchy in `app/errors.py`: `FactoryError` → `ConfigError`, `SandboxError`, `GitOpsError`, `AgentError`, `LlmProxyError`. Each carries a `FailureReason`.
* Never `except Exception: pass`. Broad `except` is allowed only in `run_job` (PLAN.md §6.1) and must record the failure.
* Never put a secret, token, or full issue body in an exception message.

### Logging

* `structlog` with JSON output. Bind `job_id`, `tenant_id`, `attempt`, `step` on every log line in a job.
* All log output passes through the same `redact()` used for `job_logs`. A test asserts that a known secret logged at any level never appears in captured output.
* No `print()` (ruff `T20`).

### Sync vs async

* FastAPI routes: `async def`, `httpx.AsyncClient`, SQLAlchemy async session with `psycopg` async.
* Celery worker and LangGraph nodes: sync. Do not call `asyncio.run` inside nodes.
* Never block the event loop in an `async def` (ruff `ASYNC` rules enforce common cases).

### Timeouts and cleanup

* Every `httpx` client has an explicit `timeout=`. Every Docker exec has the `timeout_seconds` from config.
* Any resource that must be released (container, network, LLM key, temp dir) is released in `finally` or via a context manager. Resources are created with `factory.job_id` labels so the reaper can find leaks.

### Database

* SQLAlchemy 2.x style only: `Mapped[]`, `mapped_column`, `select()`. No legacy `Query` API.
* Every query on tenant-owned data filters by `tenant_id`. Provide a helper (`tenant_scoped(select(Job), tenant_id)`) and use it. *(review)*
* Migrations: generate with `alembic revision --autogenerate`, then read and hand-edit. Never edit a migration that is merged. Each migration must upgrade and downgrade cleanly (tested in CI against a fresh Postgres).
* State transitions use conditional `UPDATE ... WHERE status = :expected` (atomic claim pattern from PLAN.md §6.1), never read-then-write.

### Clean code

**Naming** (ruff `N` for casing; the rest *(review)*)

* Functions are verbs: `create_container`, `mint_installation_token`, `route_after_verify`.
* Classes are nouns: `SandboxRunner`, `JobContext`. No `Manager`, `Helper`, `Util`, or `Data` suffixes; name the responsibility instead.
* Booleans read as questions: `is_protected`, `has_changes`, `should_retry`.
* Collections are plural: `changed_paths`, `setup_commands`.
* Units in names when the type does not carry them: `timeout_seconds`, `duration_ms`, `budget_usd`.
* No abbreviations except the standard set: `ctx`, `db`, `id`, `url`, `cfg`, `env`, `pr`, `llm`.
* A name says what the thing *is*, not how it is stored: `pending_jobs`, not `job_list`.

**Functions**

* One job per function. If its description needs "and", split it.
* Complexity ≤ 10, branches ≤ 10, statements ≤ 40, returns ≤ 5, nesting ≤ 3 (ruff `C90`, `PLR0912`, `PLR0915`, `PLR0911`, `PLR1702`). Target ~20 lines; ~50 is the review ceiling.
* Guard clauses and early returns instead of nested `if`/`else`. Handle the error or edge case first, then the happy path unindented.
* No boolean flag arguments (ruff `FBT`). Use two functions, or a keyword-only `StrEnum` argument.
* More than 4 parameters of the same domain concept → group them into a frozen dataclass or Pydantic model.
* Command–query separation: a function either returns information or changes state, not both. Exception: atomic claims like `claim_job() -> bool`, which are named for the action.
* No output parameters. Do not mutate arguments; return new values. LangGraph nodes return a partial state update, never mutate `state` in place.

**Constants and magic values**

* No magic numbers or strings in `app/` (ruff `PLR2004`). Tunables go in `Settings`; fixed protocol values (`64 * 1024` log cap, `200` retry tail lines, `50` comment lines, label names) go in `app/constants.py` with a name and a one-line reason.

**Duplication and abstraction**

* Rule of three: tolerate one duplicate, extract on the third copy.
* Do not add an abstraction, base class, or config option for a need that does not exist yet (YAGNI). The `Protocol` per adapter in §3 is the deliberate exception, because tests need it.
* Prefer composition and plain functions over inheritance. Inheritance depth ≤ 2 outside the exception hierarchy.

**Modules**

* One concept per module. Split a module that grows past ~400 lines or mixes layers. *(review)*
* No circular imports; dependencies point inward: `main`/`worker` → `orchestrator` → adapters (`sandbox`, `github`, `llm`, `agents`) → `schemas`/`models`/`errors`/`constants`. Enforce with `import-linter` contracts in CI.
* Public API of a package is what its `__init__.py` re-exports. Names starting with `_` are not imported across packages.

**Comments and docstrings**

* Docstrings on public functions/classes: one line saying what, plus the non-obvious why. No restating the signature.
* Comments explain *why*, not *what*. If code needs a *what* comment, rename or extract instead.
* No commented-out code (ruff `ERA`). No `TODO` without a linked issue: `# TODO(#123): ...`.

**Refactoring discipline**

* Boy-scout rule: leave touched code a little cleaner, within the scope of the change.
* Behavior changes and refactors go in separate commits (`refactor:` commits change no behavior and no tests' expectations). Large refactors go in their own PR.
* Refactor only under green tests. If the code has no test, add a characterization test first.

## 4. Testing Strategy

### Layers

| Layer | Location | Runs | Needs |
|---|---|---|---|
| Unit | `tests/unit/` | every commit, < 30 s, parallel (`-n auto`) | nothing; network disabled by `pytest-socket` |
| DB | `tests/unit/` or `tests/db/`, marked `db` | every PR | Docker (testcontainers Postgres) |
| Integration | `tests/integration/`, marked `docker` | every PR | Docker daemon, mock agent, local LiteLLM stub |
| End-to-end smoke | `tests/e2e/` | nightly + before release | full `docker-compose`, real GitHub test repo, real LLM with tiny budget |

### Rules

* **Write the test first for every security invariant and every bug fix.** A bug fix PR without a regression test is rejected. *(review)*
* No real network or real LLM calls in unit or integration tests. Use the `mock` agent (PLAN.md Step 3), `respx` for GitHub/LiteLLM HTTP, and a fake `SandboxRunner` for orchestrator unit tests.
* Tests are independent and order-agnostic (`pytest-randomly` optional). No shared mutable fixtures across tests.
* Test names describe behavior: `test_verify_fails_with_protected_path_modified_when_agent_edits_tests_dir`.
* Use `hypothesis` for: `.factory.yaml` parsing (unknown keys always rejected), glob matching in `protected_paths`, `redact()` (a secret never survives, whatever surrounds it), log truncation to 64 KB.

### Coverage gates

* Global: 85 % line + branch (enforced).
* 100 % branch on security-critical modules: `github/webhooks.py`, `auth.py`, protected-path matching, `redact()`, limit clamping, `orchestrator/graph.py` routing. Enforced by a separate `coverage report --include=... --fail-under=100` step.

### Required invariant tests (map to PLAN.md §7)

| Invariant | Test asserts |
|---|---|
| No GitHub token in sandbox | `.git/config` in the copied-in repo has no remote and no token; exec env contains only the allow-listed keys |
| No host mounts | created container has empty `Mounts` |
| Container hardening | `HostConfig` has the exact caps, pids, memory, `no-new-privileges`, non-root user |
| Network phases | from agent network, `curl https://example.com` fails and LiteLLM health URL succeeds |
| Cleanup on crash | node raises → container, networks, LLM key, temp dir all gone |
| Webhook order | bad signature rejected **before** any JSON parsing or DB access |
| Tenant isolation | job of tenant A requested with tenant B key → 404 |
| Limit clamping | `.factory.yaml` cannot raise budget/attempts/timeout above tenant max |
| Redaction | virtual key never appears in `job_logs` or log output |

## 5. Git Workflow

* **Trunk-based.** `main` is always releasable. Short-lived branches (`feat/…`, `fix/…`, `chore/…`), merged within ~2 days.
* **Conventional Commits** (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `chore:`, `ci:`; `!` for breaking). Enables automated changelog.
* **Squash-merge** PRs. PR title = final commit message.
* **Branch protection on `main`:** PR required, CI green, 1 approval, no force push, linear history, signed commits recommended.
* **PR size:** aim < 400 changed lines excluding lockfile and migrations. Split larger work. *(review)*
* **`CODEOWNERS`:** security-critical paths (`app/github/`, `app/auth.py`, `app/sandbox/`, `app/llm/`, `docker/`, `alembic/versions/`) need a second reviewer.

### PR template checklist

```markdown
- [ ] Linked issue / PLAN.md section
- [ ] Tests added or updated (regression test for bug fixes)
- [ ] Touches a PLAN.md §7 invariant? If yes, invariant test updated
- [ ] New external call has a timeout and cleanup path
- [ ] No secrets, tokens, or issue body in logs or exceptions
- [ ] Migration included and reversible (if models changed)
- [ ] Docs / ADR updated (if behavior or architecture changed)
- [ ] Refactors in separate commits from behavior changes; no new TODO without an issue link
```

## 6. Continuous Integration (GitHub Actions)

Pipeline on every PR, jobs in parallel where possible:

1. **lint**: `uv sync --locked`, `ruff format --check`, `ruff check`, `mypy`, `lint-imports`.
2. **unit**: `pytest -m "not docker and not db" -n auto --cov`.
3. **db**: `pytest -m db`, then `alembic upgrade head && alembic check && alembic downgrade base && alembic upgrade head`.
4. **integration**: `pytest -m docker` (runc on CI runners; gVisor job nightly on a self-hosted runner).
5. **security**: `gitleaks`, `pip-audit`, build images + `trivy`.
6. **coverage gate**: combine coverage from 2–4, enforce global and critical-module thresholds.

Also:

* `uv sync --locked` everywhere: CI fails if `uv.lock` is out of date.
* Pin Actions by commit SHA, not tag. Minimal `permissions:` per workflow.
* Renovate (or Dependabot) for Python deps, Docker base images, Actions, and the pinned Claude Code CLI version. Group minor/patch updates weekly.
* Nightly: e2e smoke + gVisor integration run.

## 7. Documentation

* `README.md` (repo root): what it is, quickstart (`docker compose up`), how to run tests.
* `docs/PLAN.md`: spec. Update it in the same PR when behavior changes.
* `docs/adr/NNNN-title.md`: Architecture Decision Records (context, decision, consequences) for any choice that is costly to reverse: e.g. LiteLLM required, no mid-run resume, gVisor in prod, patch-based PR creation.
* `CHANGELOG.md`: generated from Conventional Commits on release.
* `.env.example`: every setting from `config.py`, with no real values.

### Writing style: ASD-STE100

Write all long prose in ASD-STE100 Simplified Technical English. This applies to docs, ADRs, runbooks, milestone logs, PR descriptions, issue comments, reports, and long explanations. Agents must apply the `asd-ste100` skill before they write or revise this text.

Rules:

* Use the active voice. Write one instruction in each sentence.
* Keep procedural sentences to 20 words or fewer. Keep descriptive sentences to 25 words or fewer.
* Use one word for one meaning. Do not use synonyms for a defined term (for example, always "job", never "run" or "task" for the same thing).
* Write steps as a numbered list, in the order the reader does them.
* Put a warning or caution before the step it applies to.
* Do not use idioms, hedges, or filler words.

Exceptions. Keep these exact, and do not rewrite them:

* Code, commands, identifiers, file paths, and configuration keys.
* Quoted error messages and log output.
* Commit subjects. They follow Conventional Commits (§5).

## 8. Definition of Done (per PLAN.md milestone)

A milestone is done only when:

1. All code for the milestone is merged to `main` with CI green.
2. The milestone's tests listed in PLAN.md §8 exist and pass.
3. Related invariant tests from §4 of this file exist and pass.
4. Coverage gates hold.
5. `docker compose up` still starts every service and `/health` returns 200.
6. Docs and ADRs updated.

## 9. Bootstrap Checklist (do before PLAN.md Step 1)

1. `git init`, add `.gitignore` (Python, `.env`, `.venv`, coverage, IDE files).
2. `uv init --package`, set `requires-python = ">=3.14"`, add dev group with the §2 tools.
3. Add the `pyproject.toml` baseline from §2.
4. Add `.pre-commit-config.yaml`; run `pre-commit install`.
5. Add `.github/workflows/ci.yml`, `.github/pull_request_template.md`, `CODEOWNERS`, `renovate.json`.
6. Add `app/errors.py` (exception hierarchy + `FailureReason`), `app/log.py` (structlog + redaction; not `logging.py`, to avoid shadowing the stdlib name) as the first code, since every later module depends on them.
7. Add `CLAUDE.md` pointing the coding agent at `docs/PLAN.md` and `docs/ENGINEERING.md`, with the commands: `uv run ruff format`, `uv run ruff check --fix`, `uv run mypy app`, `uv run pytest -m "not docker"`.
8. Write ADR-0001 recording the stack choices in PLAN.md §2.
