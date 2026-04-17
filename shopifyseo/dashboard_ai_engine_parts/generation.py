import copy
import datetime
import json
import logging
import re
import sqlite3
import time
from typing import Callable

logger = logging.getLogger(__name__)

from .config import REGENERABLE_FIELDS
from .context import condensed_context, object_context, prompt_context, signal_availability_summary
from .images import (
    extract_first_paragraph_plain_text,
    inject_article_body_image,
    inject_article_body_images,
    parse_h2_sections,
    test_image_model,
    try_prepare_article_images_bundle,
)
from .prompts import field_review_response_schema, field_review_user_prompt, field_system_prompt, field_user_prompt, prompt_context, review_system_prompt, single_field_response_schema
from .providers import (
    AIProviderRequestError,
    _call_ai,
    _friendly_ai_error,
    _provider_display,
    _require_provider_credentials,
)
from .qa import (
    RecommendationValidationError,
    build_retry_feedback,
    build_retry_feedback_from_error,
    clamp_generated_seo_field,
    validate_output,
    validate_single_field,
)
from .settings import ai_configured, ai_settings

ProgressCallback = Callable[[dict], None]
CancelCallback = Callable[[], bool]

# Re-exported from sub-modules for backward compatibility
from ._article_ideas import generate_article_ideas
from ._article_draft import (
    sanitize_article_internal_links,
    generate_article_draft,
    ensure_link_titles,
)



def _emit_progress(progress_callback: ProgressCallback | None, **payload) -> None:
    if progress_callback is not None:
        progress_callback(payload)


