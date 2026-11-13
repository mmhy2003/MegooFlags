"""A flag's evaluation counts in one environment, as an hourly or daily series."""

import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from megooflags_api.models import EvaluationCount
from megooflags_api.schemas.evaluation import EvaluationPoint, EvaluationSeries
from megooflags_api.services.events import truncate_hour

EvaluationRange = Literal["24h", "7d", "30d"]
Interval = Literal["hour", "day"]

_WINDOWS: dict[str, tuple[Interval, int]] = {
    "24h": ("hour", 24),
    "7d": ("day", 7),
    "30d": ("day", 30),
}


def series_window(range_: EvaluationRange, now: datetime) -> tuple[Interval, list[datetime]]:
    """The bucket starts, oldest first: hours ending at `now`'s hour, or UTC midnights ending
    today."""
    interval, size = _WINDOWS[range_]
    last = truncate_hour(now)
    step = timedelta(hours=1)
    if interval == "day":
        last, step = last.replace(hour=0), timedelta(days=1)
    return interval, [last - step * i for i in range(size - 1, -1, -1)]


def fill_series(
    interval: Interval, starts: list[datetime], rows: Iterable[tuple[datetime, str, int]]
) -> EvaluationSeries:
    """Sum hourly `(hour, variant, count)` rows into the buckets; rows before the first are
    ignored, and every bucket has a count for every variant."""
    sums: dict[datetime, dict[str, int]] = {start: {} for start in starts}
    variants: set[str] = set()
    for hour, variant, count in rows:
        bucket = truncate_hour(hour)
        if interval == "day":
            bucket = bucket.replace(hour=0)
        if bucket not in sums:
            continue
        sums[bucket][variant] = sums[bucket].get(variant, 0) + count
        variants.add(variant)
    names = sorted(variants)
    points = [
        EvaluationPoint(start=start, counts={name: sums[start].get(name, 0) for name in names})
        for start in starts
    ]
    total = sum(sum(counts.values()) for counts in sums.values())
    return EvaluationSeries(interval=interval, variants=names, points=points, total=total)


async def evaluation_series(
    session: AsyncSession,
    environment_id: uuid.UUID,
    flag_key: str,
    range_: EvaluationRange,
    now: datetime,
) -> EvaluationSeries:
    interval, starts = series_window(range_, now)
    rows = await session.execute(
        select(EvaluationCount.hour, EvaluationCount.variant, func.sum(EvaluationCount.count))
        .where(
            EvaluationCount.environment_id == environment_id,
            EvaluationCount.flag_key == flag_key,
            EvaluationCount.hour >= starts[0],
        )
        .group_by(EvaluationCount.hour, EvaluationCount.variant)
    )
    return fill_series(interval, starts, ((h, v, int(n)) for h, v, n in rows))
