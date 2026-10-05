"""Lint-style test to track direct conn.execute() call sites using AST analysis.

This test ensures that new code uses the shopifyseo.db.execute wrapper instead
of calling conn.execute() directly. Direct calls are tracked in a per-file count
allowlist that can only shrink over time as code is migrated.

The allowlist helps ensure:
1. New code uses the wrapper for proper % escaping on PostgreSQL
2. Migration progress is visible and tracked
3. CI fails if new direct execute sites are added

Migration plan:
- PR2: Infrastructure + team_tasks.py + api_usage.py
- PR3: route 7 sqlite3.connect sites through get_connection()
- PR4: portable SQL (ON CONFLICT, insert_returning_id, dialect helpers)
- PR5: write_tx / lock retry (BEGIN IMMEDIATE sites removed from app code)
- PR6a: dashboard type/dialect cleanup (schema helpers replace PRAGMA / sqlite_master)
- PR6b: backend services/routers type/dialect cleanup (schema helpers replace PRAGMA)
- Later PRs: remaining conn.execute() sites (see ALLOWED_DIRECT_EXECUTE_COUNTS)

Using AST-based counting makes the test robust against:
- Line number shifts from comments, blank lines, or unrelated edits
- False positives from comments or strings containing patterns
"""
import ast
from pathlib import Path

import pytest


ALLOWED_DIRECT_EXECUTE_COUNTS: dict[str, int] = {
    # Backend app layer
    "backend/app/db.py": 2,
    "backend/app/routers/article_ideas.py": 1,
    "backend/app/routers/blogs.py": 12,
    "backend/app/routers/internal_links.py": 27,
    "backend/app/routers/keywords.py": 14,
    "backend/app/services/article_service.py": 1,
    "backend/app/services/image_seo_service/_catalog.py": 9,
    "backend/app/services/image_seo_service/_optimizer.py": 3,
    "backend/app/services/index_evidence.py": 2,
    "backend/app/services/keyword_clustering/_context.py": 1,
    "backend/app/services/keyword_clustering/_crud.py": 14,
    "backend/app/services/keyword_clustering/_gaps.py": 6,
    "backend/app/services/keyword_clustering/_generation.py": 5,
    "backend/app/services/keyword_clustering/_planning.py": 3,
    "backend/app/services/keyword_clustering/_storage.py": 8,
    "backend/app/services/keyword_clustering/_store_fit.py": 1,
    "backend/app/services/keyword_research/competitor_blocklist.py": 3,
    "backend/app/services/keyword_research/keyword_db.py": 20,
    "backend/app/services/keyword_research/research_runner.py": 1,
    "backend/app/services/open_page_rank.py": 8,
    "backend/app/services/opportunities_service.py": 6,
    "backend/app/services/overview_results.py": 2,
    "backend/app/services/rank_tracking.py": 38,
    "backend/app/services/team_tasks.py": 6,
    # shopifyseo library
    "shopifyseo/api_usage.py": 11,
    "shopifyseo/article_draft_retrieval.py": 8,
    "shopifyseo/catalog_image_work.py": 6,
    "shopifyseo/dashboard_actions/_sync_pagespeed.py": 1,
    "shopifyseo/dashboard_ai_engine_parts/_article_draft.py": 14,
    "shopifyseo/dashboard_ai_engine_parts/_article_ideas.py": 4,
    "shopifyseo/dashboard_ai_engine_parts/context.py": 8,
    "shopifyseo/dashboard_ai_engine_parts/generation.py": 1,
    "shopifyseo/dashboard_article_ideas.py": 55,
    "shopifyseo/dashboard_google/_auth.py": 4,
    "shopifyseo/dashboard_google/_cache.py": 5,
    "shopifyseo/dashboard_google/_ga4.py": 1,
    "shopifyseo/dashboard_google/_gsc.py": 4,
    "shopifyseo/dashboard_queries/_basic_fetchers.py": 25,
    "shopifyseo/dashboard_queries/_editors.py": 5,
    "shopifyseo/dashboard_queries/_gsc_dimensions.py": 2,
    "shopifyseo/dashboard_queries/_object_detail.py": 14,
    "shopifyseo/dashboard_queries/_seo_facts.py": 2,
    "shopifyseo/dashboard_queries/_text_tokens.py": 4,
    "shopifyseo/dashboard_queries/_urls.py": 4,
    "shopifyseo/dashboard_store.py": 64,
    "shopifyseo/embedding_store.py": 49,
    "shopifyseo/embedding_sync.py": 1,
    "shopifyseo/index_evidence.py": 14,
    "shopifyseo/internal_links/ai_weave.py": 4,
    "shopifyseo/internal_links/apply.py": 24,
    "shopifyseo/internal_links/auto_apply.py": 3,
    "shopifyseo/internal_links/graph.py": 4,
    "shopifyseo/internal_links/manual_weave.py": 7,
    "shopifyseo/internal_links/pipeline.py": 24,
    "shopifyseo/internal_links/safety.py": 1,
    "shopifyseo/internal_links/store.py": 8,
    "shopifyseo/internal_links/write_time.py": 4,
    "shopifyseo/opportunity_tasks.py": 17,
    "shopifyseo/product_linkability.py": 4,
    "shopifyseo/rank_tracking/store.py": 11,
    "shopifyseo/shopify_catalog_sync/__init__.py": 11,
    "shopifyseo/shopify_catalog_sync/blogs.py": 13,
    "shopifyseo/shopify_catalog_sync/collections.py": 9,
    "shopifyseo/shopify_catalog_sync/db.py": 10,
    "shopifyseo/shopify_catalog_sync/page_template_enrichment.py": 2,
    "shopifyseo/shopify_catalog_sync/pages.py": 1,
    "shopifyseo/shopify_catalog_sync/products.py": 11,
    "shopifyseo/shopify_image_cache.py": 14,
    # DB layer internals (OK to use direct execute)
    "shopifyseo/db/connect.py": 3,
    "shopifyseo/db/helpers.py": 20,
    "shopifyseo/db/identity.py": 4,
}

