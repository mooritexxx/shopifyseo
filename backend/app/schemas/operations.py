from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CacheStatusPayload(BaseModel):
    label: str
    kind: str
    text: str
    meta: dict[str, Any] | None = None


class SummaryPeriodPayload(BaseModel):
    start_date: str = ""
    end_date: str = ""


class GscRowPayload(BaseModel):
    keys: list[str] = []
    clicks: float | int = 0
    impressions: float | int = 0
    ctr: float = 0.0
    position: float = 0.0


class GoogleValuePayload(BaseModel):
    value: str = ""


class Ga4RowPayload(BaseModel):
    dimensionValues: list[GoogleValuePayload] = []
    metricValues: list[GoogleValuePayload] = []


class GscPropertyBreakdownWindowPayload(BaseModel):
    start_date: str = ""
    end_date: str = ""


class GscPropertyBreakdownSlicePayload(BaseModel):
    rows: list[GscRowPayload] = []
    error: str = ""
    cache: CacheStatusPayload
    top_bucket_impressions_pct_vs_prior: float | None = None


class GscPropertyBreakdownsPayload(BaseModel):
    """Property-level GSC dimensions (country, device, search appearance) for the MTD overview window."""

    available: bool = False
    period_mode: str = "mtd"
    anchor_date: str = ""
    window: GscPropertyBreakdownWindowPayload = Field(default_factory=GscPropertyBreakdownWindowPayload)
    country: GscPropertyBreakdownSlicePayload
    device: GscPropertyBreakdownSlicePayload
    searchAppearance: GscPropertyBreakdownSlicePayload
    errors: list[dict[str, Any]] = Field(default_factory=list)
    error: str = ""


class SettingsValuesPayload(BaseModel):
    store_name: str = ""
    primary_market_country: str = ""
    dashboard_timezone: str = ""
    store_custom_domain: str = ""
    shopify_shop: str = ""
    shopify_api_version: str = ""
    shopify_client_id: str = ""
    shopify_client_secret: str = ""
    dataforseo_api_login: str = ""
    dataforseo_api_password: str = ""
    google_client_id: str = ""
    google_client_secret: str = ""
    search_console_site: str = ""
    ga4_property_id: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    ollama_api_key: str = ""
    ollama_base_url: str = ""
    ai_generation_provider: str = ""
    ai_generation_model: str = ""
    ai_sidekick_provider: str = ""
    ai_sidekick_model: str = ""
    ai_review_provider: str = ""
    ai_review_model: str = ""
    ai_image_provider: str = ""
    ai_image_model: str = ""
    ai_vision_provider: str = ""
    ai_vision_model: str = ""
    ai_timeout_seconds: str = ""
    ai_max_retries: str = ""
    google_ads_developer_token: str = ""
    google_ads_customer_id: str = ""
    google_ads_login_customer_id: str = ""


class GoogleSignalsPayload(BaseModel):
    configured: bool
    connected: bool
    auth_url: str | None = None
    selected_site: str = ""
    available_sites: list[str]
    ga4_property_id: str = ""
    summary_period: SummaryPeriodPayload
    gsc_pages: list[GscRowPayload]
    gsc_queries: list[GscRowPayload]
    ga4_rows: list[Ga4RowPayload]
    gsc_cache: CacheStatusPayload
    ga4_cache: CacheStatusPayload
    gsc_property_breakdowns: GscPropertyBreakdownsPayload
    error: str = ""


class GoogleSelectionPayload(BaseModel):
    site_url: str = ""
    ga4_property_id: str = ""


class Ga4PropertyPayload(BaseModel):
    property_id: str
    display_name: str
    account_name: str = ""


class GoogleAdsCustomerPayload(BaseModel):
    customer_id: str
    descriptive_name: str = ""
    resource_name: str = ""


class SyncScopeReadyPayload(BaseModel):
    shopify: bool
    gsc: bool
    ga4: bool
    index: bool
    pagespeed: bool
    structured: bool


class SettingsPayload(BaseModel):
    values: SettingsValuesPayload
    google_configured: bool
    google_connected: bool
    ai_configured: bool
    auth_url: str | None = None
    available_gsc_sites: list[str] = Field(default_factory=list)
    available_ga4_properties: list[Ga4PropertyPayload] = Field(default_factory=list)
    available_google_ads_customers: list[GoogleAdsCustomerPayload] = Field(default_factory=list)
    ga4_api_activation_url: str = ""
    sync_scope_ready: SyncScopeReadyPayload


class SettingsUpdatePayload(SettingsValuesPayload):
    model_config = ConfigDict(extra="ignore")


class SettingsAiTestPayload(SettingsUpdatePayload):
    target: str = "generation"


class GoogleAdsTestPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    google_ads_developer_token: str = ""


class ShopifyTestPayload(BaseModel):
    """Optional overrides for Shopify Admin probe; missing fields fall back to DB / env via runtime_setting."""

    model_config = ConfigDict(extra="ignore")

    shopify_shop: str = ""
    shopify_client_id: str = ""
    shopify_client_secret: str = ""
    shopify_api_version: str = ""


class OllamaModelsRequestPayload(BaseModel):
    ollama_base_url: str = ""
    ollama_api_key: str = ""


class OllamaModelsPayload(BaseModel):
    models: list[str]


class AnthropicModelsRequestPayload(BaseModel):
    anthropic_api_key: str = ""


class AnthropicModelsPayload(BaseModel):
    models: list[str]


class GeminiModelsRequestPayload(BaseModel):
    gemini_api_key: str = ""


class GeminiModelsPayload(BaseModel):
    models: list[str]


class OpenRouterModelsRequestPayload(BaseModel):
    openrouter_api_key: str = ""


class OpenRouterModelsPayload(BaseModel):
    models: list[str]


class ActionMessagePayload(BaseModel):
    message: str
    result: dict[str, Any] | None = None
