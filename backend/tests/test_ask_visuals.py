import asyncio

import pytest
from google.adk.tools import FunctionTool

from app.ask.visuals import show_chart, show_table, validate_visual

SERIES = [{"name": "Cases", "points": [{"label": "Ward 1", "value": 3}, {"label": "Ward 2", "value": 1}]}]


def run(coroutine):
    return asyncio.run(coroutine)


def test_valid_chart_and_table():
    assert run(show_chart("bar", "Red cases", SERIES))["ok"]
    assert run(show_chart("donut", "Tiers", SERIES))["ok"]
    assert run(show_table("Top risk", ["Name", "Score"], [["Kamla", 7], ["Mohan", 5]]))["ok"]


@pytest.mark.parametrize("kind,series", [
    ("pie", SERIES),
    ("bar", SERIES * 7),
    ("donut", SERIES * 2),
    ("bar", [{"name": "x", "points": [{"label": "a", "value": "lots"}]}]),
    ("bar", []),
])
def test_bad_charts_say_why(kind, series):
    result = run(show_chart(kind, "t", series))
    assert result["ok"] is False and result["reason"]


def test_bad_tables_say_why():
    assert not run(show_table("t", ["a", "b"], [["only one"]]))["ok"]
    assert not run(show_table("t", ["a"], [["x"]] * 51))["ok"]
    assert not run(show_table("t", [f"c{i}" for i in range(9)], []))["ok"]


def test_validate_visual_returns_the_browser_spec_or_none():
    spec = validate_visual("show_chart", {"type": "line", "title": "Trend", "series": SERIES})
    assert spec["type"] == "line" and spec["series"][0]["points"][0] == {"label": "Ward 1", "value": 3.0}
    assert validate_visual("show_chart", {"type": "pie", "title": "t", "series": SERIES}) is None
    assert validate_visual("get_case", {}) is None


def test_adk_can_declare_them():
    from app.ask import tools

    for function in (show_chart, show_table, *tools.ROOT_TOOLS, *tools.ANALYST_TOOLS, *tools.SUMMARIZER_TOOLS):
        declaration = FunctionTool(function)._get_declaration()
        assert declaration.name == function.__name__
        assert declaration.parameters_json_schema["type"] == "object"
