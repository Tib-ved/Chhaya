"""Charts and tables Ask Chhaya can show. The model calls show_chart / show_table; the spec is checked
here, and only a spec that passes is sent to the browser, where the app's own chart components draw it."""
import secrets
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

MAX_SERIES, MAX_POINTS, MAX_COLUMNS, MAX_ROWS = 6, 40, 8, 50


class Point(BaseModel):
    label: str = Field(max_length=60)
    value: float


class Series(BaseModel):
    name: str = Field(max_length=60)
    points: list[Point] = Field(min_length=1, max_length=MAX_POINTS)


class ChartSpec(BaseModel):
    type: Literal["bar", "stacked_bar", "grouped_bar", "line", "donut"]
    title: str = Field(max_length=120)
    x_label: str = Field("", max_length=60)
    y_label: str = Field("", max_length=60)
    series: list[Series] = Field(min_length=1, max_length=MAX_SERIES)

    @model_validator(mode="after")
    def _shape(self):
        if self.type == "donut" and len(self.series) != 1:
            raise ValueError("a donut has exactly one series")
        return self


class TableSpec(BaseModel):
    title: str = Field(max_length=120)
    columns: list[str] = Field(min_length=1, max_length=MAX_COLUMNS)
    rows: list[list[str | float | int | None]] = Field(max_length=MAX_ROWS)

    @model_validator(mode="after")
    def _shape(self):
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError(f"every row needs {len(self.columns)} cells")
        return self


def _reason(error: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in error.errors()[:3])


def validate_visual(name: str, args: dict) -> dict | None:
    """The spec as sent to the browser, or None when it does not pass."""
    model = {"show_chart": ChartSpec, "show_table": TableSpec}.get(name)
    if model is None:
        return None
    try:
        return model.model_validate(args).model_dump()
    except ValidationError:
        return None


async def show_chart(type: str, title: str, series: list[dict], x_label: str = "", y_label: str = "") -> dict:
    """Show the officer a chart. Use numbers from tool results only.

    Args:
        type: one of bar, stacked_bar, grouped_bar, line, donut.
        title: a short title.
        series: up to 6 series, each {"name": str, "points": [{"label": str, "value": number}]} with up to 40 points. A donut has one series.
        x_label: label of the x axis.
        y_label: label of the y axis.
    """
    try:
        ChartSpec.model_validate({"type": type, "title": title, "series": series, "x_label": x_label, "y_label": y_label})
    except ValidationError as error:
        return {"ok": False, "reason": _reason(error)}
    return {"ok": True, "id": f"chart-{secrets.token_hex(4)}"}


async def show_table(title: str, columns: list[str], rows: list[list]) -> dict:
    """Show the officer a table. Use values from tool results only.

    Args:
        title: a short title.
        columns: up to 8 column names.
        rows: up to 50 rows, each a list with one cell (text or number) per column.
    """
    try:
        TableSpec.model_validate({"title": title, "columns": columns, "rows": rows})
    except ValidationError as error:
        return {"ok": False, "reason": _reason(error)}
    return {"ok": True, "id": f"table-{secrets.token_hex(4)}"}
