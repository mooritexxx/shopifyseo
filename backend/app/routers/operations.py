from fastapi import APIRouter, HTTPException, Query, status

from backend.app.schemas.common import SuccessResponse, success_response
from backend.app.schemas.operations import (
    ActionMessagePayload,
    AnthropicModelsPayload,
    AnthropicModelsRequestPayload,
    GeminiModelsPayload,
    GeminiModelsRequestPayload,
    GoogleSelectionPayload,
    GoogleSignalsPayload,
    OpenRouterModelsPayload,
    OpenRouterModelsRequestPayload,
    OllamaModelsPayload,
    OllamaModelsRequestPayload,
    SettingsAiTestPayload,
    GoogleAdsTestPayload,
    SettingsPayload,
    SettingsUpdatePayload,
)
from backend.app.services.dashboard_service import (
    get_anthropic_models,
    get_gemini_models,
    get_google_signals_data,
    get_ollama_models,
    get_openrouter_models,
    get_settings_data,
    refresh_google_summary,
    save_google_selection,
    save_settings,
    test_ai_connection,
    test_google_ads_connection,
    test_image_model,
    test_vision_model,
)


router = APIRouter(prefix="/api", tags=["operations"])


@router.get("/google-signals", response_model=SuccessResponse[GoogleSignalsPayload])
def google_signals():
    return success_response(get_google_signals_data())


@router.post("/google-signals/site", response_model=SuccessResponse[ActionMessagePayload])
def google_signals_site(payload: GoogleSelectionPayload):
    return success_response({"message": save_google_selection(payload.site_url, payload.ga4_property_id)})


@router.post("/google-signals/refresh", response_model=SuccessResponse[ActionMessagePayload])
def google_signals_refresh(payload: ActionMessagePayload):
    scope = (payload.result or {}).get("scope") or "search_console_summary"
    return success_response({"message": refresh_google_summary(scope)})


@router.get("/settings", response_model=SuccessResponse[SettingsPayload])
def settings():
    return success_response(get_settings_data())


@router.post("/settings", response_model=SuccessResponse[ActionMessagePayload])
def settings_save(payload: SettingsUpdatePayload):
    return success_response({"message": save_settings(payload.model_dump())})


@router.post("/settings/ai-test", response_model=SuccessResponse[ActionMessagePayload])
def settings_ai_test(payload: SettingsAiTestPayload):
    try:
        data = payload.model_dump()
        target = data.pop("target", "generation")
        result = test_ai_connection(data, target=target)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    return success_response({"message": "AI connection successful", "result": result})


@router.post("/settings/image-model-test", response_model=SuccessResponse[ActionMessagePayload])
def settings_image_model_test(payload: SettingsUpdatePayload):
    try:
        result = test_image_model(payload.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    return success_response({"message": "Sample image generated successfully", "result": result})


@router.post("/settings/vision-model-test", response_model=SuccessResponse[ActionMessagePayload])
def settings_vision_model_test(payload: SettingsUpdatePayload):
    try:
        result = test_vision_model(payload.model_dump())
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    return success_response({"message": "Vision model test succeeded", "result": result})


@router.post("/settings/google-ads-test", response_model=SuccessResponse[ActionMessagePayload])
def settings_google_ads_test(payload: GoogleAdsTestPayload):
    try:
        raw = (payload.google_ads_developer_token or "").strip()
        result = test_google_ads_connection(override_token=raw if raw else None)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))
    return success_response({"message": "Google Ads API connection OK", "result": result})


@router.post("/settings/ollama-models", response_model=SuccessResponse[OllamaModelsPayload])
def settings_ollama_models(payload: OllamaModelsRequestPayload):
    try:
        return success_response(get_ollama_models(payload.ollama_base_url, payload.ollama_api_key))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post("/settings/anthropic-models", response_model=SuccessResponse[AnthropicModelsPayload])
def settings_anthropic_models(payload: AnthropicModelsRequestPayload):
    try:
        return success_response(get_anthropic_models(payload.anthropic_api_key))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post("/settings/gemini-models", response_model=SuccessResponse[GeminiModelsPayload])
def settings_gemini_models(payload: GeminiModelsRequestPayload):
    try:
        return success_response(get_gemini_models(payload.gemini_api_key))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.post("/settings/openrouter-models", response_model=SuccessResponse[OpenRouterModelsPayload])
def settings_openrouter_models(payload: OpenRouterModelsRequestPayload):
    try:
        return success_response(get_openrouter_models(payload.openrouter_api_key))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))


@router.get("/usage/summary")
def usage_summary(days: int = Query(default=30, ge=1, le=365)):
    from backend.app.db import open_db_connection
    from shopifyseo.api_usage import get_usage_summary
    conn = open_db_connection()
    try:
        data = get_usage_summary(conn, days=days)
    finally:
        conn.close()
    return success_response(data)
