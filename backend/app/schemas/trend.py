from pydantic import BaseModel, Field


class TrendPayload(BaseModel):
    """Per-object click/impression trend: current window vs the preceding one.

    Mirrors ``gsc_page_trend_map`` output (``shopifyseo/dashboard_store.py``) and
    ``EMPTY_TREND`` in ``backend/app/services/_catalog_helpers.py`` -- an
    all-defaults instance is exactly ``EMPTY_TREND``.

    ``*_delta_pct`` is ``None`` when there is no prior-period baseline to compare
    against, which the UI renders as an em dash rather than a misleading +100%.

    Carriers of this field default it to an empty trend rather than ``None``: the
    frontend declares ``trend: trendSchema.optional()``, and Zod's ``optional()``
    accepts ``undefined`` but rejects ``null``, so emitting null would fail
    validation for the whole response.
    """

    clicks_current: int = 0
    clicks_previous: int = 0
    clicks_delta_pct: float | None = None
    impressions_current: int = 0
    impressions_previous: int = 0
    impressions_delta_pct: float | None = None
    series: list[int] = Field(default_factory=list)
