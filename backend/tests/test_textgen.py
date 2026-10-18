import sys
import types

from app.config import Settings
from app.textgen import TEXT_TIMEOUT_MS, make_text_model


def test_no_text_model_without_a_model_name_or_project():
    assert make_text_model(Settings(_env_file=None, text_model="", gcp_project="p")) is None
    assert make_text_model(Settings(_env_file=None, text_model="m", gcp_project="")) is None


def test_the_client_has_a_time_limit_and_is_made_once(monkeypatch):
    made = []

    class FakeClient:
        def __init__(self, **kwargs):
            made.append(kwargs)
            self.models = self

        def generate_content(self, **kwargs):
            return types.SimpleNamespace(text="a note")

    fake_genai = types.SimpleNamespace(Client=FakeClient)
    google = types.ModuleType("google")
    google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    text_model = make_text_model(Settings(_env_file=None, text_model="m", gcp_project="p"))
    assert text_model("i", "p1") == "a note" and text_model("i", "p2") == "a note"
    assert len(made) == 1
    assert made[0]["http_options"] == {"timeout": TEXT_TIMEOUT_MS}


def test_the_model_is_asked_to_think_little_unless_told_otherwise(monkeypatch):
    asked = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = self

        def generate_content(self, **kwargs):
            asked.append(kwargs["config"])
            return types.SimpleNamespace(text="a note")

    fake_genai = types.SimpleNamespace(Client=FakeClient)
    google = types.ModuleType("google")
    google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)

    make_text_model(Settings(_env_file=None, text_model="m", gcp_project="p"))("i", "p")
    assert asked[0]["thinking_config"] == {"thinking_level": "low"}
    assert asked[0]["system_instruction"] == "i"

    make_text_model(Settings(_env_file=None, text_model="m", gcp_project="p", text_thinking=""))("i", "p")
    assert "thinking_config" not in asked[1]


def test_a_passing_failure_is_tried_once_more_and_a_lasting_one_is_raised(monkeypatch):
    import pytest

    outcomes = []

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = self

        def generate_content(self, **kwargs):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return types.SimpleNamespace(text=outcome)

    fake_genai = types.SimpleNamespace(Client=FakeClient)
    google = types.ModuleType("google")
    google.genai = fake_genai
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    text_model = make_text_model(Settings(_env_file=None, text_model="m", gcp_project="p"))

    outcomes[:] = [RuntimeError("server error"), "a note"]
    assert text_model("i", "p") == "a note" and outcomes == []

    outcomes[:] = [RuntimeError("server error"), RuntimeError("still down"), "never reached"]
    with pytest.raises(RuntimeError, match="still down"):
        text_model("i", "p")
    assert outcomes == ["never reached"]
