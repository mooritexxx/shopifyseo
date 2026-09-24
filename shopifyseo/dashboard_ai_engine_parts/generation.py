import copy
import datetime
import json
import logging
import re
import sqlite3
import time
from typing import Callable

from shopifyseo.exceptions import AICancelledError

logger = logging.getLogger(__name__)

from .config import REGENERABLE_FIELDS
from .context import condensed_context, object_context, signal_availability_summary
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
    check_title_puff_redundancy,
    clamp_generated_seo_field,
    description_needs_retry,
    validate_commonwealth_spelling,
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
        raise AICancelledError()


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
    conn: sqlite3.Connection | None = None,
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
    sys_prompt = field_system_prompt(object_type, field, prompt_profile, conn=conn)
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
    step_total = len(full_generation_fields) + 2  # +1 context/QA, +1 saving
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
                conn=conn,
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
    except AICancelledError:
        raise
    except Exception as exc:
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
        stage="validating_qa",
        step_index=step_total - 1,
        step_total=step_total,
        model="validator",
        message="Running QA validation",
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

    # Run QA validation
    from .qa import _score_body, _score_description, validate_body_spec_claims
    from .config import QA_SCORE_FLOOR, BODY_MIN_LENGTH
    from .context import product_specs as _extract_product_specs
    qa_score, qa_issues = validate_output(object_type, recommendation)
    qa_floor = QA_SCORE_FLOOR.get(object_type, 4) / 10.0

    # Check if seo_title has puff redundancy or spelling issues — retry once if so
    title_retried = False
    if object_type == "product":
        detail_payload = context.get("detail") or {}
        product_title = (detail_payload.get("product") or {}).get("title", "")
        puff_ok, puff_issues = check_title_puff_redundancy(product_title, recommendation["seo_title"])
        _, title_spelling_issues = validate_commonwealth_spelling(recommendation["seo_title"])

        if not puff_ok or title_spelling_issues:
            retry_reason = puff_issues[0] if puff_issues else f"spelling: {title_spelling_issues[0]}"
            logger.info(f"SEO title needs retry ({retry_reason}) for {object_type}/{handle}")
            _emit_progress(
                progress_callback,
                stage="retrying_seo_title",
                step_index=step_total - 1,
                step_total=step_total,
                model=_provider_display(generation_provider, generation_model),
                message=f"SEO title failed validation ({retry_reason}), retrying once",
            )
            try:
                # Build accepted fields without seo_title (we're regenerating it)
                retry_accepted = {k: v for k, v in accepted_fields.items() if k != "seo_title"}
                retry_context = _context_with_accepted_fields(context, retry_accepted)
                retry_prompt_ctx = prompt_context(retry_context)
                # Retry title generation
                retry_result = _generate_single_field_core(
                    settings=settings,
                    context=context,
                    object_type=object_type,
                    field="seo_title",
                    accepted_fields=retry_accepted,
                    prompt_context_precomputed=retry_prompt_ctx,
                    signal_narrative_precomputed=None,
                    progress_callback=progress_callback,
                    cancel_callback=cancel_callback,
                    step_index=step_total - 1,
                    step_total=step_total,
                    conn=conn,
                )
                retry_title = clamp_generated_seo_field("seo_title", retry_result["value"])
                retry_puff_ok, retry_puff_issues = check_title_puff_redundancy(product_title, retry_title)
                _, retry_title_spelling = validate_commonwealth_spelling(retry_title)

                # Accept retry if it fixes the issues
                retry_is_better = (
                    (not puff_ok and retry_puff_ok) or
                    (len(retry_title_spelling) < len(title_spelling_issues)) or
                    (retry_puff_ok and len(retry_puff_issues) < len(puff_issues))
                )
                if retry_is_better:
                    recommendation["seo_title"] = retry_title
                    generated_fields["seo_title"]["value"] = retry_title
                    review_actions["seo_title"] = retry_result.get("review_action", "")
                    title_retried = True
                    logger.info(f"SEO title retry improved for {object_type}/{handle}")
                else:
                    logger.info(f"SEO title retry did not improve for {object_type}/{handle}")
            except Exception as e:
                logger.warning(f"SEO title retry failed for {object_type}/{handle}: {e}")

    # Check if seo_description is too short — retry once if so
    description_retried = False
    needs_desc_retry, desc_retry_reason = description_needs_retry(object_type, recommendation["seo_description"])
    # Also check for US spellings in the description
    _, spelling_issues = validate_commonwealth_spelling(recommendation["seo_description"])
    if needs_desc_retry or spelling_issues:
        retry_reason = desc_retry_reason if needs_desc_retry else f"spelling issues: {spelling_issues[:2]}"
        logger.info(f"SEO description needs retry ({retry_reason}) for {object_type}/{handle}")
        _emit_progress(
            progress_callback,
            stage="retrying_seo_description",
            step_index=step_total - 1,
            step_total=step_total,
            model=_provider_display(generation_provider, generation_model),
            message=f"SEO description failed validation ({retry_reason}), retrying once",
        )
        try:
            # Build accepted fields with current seo_title for complementarity
            retry_accepted = dict(accepted_fields)
            retry_accepted["seo_title"] = recommendation["seo_title"]
            # Build fresh prompt context
            retry_context = _context_with_accepted_fields(context, retry_accepted)
            retry_prompt_ctx = prompt_context(retry_context)
            # Retry description generation
            retry_result = _generate_single_field_core(
                settings=settings,
                context=context,
                object_type=object_type,
                field="seo_description",
                accepted_fields=retry_accepted,
                prompt_context_precomputed=retry_prompt_ctx,
                signal_narrative_precomputed=None,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
                step_index=step_total - 1,
                step_total=step_total,
                conn=conn,
            )
            retry_desc = clamp_generated_seo_field("seo_description", retry_result["value"])
            retry_desc_score, _ = _score_description(object_type, retry_desc)
            _, retry_spelling_issues = validate_commonwealth_spelling(retry_desc)
            original_desc_score, _ = _score_description(object_type, recommendation["seo_description"])

            # Accept retry if it's better (longer or better score, and fewer spelling issues)
            retry_is_better = (
                (retry_desc_score > original_desc_score and len(retry_spelling_issues) <= len(spelling_issues)) or
                (len(retry_spelling_issues) < len(spelling_issues))
            )
            if retry_is_better:
                recommendation["seo_description"] = retry_desc
                generated_fields["seo_description"]["value"] = retry_desc
                review_actions["seo_description"] = retry_result.get("review_action", "")
                description_retried = True
                logger.info(f"SEO description retry improved (length={len(retry_desc)}) for {object_type}/{handle}")
            else:
                logger.info(f"SEO description retry did not improve (retry_length={len(retry_desc)}) for {object_type}/{handle}")
        except Exception as e:
            logger.warning(f"SEO description retry failed for {object_type}/{handle}: {e}")

    # Check if body specifically fails the floor — retry once if so
    body_retried = False
    body_score, body_issues = _score_body(object_type, recommendation["body"])
    body_min_length = BODY_MIN_LENGTH.get(object_type, 300)

    # For products, also validate spec claims (puff count, nicotine, battery)
    spec_claim_issues: list[str] = []
    if object_type == "product":
        detail_payload = context.get("detail") or {}
        primary = detail_payload.get("product") or {}
        specs = _extract_product_specs(primary, detail_payload)
        _, spec_claim_issues = validate_body_spec_claims(recommendation["body"], specs)
        if spec_claim_issues:
            logger.info(f"Body has unsupported spec claims for {object_type}/{handle}: {spec_claim_issues}")

    # Retry body if it fails QA floor OR has unsupported spec claims
    should_retry_body = body_issues or body_score < 0.7 or spec_claim_issues
    if should_retry_body:
        retry_reason = "spec claims" if spec_claim_issues else f"QA score={body_score:.2f}"
        logger.info(f"Body QA failed ({retry_reason}), attempting retry for {object_type}/{handle}")
        _emit_progress(
            progress_callback,
            stage="retrying_body",
            step_index=step_total - 1,
            step_total=step_total,
            model=_provider_display(generation_provider, generation_model),
            message=f"Body failed validation ({retry_reason}), retrying once",
        )
        try:
            # Update accepted fields with current values for retry
            retry_accepted = dict(accepted_fields)
            retry_accepted["seo_title"] = recommendation["seo_title"]
            retry_accepted["seo_description"] = recommendation["seo_description"]
            # Build fresh prompt context with accepted fields
            retry_context = _context_with_accepted_fields(context, retry_accepted)
            retry_prompt_ctx = prompt_context(retry_context)
            # Retry body generation
            retry_result = _generate_single_field_core(
                settings=settings,
                context=context,
                object_type=object_type,
                field="body",
                accepted_fields=retry_accepted,
                prompt_context_precomputed=retry_prompt_ctx,
                signal_narrative_precomputed=None,
                progress_callback=progress_callback,
                cancel_callback=cancel_callback,
                step_index=step_total - 1,
                step_total=step_total,
                conn=conn,
            )
            retry_body = ensure_link_titles(retry_result["value"], conn)
            retry_body_score, retry_body_issues = _score_body(object_type, retry_body)

            # For products, also check spec claims on retry
            retry_spec_issues: list[str] = []
            if object_type == "product" and specs:
                _, retry_spec_issues = validate_body_spec_claims(retry_body, specs)

            # Accept retry if it's better (better score or fewer spec issues)
            retry_is_better = (
                (retry_body_score > body_score) or
                (len(retry_spec_issues) < len(spec_claim_issues))
            )
            if retry_is_better:
                recommendation["body"] = retry_body
                body_score = retry_body_score
                body_issues = retry_body_issues
                spec_claim_issues = retry_spec_issues
                generated_fields["body"]["value"] = retry_body
                review_actions["body"] = retry_result.get("review_action", "")
                body_retried = True
                logger.info(f"Body retry improved (score={body_score:.2f}, spec_issues={len(spec_claim_issues)}) for {object_type}/{handle}")
            else:
                logger.info(f"Body retry did not improve (retry_score={retry_body_score:.2f}, retry_spec_issues={len(retry_spec_issues)}) for {object_type}/{handle}")
        except Exception as e:
            logger.warning(f"Body retry failed for {object_type}/{handle}: {e}")

    # Re-run QA validation after potential title, description, or body retry
    if title_retried or description_retried or body_retried:
        qa_score, qa_issues = validate_output(object_type, recommendation)

    _emit_progress(
        progress_callback,
        stage="saving_result",
        step_index=step_total,
        step_total=step_total,
        model="database",
        message="Saving recommendation result",
    )

    recommendation["_meta"] = {
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
    }
    # Populate _qa with validation results
    all_issues = list(qa_issues)
    if spec_claim_issues:
        all_issues.extend(spec_claim_issues)
    recommendation["_qa"] = {
        "score": round(qa_score, 2),
        "floor": qa_floor,
        "passed": qa_score >= qa_floor and not spec_claim_issues,
        "issues": all_issues,
        "spec_claim_issues": spec_claim_issues,
        "title_retried": title_retried,
        "description_retried": description_retried,
        "body_retried": body_retried,
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
            conn=conn,
        )
        final_value = result["value"]
        if field == "body":
            final_value = ensure_link_titles(final_value, conn)
        review_action = result["review_action"]
    except AICancelledError:
        raise
    except Exception as exc:
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

