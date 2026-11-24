"""Простые SVG-графики без внешних JS-библиотек."""

from __future__ import annotations

from dataclasses import dataclass

from ..timeutil import clamp


@dataclass
class Spark:
    line: str
    area: str
    width: int
    height: int
    low: float
    high: float
    last: float | None
    count: int

    @property
    def empty(self) -> bool:
        return self.count == 0


def sparkline(
    values: list[float | None],
    *,
    width: int = 320,
    height: int = 56,
    vmin: float = 0.0,
    vmax: float = 100.0,
) -> Spark:
    clean = [None if v is None else float(v) for v in values]
    present = [v for v in clean if v is not None]
    span = max(0.001, vmax - vmin)
    n = len(clean)

    points: list[tuple[float, float]] = []
    for index, value in enumerate(clean):
        if value is None:
            continue
        x = 1.0 if n <= 1 else 1.0 + index * (width - 2) / (n - 1)
        ratio = (clamp(value, vmin, vmax) - vmin) / span
        y = height - 1 - ratio * (height - 2)
        points.append((round(x, 2), round(y, 2)))

    if not points:
        return Spark("", "", width, height, vmin, vmax, None, 0)

    line = "M " + " L ".join(f"{x} {y}" for x, y in points)
    first_x, last_x = points[0][0], points[-1][0]
    area = (
        f"M {first_x} {height - 1} L {first_x} {points[0][1]} "
        + " L ".join(f"{x} {y}" for x, y in points)
        + f" L {last_x} {height - 1} Z"
    )
    return Spark(
        line=line,
        area=area,
        width=width,
        height=height,
        low=min(present),
        high=max(present),
        last=present[-1],
        count=len(present),
    )
