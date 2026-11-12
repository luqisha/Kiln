from hypothesis import given
from hypothesis import strategies as st

from app.constants import REDACTION_PLACEHOLDER
from app.log import redact, truncate_tail

# Secrets with brackets or inside the placeholder could be "re-formed" by the placeholder
# itself; real per-job keys (e.g. "sk-...") never look like that.
secret_text = st.text(min_size=1).filter(
    lambda s: "[" not in s and "]" not in s and s not in REDACTION_PLACEHOLDER
)


def test_redact_replaces_secret() -> None:
    assert redact("key=sk-abc123 end", ["sk-abc123"]) == f"key={REDACTION_PLACEHOLDER} end"


def test_redact_without_secrets_returns_text_unchanged() -> None:
    assert redact("nothing here", []) == "nothing here"


def test_redact_ignores_empty_secret() -> None:
    assert redact("abc", [""]) == "abc"


def test_redact_handles_secret_contained_in_another() -> None:
    assert redact("x sk-abc123 y", ["sk-abc", "sk-abc123"]) == f"x {REDACTION_PLACEHOLDER} y"


@given(prefix=st.text(), suffix=st.text(), secret=secret_text, others=st.lists(secret_text))
def test_secret_never_survives_redaction(
    prefix: str, suffix: str, secret: str, others: list[str]
) -> None:
    text = prefix + secret + suffix + secret
    assert secret not in redact(text, [secret, *others])


def test_truncate_tail_keeps_short_text() -> None:
    assert truncate_tail("short", 64) == ("short", False)


def test_truncate_tail_keeps_last_bytes() -> None:
    assert truncate_tail("0123456789", 4) == ("6789", True)


@given(text=st.text(), max_bytes=st.integers(min_value=0, max_value=256))
def test_truncate_tail_never_exceeds_limit(text: str, max_bytes: int) -> None:
    kept, was_cut = truncate_tail(text, max_bytes)
    assert len(kept.encode()) <= max_bytes
    assert text.endswith(kept)
    assert was_cut == (kept != text)


def test_truncate_tail_to_zero_bytes_keeps_nothing() -> None:
    assert truncate_tail("abc", 0) == ("", True)
