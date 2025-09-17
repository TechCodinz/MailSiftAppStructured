import io
import types
from _pytest.monkeypatch import MonkeyPatch

from app import extract_emails_from_text
from typing import NoReturn
import file_parsing
import payments


def test_extract_emails_simple() -> None:
    valid, invalid = extract_emails_from_text("Contact: user@example.com")
    assert "user@example.com" in valid


def test_extract_text_from_file_safe() -> None:
    data = io.BytesIO(b"hello world")
    text = file_parsing.extract_text_from_file(data, "test.txt")
    assert isinstance(text, str)
    assert "hello" in text.lower()


def test_verify_trc20_handles_nonjson(monkeypatch: MonkeyPatch) -> None:
    class DummyResp:
        status_code = 200
        text = "not json"

        def json(self) -> NoReturn:
            raise ValueError("no json")

    monkeypatch.setattr(
        payments,
        "requests",
        types.SimpleNamespace(get=lambda url, timeout=10: DummyResp()),
    )
    res = payments.verify_trc20_tx_online("tx123")
    assert res is False
