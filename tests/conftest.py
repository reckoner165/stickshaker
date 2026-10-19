import pytest

from stickshaker import events


@pytest.fixture(autouse=True)
def allow_tmp_transcripts(tmp_path, monkeypatch):
    """Tests write transcripts to tmp_path; treat it like ~/.claude for reading."""
    monkeypatch.setattr(events, "ALLOWED_ROOTS", (tmp_path,))
