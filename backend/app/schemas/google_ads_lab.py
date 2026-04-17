from typing import Any, Literal

from pydantic import BaseModel, Field


class GoogleAdsLabContextPayload(BaseModel):
    google_configured: bool
    google_connected: bool
    customer_id: str = ""
    login_customer_id_default: str = Field(
        default="",
        description="Default login-customer-id from Settings (MCC) for lab requests.",
    )
    developer_token_configured: bool
    developer_token_source: str = Field(
        default="unset",
        description="env | db | unset — where GOOGLE_ADS_DEVELOPER_TOKEN was resolved from.",
    )
    lab_hints: list[str] = Field(default_factory=list)


class GoogleAdsLabInvokePayload(BaseModel):
    rpc_method: Literal[
        "generateKeywordIdeas",
        "generateKeywordHistoricalMetrics",
        "generateKeywordForecastMetrics",
        "generateAdGroupThemes",
    ]
    body: dict[str, Any] = Field(default_factory=dict)
    customer_id: str = ""
    login_customer_id: str = ""


class GoogleAdsLabPlanningPayload(BaseModel):
    """How KeywordPlanIdeaService was called after MCC → client resolution."""

    url_customer_id: str
    login_customer_id: str = ""
    note: str = ""


class GoogleAdsLabInvokeResponse(BaseModel):
    result: dict[str, Any]
    planning: GoogleAdsLabPlanningPayload