def insert_recommendation_record(
    conn: sqlite3.Connection,
    *,
    object_type: str,
    handle: str,
    status: str,
    priority: str,
    summary: str,
    details: dict | None,
    source: str,
    model: str,
    prompt_version: str,
    error_message: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO seo_recommendations(
          object_type, object_handle, category, priority, summary, details_json, source,
          status, model, prompt_version, error_message, updated_at, created_at
        )
        VALUES(?, ?, 'content_brief', ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (object_type, handle, priority, summary, json.dumps(details, ensure_ascii=True) if details is not None else "", source, status, model, prompt_version, error_message),
    )
    conn.commit()


def _build_error_details(candidate: dict, model: str, prompt_version: str, requested_prompt_version: str, prompt_profile: str, error_message: str, context: dict) -> dict | None:
    if not candidate:
        return None
    error_details = dict(candidate)
    meta = dict(error_details.get("_meta") or {})
    qa = dict(error_details.get("_qa") or {})
    meta.update({
        "model": model,
        "prompt_version": prompt_version,
        "requested_prompt_version": requested_prompt_version,
        "prompt_profile": prompt_profile,
        "generated_at": int(time.time()),
        "signal_availability": signal_availability_summary(context),
        "failed": True,
        "failure_reason": error_message,
        "qa_score": qa.get("score"),
    })
    error_details["_meta"] = meta
    return error_details


def _build_single_field_error_details(
    *,
    field: str,
    value: str,
    accepted_fields: dict,
    model: str,
    prompt_version: str,
    requested_prompt_version: str,
    prompt_profile: str,
    error_message: str,
    context: dict,
    review_action: str,
) -> dict:
    details = {
        field: value,
        "accepted_fields": accepted_fields,
        "_meta": {
            "field": field,
            "model": model,
            "prompt_version": prompt_version,
            "requested_prompt_version": requested_prompt_version,
            "prompt_profile": prompt_profile,
            "generated_at": int(time.time()),
            "signal_availability": signal_availability_summary(context),
            "failed": True,
            "failure_reason": error_message,
            "review_action": review_action,
            "single_field_regeneration": True,
        },
    }
    return details


def _augment_error_details(details: dict | None, exc: Exception) -> dict | None:
    if details is None:
        details = {}
    if isinstance(exc, AIProviderRequestError):
        meta = dict(details.get("_meta") or {})
        meta["ai_request"] = exc.details
        details["_meta"] = meta
    return details


def _raise_if_cancelled(cancel_callback: CancelCallback | None) -> None:
    if cancel_callback and cancel_callback():
        raise RuntimeError("AI generation cancelled by user")


def _context_with_accepted_fields(context: dict, accepted_fields: dict[str, str]) -> dict:
    if not accepted_fields:
        return context
    updated = copy.deepcopy(context)
    detail = updated.get("detail") or {}
    primary = detail.get("product") or detail.get("collection") or detail.get("page") or detail.get("article")
    if not isinstance(primary, dict):
        return updated
    if "seo_title" in accepted_fields:
        primary["seo_title"] = accepted_fields.get("seo_title", "")
    if "seo_description" in accepted_fields:
        primary["seo_description"] = accepted_fields.get("seo_description", "")
    if "body" in accepted_fields:
        if updated.get("object_type") in ("page", "blog_article"):
            primary["body"] = accepted_fields.get("body", "")
        else:
            primary["description_html"] = accepted_fields.get("body", "")
    if "tags" in accepted_fields:
        primary["tags"] = accepted_fields.get("tags", "")
    return updated


def _generate_single_field_core(
    *,
    settings: dict,
    context: dict,
    object_type: str,
    field: str,
    accepted_fields: dict,
    prompt_context_precomputed: dict | None = None,
    signal_narrative_precomputed: str,
    progress_callback: ProgressCallback | None = None,
    cancel_callback: CancelCallback | None = None,
    step_index: int = 0,
    step_total: int = 0,
) -> dict:
    generation_provider = settings["generation_provider"]
    generation_model = settings["generation_model"]
    review_provider = settings["review_provider"]
    review_model = settings["review_model"]
    prompt_version = settings["prompt_version"]
    prompt_profile = settings["prompt_profile"]
    timeout = settings["timeout"]

    _raise_if_cancelled(cancel_callback)
    _emit_progress(
        progress_callback,
        stage=f"preparing_{field}",
        step_index=step_index,
        step_total=step_total,
        model="context",
        message=f"Preparing {field} context",
    )
    effective_context = _context_with_accepted_fields(context, accepted_fields)
    effective_prompt_context = prompt_context_precomputed if prompt_context_precomputed is not None else prompt_context(effective_context)
    field_gen_schema = single_field_response_schema(object_type, field)
    field_rev_schema = field_review_response_schema(object_type, field)
    sys_prompt = field_system_prompt(object_type, field, prompt_profile)
    usr_prompt = field_user_prompt(
        object_type,
        field,
        effective_context,
        accepted_fields,
        prompt_version,
        prompt_context_dict=effective_prompt_context,
        signal_narrative_str=signal_narrative_precomputed,
    )

    _raise_if_cancelled(cancel_callback)
    _emit_progress(
        progress_callback,
        stage=f"generating_{field}",
        step_index=step_index,
        step_total=step_total,
        model=_provider_display(generation_provider, generation_model),
        message=f"Generation started for {field} with {_provider_display(generation_provider, generation_model)}",
    )
    _emit_progress(
        progress_callback,
        stage=f"waiting_generation_{field}",
        step_index=step_index,
        step_total=step_total,
        model=_provider_display(generation_provider, generation_model),
        message=f"Waiting for generation response for {field}",
    )
    draft = _call_ai(
        settings,
        generation_provider,
        generation_model,
        [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": usr_prompt},
        ],
        timeout,
        json_schema=field_gen_schema,
        stage=f"single_field_generate:{field}",
    )
    _raise_if_cancelled(cancel_callback)
    draft_value = str(draft.get(field) or "").strip()
    if not draft_value:
        raise RuntimeError(f"Generation model returned empty {field}")
    _emit_progress(
        progress_callback,
        stage=f"generation_complete_{field}",
        step_index=step_index,
        step_total=step_total,
        model=_provider_display(generation_provider, generation_model),
        message=f"Generation finished for {field}",
    )

    # seo_title and seo_description skip the AI review pass:
    #   - JSON schema already enforces character-length constraints at the API level.
    #   - Field-specific system prompts and tight instructions make a second call redundant.
    #   - Removing the review pass halves the API calls and latency for these two fields.
    # body retains the review pass: structure (5 sections, H2/H3, link integrity) and
    # HTML quality are harder to enforce via schema alone and benefit from a second look.
    review_fields = {"body"}
    if field in review_fields:
        review_usr = field_review_user_prompt(
            field,
            draft_value,
            effective_context,
            accepted_fields,
            prompt_context_dict=effective_prompt_context,
            signal_narrative_str=signal_narrative_precomputed,
        )
        try:
            _raise_if_cancelled(cancel_callback)
            _emit_progress(
                progress_callback,
                stage=f"starting_review_{field}",
                step_index=step_index,
                step_total=step_total,
                model=_provider_display(review_provider, review_model),
                message=f"QA review started for {field}",
            )
            _emit_progress(
                progress_callback,
                stage=f"waiting_review_{field}",
                step_index=step_index,
                step_total=step_total,
                model=_provider_display(review_provider, review_model),
                message=f"Waiting for QA review response for {field}",
            )
            reviewed = _call_ai(
                settings,
                review_provider,
                review_model,
                [
                    {"role": "system", "content": review_system_prompt()},
                    {"role": "user", "content": review_usr},
                ],
                timeout,
                json_schema=field_rev_schema,
                stage=f"single_field_review:{field}",
            )
            _raise_if_cancelled(cancel_callback)
            final_value = str(reviewed.get(field) or draft_value).strip()
            review_action = (reviewed.get("_review") or {}).get(field, "approved")
            review_used = True
            _emit_progress(
                progress_callback,
                stage=f"review_complete_{field}",
                step_index=step_index,
                step_total=step_total,
                model=_provider_display(review_provider, review_model),
                message=f"QA review finished for {field}",
            )
        except Exception:
            final_value = draft_value
            review_action = "review_skipped"
            review_used = False
            _emit_progress(
                progress_callback,
                stage=f"review_skipped_{field}",
                step_index=step_index,
                step_total=step_total,
                model=_provider_display(review_provider, review_model),
                message=f"QA review skipped for {field}",
            )
    else:
        # Review skipped by design for this field — draft value is final.
        final_value = draft_value
        review_action = "review_skipped"
        review_used = False
        _emit_progress(
            progress_callback,
            stage=f"review_skipped_{field}",
            step_index=step_index,
            step_total=step_total,
            model="",
            message=f"Review not required for {field}",
        )

    if field in ("seo_title", "seo_description"):
        final_value = clamp_generated_seo_field(field, final_value)

    _emit_progress(
        progress_callback,
        stage=f"validating_{field}",
        step_index=step_index,
        step_total=step_total,
        model="validator",
        message=f"Validating {field}",
    )
    validate_single_field(object_type, field, final_value, effective_context)
    _raise_if_cancelled(cancel_callback)
    _emit_progress(
        progress_callback,
        stage=f"completed_{field}",
        step_index=step_index,
        step_total=step_total,
        model="validator",
        message=f"{field.replace('_', ' ')} complete",
    )
    return {
        "field": field,
        "value": final_value,
        "generation_model": _provider_display(generation_provider, generation_model),
        "review_model": _provider_display(review_provider, review_model) if review_used else "",
        "review_action": review_action,
        "generated_at": int(time.time()),
    }


def generate_recommendation(
    conn: sqlite3.Connection,
    object_type: str,
    handle: str,
    progress_callback: ProgressCallback | None = None,
    cancel_callback: CancelCallback | None = None,
) -> dict:
    logger.info(
        f"generate_recommendation called (FULL GENERATION): object_type={object_type}, handle={handle}"
    )
    settings = ai_settings(conn)
    generation_provider = settings["generation_provider"]
    generation_model = settings["generation_model"]
    review_provider = settings["review_provider"]
    review_model = settings["review_model"]
    _require_provider_credentials(settings, generation_provider)
    _require_provider_credentials(settings, review_provider)

    context = object_context(conn, object_type, handle)
    # Load cluster context + compute keyword gaps
    try:
        from backend.app.services.keyword_clustering import (
            load_clusters, _get_matched_cluster_keywords, compute_seo_gaps,
        )
        clusters_data = load_clusters(conn)
        from shopifyseo.dashboard_google import get_service_setting as _get_ss
        target_raw = _get_ss(conn, "target_keywords", "{}")
        target_data = json.loads(target_raw) if target_raw else {}

        vendor = ""
        if object_type == "product":
            vendor = (context.get("detail") or {}).get("product", {}).get("vendor", "")

        cluster_ctx, all_kws, primary_kw, kw_map = _get_matched_cluster_keywords(
            clusters_data, target_data, object_type, handle, conn=conn, vendor=vendor,
        )

        if cluster_ctx:
            context["cluster_seo_context"] = cluster_ctx

        if all_kws:
            detail = context.get("detail") or {}
            primary_obj = (
                detail.get("product") or detail.get("collection")
                or detail.get("page") or detail.get("article") or {}
            )
            content_fields = {
                "title": primary_obj.get("title", ""),
                "seo_title": primary_obj.get("seo_title", ""),
                "seo_description": primary_obj.get("seo_description", ""),
                "body": primary_obj.get("description_html") or primary_obj.get("body") or "",
            }
            gaps = compute_seo_gaps(all_kws, content_fields, kw_map, object_type, primary_kw)
            if gaps:
                context["seo_keyword_gaps"] = gaps
    except Exception:
        logger.debug("Failed to load cluster context; proceeding without it")
    # Do not pre-build a single shared narrative here. Passing None lets each field's call to
    # _generate_single_field_core → field_user_prompt / field_review_user_prompt select the
    # correct per-field narrative (build_title_signal_narrative for seo_title,
    # build_description_signal_narrative for seo_description, build_signal_narrative for body).
    signal_narrative_precomputed = None
    prompt_version = settings["prompt_version"]
    requested_prompt_version = settings.get("requested_prompt_version") or prompt_version
    prompt_profile = settings["prompt_profile"]
    _raise_if_cancelled(cancel_callback)
    _emit_progress(
        progress_callback,
        stage="building_context",
        step_index=0,
        step_total=5,
        model="",
        message="Building recommendation context",
    )

    generated_fields: dict[str, dict] = {}
    review_actions: dict[str, str] = {}
    accepted_fields: dict[str, str] = {}
    full_generation_fields = ["seo_title", "seo_description", "body"]
    if object_type == "product":
        full_generation_fields.append("tags")
    elif object_type == "blog_article":
        full_generation_fields.insert(0, "title")
    step_total = len(full_generation_fields) + 2  # +1 context building, +1 saving
    last_error = ""
    priority = context["fact"]["priority"]
    try:
        for idx, field in enumerate(full_generation_fields, start=1):
            _raise_if_cancelled(cancel_callback)
            # Compute prompt_context once per field iteration to avoid redundant curated_primary_object calls
            # This is computed per iteration because accepted_fields changes, but the expensive curated_primary_object
            # result can be reused by signal narrative builders via prompt_context_dict["primary_object"]
            effective_context = _context_with_accepted_fields(context, dict(accepted_fields))
            prompt_context_precomputed = prompt_context(effective_context)
            result = _generate_single_field_core(
                settings=settings,
                context=context,
                object_type=object_type,
                field=field,
                accepted_fields=dict(accepted_fields),
                prompt_context_precomputed=prompt_context_precomputed,
                signal_narrative_precomputed=signal_narrative_precomputed,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
                step_index=idx,
                step_total=step_total,
            )
            generated_fields[field] = result
            review_actions[field] = result.get("review_action") or ""
            accepted_fields[field] = result["value"]

            # Emit field completion event for SSE streaming
            _emit_progress(
                progress_callback,
                stage=f"field_complete_{field}",
                step_index=idx,
                step_total=step_total,
                model=_provider_display(generation_provider, generation_model),
                message=f"Generated {field.replace('_', ' ')}",
                field_complete=field,
                field_value=result["value"],
            )

            # Save partial recommendation after each field completes for real-time updates
            partial_recommendation = {
                "seo_title": clamp_generated_seo_field("seo_title", generated_fields.get("seo_title", {}).get("value", "")),
                "seo_description": clamp_generated_seo_field("seo_description", generated_fields.get("seo_description", {}).get("value", "")),
                "body": generated_fields.get("body", {}).get("value", ""),
            }
            if object_type == "product":
                tags_raw = generated_fields.get("tags", {}).get("value", "")
                if tags_raw:
                    partial_recommendation["tags"] = [t.strip() for t in tags_raw.split(",") if t.strip()]
            if object_type == "blog_article":
                title_val = generated_fields.get("title", {}).get("value", "")
                if title_val:
                    partial_recommendation["title"] = title_val
            partial_recommendation["_meta"] = {
                "generation_model": _provider_display(generation_provider, generation_model),
                "review_model": _provider_display(review_provider, review_model),
                "model": f"{_provider_display(generation_provider, generation_model)}+{_provider_display(review_provider, review_model)}",
                "prompt_version": prompt_version,
                "requested_prompt_version": requested_prompt_version,
                "prompt_profile": prompt_profile,
                "generated_at": int(time.time()),
                "signal_availability": signal_availability_summary(context),
                "review_actions": review_actions,
                "generation_strategy": "split_single_field_calls",
                "generating": True,  # Mark as in-progress
            }
            partial_summary = partial_recommendation.get("seo_title") or f"Generating for {handle}"
            insert_recommendation_record(
                conn,
                object_type=object_type,
                handle=handle,
                status="generating",
                priority=priority,
                summary=partial_summary,
                details=partial_recommendation,
                source="dashboard_ai",
                model=f"{_provider_display(generation_provider, generation_model)}+{_provider_display(review_provider, review_model)}",
                prompt_version=prompt_version,
            )
    except Exception as exc:
        if str(exc) == "AI generation cancelled by user":
            raise
        last_error = str(exc)
        logger.error(
            f"AI generation failed for {object_type}/{handle}: {last_error}",
            exc_info=True,
            extra={
                "object_type": object_type,
                "handle": handle,
                "generation_provider": generation_provider,
                "generation_model": generation_model,
                "review_provider": review_provider,
                "review_model": review_model,
                "prompt_version": prompt_version,
            }
        )
        partial = {
            "seo_title": clamp_generated_seo_field("seo_title", generated_fields.get("seo_title", {}).get("value", "")),
            "seo_description": clamp_generated_seo_field("seo_description", generated_fields.get("seo_description", {}).get("value", "")),
            "body": generated_fields.get("body", {}).get("value", ""),
            "_meta": {
                "review_actions": review_actions,
                "generated_fields": list(generated_fields.keys()),
            },
        }
        if object_type == "product":
            tags_raw = generated_fields.get("tags", {}).get("value", "")
            partial["tags"] = [t.strip() for t in tags_raw.split(",") if t.strip()]
        if object_type == "blog_article":
            title_val = generated_fields.get("title", {}).get("value", "")
            if title_val:
                partial["title"] = title_val
        priority = context["fact"]["priority"]
        error_details = _build_error_details(
            partial,
            _provider_display(generation_provider, generation_model),
            prompt_version,
            requested_prompt_version,
            prompt_profile,
            last_error,
            context,
        )
        error_details = _augment_error_details(error_details, exc)
        insert_recommendation_record(
            conn,
            object_type=object_type,
            handle=handle,
            status="error",
            priority=priority,
            summary=partial.get("seo_title") or f"AI generation failed for {handle}",
            details=error_details,
            source="dashboard_ai",
            model=f"{_provider_display(generation_provider, generation_model)}+{_provider_display(review_provider, review_model)}",
            prompt_version=prompt_version,
            error_message=last_error,
        )
        raise RuntimeError(last_error) from exc

    _raise_if_cancelled(cancel_callback)
    _emit_progress(
        progress_callback,
        stage="saving_result",
        step_index=step_total,
        step_total=step_total,
        model="database",
        message="Saving recommendation result",
    )

    body_html = generated_fields["body"]["value"]
    body_html = ensure_link_titles(body_html, conn)
    recommendation = {
        "seo_title": clamp_generated_seo_field("seo_title", generated_fields["seo_title"]["value"]),
        "seo_description": clamp_generated_seo_field("seo_description", generated_fields["seo_description"]["value"]),
        "body": body_html,
    }
    if object_type == "product":
        tags_raw = generated_fields.get("tags", {}).get("value", "")
        recommendation["tags"] = [t.strip() for t in tags_raw.split(",") if t.strip()]
    if object_type == "blog_article":
        title_val = generated_fields.get("title", {}).get("value", "")
        if title_val:
            recommendation["title"] = title_val
    recommendation["_meta"] = {
        "generation_model": _provider_display(generation_provider, generation_model),
        "review_model": _provider_display(review_provider, review_model),
        "model": f"{_provider_display(generation_provider, generation_model)}+{_provider_display(review_provider, review_model)}",
        "prompt_version": prompt_version,
        "requested_prompt_version": requested_prompt_version,
        "prompt_profile": prompt_profile,
        "generated_at": int(time.time()),
        "signal_availability": signal_availability_summary(context),
        "qa": {},
        "review_actions": review_actions,
        "generation_strategy": "split_single_field_calls",
    }
    priority = context["fact"]["priority"]
    insert_recommendation_record(
        conn, object_type=object_type, handle=handle, status="success",
        priority=priority, summary=recommendation["seo_title"],
        details=recommendation, source="dashboard_ai",
        model=f"{_provider_display(generation_provider, generation_model)}+{_provider_display(review_provider, review_model)}", prompt_version=prompt_version,
    )
    return recommendation


def generate_field_recommendation(
    conn: sqlite3.Connection,
    object_type: str,
    handle: str,
    field: str,
    accepted_fields: dict,
    progress_callback: ProgressCallback | None = None,
    cancel_callback: CancelCallback | None = None,
) -> dict:
    """Regenerate a single field, context-aware of already-accepted sibling fields."""
    logger.info(
        f"generate_field_recommendation called: object_type={object_type}, handle={handle}, field={field}, accepted_fields_keys={list(accepted_fields.keys())}"
    )
    if field not in REGENERABLE_FIELDS:
        raise ValueError(f"Field '{field}' is not regenerable. Must be one of: {REGENERABLE_FIELDS}")

    settings = ai_settings(conn)
    generation_provider = settings["generation_provider"]
    generation_model = settings["generation_model"]
    review_provider = settings["review_provider"]
    review_model = settings["review_model"]
    _require_provider_credentials(settings, generation_provider)
    _require_provider_credentials(settings, review_provider)

    context = object_context(conn, object_type, handle)
    try:
        from backend.app.services.keyword_clustering import (
            load_clusters, _get_matched_cluster_keywords, compute_seo_gaps,
        )
        clusters_data = load_clusters(conn)
        from shopifyseo.dashboard_google import get_service_setting as _get_ss
        target_raw = _get_ss(conn, "target_keywords", "{}")
        target_data = json.loads(target_raw) if target_raw else {}

        vendor = ""
        if object_type == "product":
            vendor = (context.get("detail") or {}).get("product", {}).get("vendor", "")

        cluster_ctx, all_kws, primary_kw, kw_map = _get_matched_cluster_keywords(
            clusters_data, target_data, object_type, handle, conn=conn, vendor=vendor,
        )
        if cluster_ctx:
            context["cluster_seo_context"] = cluster_ctx

        if all_kws:
            merged = _context_with_accepted_fields(context, accepted_fields)
            merged_detail = merged.get("detail") or {}
            primary_obj = (
                merged_detail.get("product") or merged_detail.get("collection")
                or merged_detail.get("page") or merged_detail.get("article") or {}
            )
            content_fields = {
                "title": primary_obj.get("title", ""),
                "seo_title": primary_obj.get("seo_title", ""),
                "seo_description": primary_obj.get("seo_description", ""),
                "body": primary_obj.get("description_html") or primary_obj.get("body") or "",
            }
            gaps = compute_seo_gaps(all_kws, content_fields, kw_map, object_type, primary_kw)
            if gaps:
                context["seo_keyword_gaps"] = gaps
    except Exception:
        logger.debug("Failed to load cluster context for single-field regen; proceeding without it")
    signal_narrative_precomputed = None
    prompt_version = settings["prompt_version"]
    requested_prompt_version = settings.get("requested_prompt_version") or prompt_version
    prompt_profile = settings["prompt_profile"]
    timeout = settings["timeout"]
    priority = context["fact"]["priority"]

    effective_context = _context_with_accepted_fields(context, accepted_fields)
    prompt_context_precomputed = prompt_context(effective_context)

    try:
        result = _generate_single_field_core(
            settings=settings,
            context=context,
            object_type=object_type,
            field=field,
            accepted_fields=accepted_fields,
            prompt_context_precomputed=prompt_context_precomputed,
            signal_narrative_precomputed=signal_narrative_precomputed,
            progress_callback=progress_callback,
            cancel_callback=cancel_callback,
            step_index=1,
            step_total=3,
        )
        final_value = result["value"]
        if field == "body":
            final_value = ensure_link_titles(final_value, conn)
        review_action = result["review_action"]
    except Exception as exc:
        if str(exc) == "AI generation cancelled by user":
            raise
        error_message = _friendly_ai_error(exc) if isinstance(exc, Exception) else str(exc)
        logger.error(
            f"Single field regeneration failed: object_type={object_type}, handle={handle}, field={field}: {error_message}",
            exc_info=True,
            extra={
                "object_type": object_type,
                "handle": handle,
                "field": field,
                "generation_provider": generation_provider,
                "generation_model": generation_model,
                "review_provider": review_provider,
                "review_model": review_model,
                "prompt_version": prompt_version,
            }
        )
        insert_recommendation_record(
            conn,
            object_type=object_type,
            handle=handle,
            status="error",
            priority=priority,
            summary=f"Single-field regeneration failed for {handle}",
            details=_build_single_field_error_details(
                field=field,
                value=accepted_fields.get(field, ""),
                accepted_fields=accepted_fields,
                model=f"{_provider_display(generation_provider, generation_model)}+{_provider_display(review_provider, review_model)}",
                prompt_version=prompt_version,
                requested_prompt_version=requested_prompt_version,
                prompt_profile=prompt_profile,
                error_message=error_message,
                context=context,
                review_action="failed",
            ),
            source="dashboard_ai",
            model=f"{_provider_display(generation_provider, generation_model)}+{_provider_display(review_provider, review_model)}",
            prompt_version=prompt_version,
            error_message=error_message,
        )
        raise RuntimeError(error_message) from exc

    _emit_progress(
        progress_callback,
        stage=f"field_complete_{field}",
        step_index=1,
        step_total=1,
        model=_provider_display(generation_provider, generation_model),
        message=f"Generated {field.replace('_', ' ')}",
        field_complete=field,
        field_value=result["value"],
    )

    return {
        **result,
    }


def test_connection(conn: sqlite3.Connection, settings_override: dict[str, str] | None = None, target: str = "generation") -> dict:
    settings = ai_settings(conn, settings_override)
    normalized_target = (target or "generation").strip().lower()
    if normalized_target == "review":
        provider = settings["review_provider"]
        model = settings["review_model"]
    elif normalized_target == "sidekick":
        provider = settings["sidekick_provider"]
        model = settings["sidekick_model"]
    else:
        normalized_target = "generation"
        provider = settings["generation_provider"]
        model = settings["generation_model"]
    parsed = _call_ai(
        settings,
        provider,
        model,
        [{"role": "system", "content": "Return valid JSON only."}, {"role": "user", "content": '{"ok":true,"message":"test"}'}],
        settings["timeout"],
        stage="settings_test",
    )
    if not isinstance(parsed, dict):
        raise RuntimeError("AI test did not return a JSON object")
    return {
        **parsed,
        "_meta": {
            "target": normalized_target,
            "provider": provider,
            "model": model,
        },
    }