EXCLUDED_DIRS = {
    "tests",
    "scripts",
    ".venv",
    "node_modules",
    "__pycache__",
    ".git",
    "frontend",
    "docs",
}


class ExecuteCallCounter(ast.NodeVisitor):
    """AST visitor that counts .execute(), .executemany(), .executescript() calls."""

    def __init__(self):
        self.count = 0

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Attribute):
            method_name = node.func.attr
            if method_name in ("execute", "executemany", "executescript"):
                self.count += 1
        self.generic_visit(node)


def _count_execute_calls(file_path: Path) -> int:
    """Count all .execute/.executemany/.executescript calls in a Python file using AST."""
    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
        counter = ExecuteCallCounter()
        counter.visit(tree)
        return counter.count
    except (SyntaxError, UnicodeDecodeError):
        return 0


def _find_all_execute_counts() -> dict[str, int]:
    """Find execute call counts for all Python files in the codebase."""
    workspace = Path(__file__).parent.parent
    counts: dict[str, int] = {}

    for py_file in workspace.rglob("*.py"):
        rel_path = py_file.relative_to(workspace)

        if any(part in EXCLUDED_DIRS for part in rel_path.parts):
            continue

        if str(rel_path) == "shopifyseo/db/execute.py":
            continue

        count = _count_execute_calls(py_file)
        if count > 0:
            counts[str(rel_path)] = count

    return counts


def test_no_new_direct_execute_sites():
    """Fail if any file has MORE execute calls than allowed."""
    current_counts = _find_all_execute_counts()
    violations = []

    for file_path, count in current_counts.items():
        allowed = ALLOWED_DIRECT_EXECUTE_COUNTS.get(file_path, 0)
        if count > allowed:
            if allowed == 0:
                violations.append(f"{file_path}: {count} new calls (not in allowlist)")
            else:
                violations.append(f"{file_path}: {count} calls (allowed: {allowed}, +{count - allowed})")

    if violations:
        violation_list = "\n  ".join(sorted(violations))
        pytest.fail(
            f"Found files with more direct execute calls than allowed:\n  {violation_list}\n\n"
            "Use shopifyseo.db.execute() instead of conn.execute() for proper "
            "PostgreSQL % escaping. If this is intentional, increase the count in "
            "ALLOWED_DIRECT_EXECUTE_COUNTS."
        )


def test_allowlist_not_too_generous():
    """Fail if any file has FEWER execute calls than allowed (allowlist should shrink)."""
    current_counts = _find_all_execute_counts()
    over_allowed = []

    for file_path, allowed in ALLOWED_DIRECT_EXECUTE_COUNTS.items():
        actual = current_counts.get(file_path, 0)
        if actual < allowed:
            over_allowed.append(f"lower allowlist for {file_path} to {actual} (currently {allowed})")

    if over_allowed:
        msg_list = "\n  ".join(sorted(over_allowed))
        pytest.fail(
            f"Allowlist counts are too high for some files:\n  {msg_list}\n\n"
            "Update ALLOWED_DIRECT_EXECUTE_COUNTS to match the current (lower) counts."
        )


def test_report_migration_progress():
    """Report the current migration progress."""
    current_counts = _find_all_execute_counts()
    total_current = sum(current_counts.values())
    total_allowed = sum(ALLOWED_DIRECT_EXECUTE_COUNTS.values())

    print(f"\n[MIGRATION PROGRESS]")
    print(f"  Total direct execute calls: {total_current}")
    print(f"  Total allowed in allowlist: {total_allowed}")
    print(f"  Files with execute calls: {len(current_counts)}")
