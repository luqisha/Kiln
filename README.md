# Kiln

Self-hosted software factory. A GitHub issue labeled `factory` becomes a job: Kiln starts an isolated Docker sandbox, runs an AI coding agent (Claude Code, OpenCode, or custom), verifies the result with the repository's own tests, and opens a pull request.

* Spec: [`docs/PLAN.md`](docs/PLAN.md)
* How we write code: [`docs/ENGINEERING.md`](docs/ENGINEERING.md)
* Decisions: [`docs/adr/`](docs/adr/)

## Development

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync                          # install Python 3.14 deps from uv.lock
uv run pre-commit install        # format, lint, type, secret checks on commit

uv run ruff format .             # format
uv run ruff check --fix .        # lint
uv run mypy app tests            # type check
uv run lint-imports              # layer boundaries
uv run pytest -m "not docker"    # unit tests (fast, no network)
uv run pytest -m docker          # integration tests (needs Docker)
```
