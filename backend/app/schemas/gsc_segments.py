from pydantic import BaseModel, Field


class GscSegmentFlagsPayload(BaseModel):
    """True when Tier B query×segment rows exist for this catalog URL."""

    has_dimensional: bool = False


class GscSegmentRollupItem(BaseModel):
    segment: str
    clicks: int = 0
    impressions: int = 0
    share: float = 0.0


class GscSegmentPairItem(BaseModel):
    query: str
    dimension_kind: str
    dimension_value: str
    clicks: int = 0
    impressions: int = 0
    position: float = 0.0


class GscSegmentSummaryPayload(BaseModel):
    """Per-URL GSC breakdown (same date window as per-URL GSC fetch / Overview period), from SQLite cache."""

    fetched_at: int | None = None
    device_mix: list[GscSegmentRollupItem] = Field(default_factory=list)
    top_countries: list[GscSegmentRollupItem] = Field(default_factory=list)
    search_appearances: list[GscSegmentRollupItem] = Field(default_factory=list)
    top_pairs: list[GscSegmentPairItem] = Field(default_factory=list)
