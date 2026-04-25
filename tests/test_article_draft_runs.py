import sqlite3

from shopifyseo.dashboard_store import (
    create_article_draft_run,
    ensure_dashboard_schema,
    get_article_draft_run,
    update_article_draft_run,
)


def test_article_draft_run_persists_json_checkpoints():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    ensure_dashboard_schema(conn)

    run_id = create_article_draft_run(conn, {"topic": "Disposable vape guide"})
    update_article_draft_run(
        conn,
        run_id,
        status="running",
        current_step="write_sections",
        seo_brief_json={"required_coverage": {"keywords": ["disposable vapes"]}},
        outline_json={"sections": [{"heading": "Intro"}]},
        article_memory_json={"covered_keywords": ["disposable vapes"]},
        checkpoints_json={"completed_batches": 1, "html_parts": ["<p>body</p>"]},
    )

    run = get_article_draft_run(conn, run_id)

    assert run is not None
    assert run["request"]["topic"] == "Disposable vape guide"
    assert run["seo_brief"]["required_coverage"]["keywords"] == ["disposable vapes"]
    assert run["outline"]["sections"][0]["heading"] == "Intro"
    assert run["article_memory"]["covered_keywords"] == ["disposable vapes"]
    assert run["checkpoints"]["completed_batches"] == 1
