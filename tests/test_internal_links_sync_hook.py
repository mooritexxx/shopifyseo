"""The post-sync hook starts an internal link refresh thread."""

from unittest.mock import patch

from shopifyseo.dashboard_actions import _sync


def test_start_internal_link_refresh_runs_pipeline():
    with patch("shopifyseo.internal_links.pipeline.generate_link_suggestions") as gen, \
         patch.object(_sync, "_db_connect_for_actions") as connect:
        thread = _sync._start_internal_link_refresh("/tmp/x.sqlite3")
        thread.join(timeout=5)
        assert gen.called
        assert connect.called
