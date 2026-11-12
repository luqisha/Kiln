# Kiln

Read before writing code:

* `docs/PLAN.md`: the spec. Build milestones in the order of §8. The threat model in §1 and the rules in §7 are hard requirements.
* `docs/ENGINEERING.md`: coding standards, clean code rules, testing strategy, git workflow. Follow it.

## Commands

```bash
uv run ruff format .
uv run ruff check --fix .
uv run mypy app tests
uv run lint-imports
uv run pytest -m "not docker and not db"   # fast unit tests
uv run pytest -m docker                    # integration, needs Docker
```

Run all of the above before saying a change is done. Add deps with `uv add` / `uv add --dev`, never by editing `uv.lock`.

## Rules that are easy to miss

* Write all long prose (docs, ADRs, runbooks, milestone logs, PR descriptions, issue comments, reports, long explanations) in ASD-STE100 Simplified Technical English. Apply the `asd-ste100` skill before you write or revise it. Code, identifiers, and quoted errors stay exact. See `docs/ENGINEERING.md` §7.
* Env vars are read only in `app/config.py`. Fixed values go in `app/constants.py`.
* Raise subclasses of `app.errors.FactoryError`; each carries a `FailureReason`.
* Log with `structlog`. Wrap job code in `app.log.job_secrets(...)` so per-job secrets are redacted; store command output with `app.log.redact` and `truncate_tail`.
* Imports point inward only (contract in `pyproject.toml`, checked by `lint-imports`).
* Every PLAN.md §7 rule needs a test that fails if the rule breaks. Bug fixes need a regression test.
* Package dir is `app/`; the PLAN.md tree's root `software-factory/` is this repo (`kiln`).
