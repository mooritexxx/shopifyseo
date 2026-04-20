"""Tests for local product gallery image cache."""

import hashlib
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from shopifyseo.product_image_seo import normalize_shopify_image_url
from shopifyseo.shopify_image_cache import (
    _worker_fetch,
    catalog_gallery_image_cached_locally,
    image_cache_root,
    invalidate_product_image_cache_entry,
    local_relpath_for,
    preferred_mime_from_bytes,
    product_image_file_cache_index,
    read_cached_product_image,
    warm_product_image_cache,
)
from shopifyseo.shopify_catalog_sync.db import ensure_schema


def test_preferred_mime_sniffs_webp_over_jpeg_header() -> None:
    webp_magic = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00\x00\x00\x00"
    assert preferred_mime_from_bytes(webp_magic, "image/jpeg") == "image/webp"


def test_local_relpath_stable() -> None:
    gid = "gid://shopify/MediaImage/123"
    a = local_relpath_for(gid)
    b = local_relpath_for(gid)
    assert a == b
    assert "/" in a
    assert a.endswith(".bin")


def test_worker_fetch_force_refresh_skips_conditional_branch(tmp_path: Path) -> None:
    """With force_refresh, do not use HEAD/If-None-Match path — one unconditional GET."""
    root = tmp_path
    rel = "aa/bb/cc.bin"
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"old")
    crow = {"local_relpath": rel, "etag": '"same"', "last_modified": ""}

    class FakeResp:
        status_code = 200
        content = b"new-bytes-here"
        headers = {"Content-Type": "image/png", "ETag": '"new"'}

        def raise_for_status(self) -> None:
            return None

    fake_inst = MagicMock()
    fake_inst.get.return_value = FakeResp()

    with patch("shopifyseo.shopify_image_cache.requests.Session", return_value=fake_inst):
        out = _worker_fetch(
            "gid://shopify/MediaImage/1",
            "https://cdn.shopify.com/x.png",
            "https://cdn.shopify.com/x.png",
            crow,
            root,
            force_refresh=True,
        )
    assert out.kind == "downloaded"
    assert out.body == b"new-bytes-here"
    fake_inst.get.assert_called_once()
    _args, kwargs = fake_inst.get.call_args
    assert "If-None-Match" not in (kwargs.get("headers") or {})


def test_warm_empty_catalog_no_crash() -> None:
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "t.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        conn.commit()
        conn.close()
        progress_calls: list[tuple[int, int]] = []
        stats = warm_product_image_cache(
            db_path,
            max_workers=2,
            progress_callback=lambda d, t: progress_calls.append((d, t)),
            force_refresh=True,
        )
        assert stats["downloaded"] == 0
        assert stats["skipped"] == 0
        assert stats["errors"] == 0
        assert progress_calls == [(0, 0)]


def test_read_cache_miss() -> None:
    with tempfile.TemporaryDirectory() as td:
        db_path = Path(td) / "t.sqlite3"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        conn.commit()
        out = read_cached_product_image(db_path, conn, "gid://x/1", "https://cdn.shopify.com/a.jpg")
        assert out is None
        conn.close()


def test_invalidate_removes_row(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    root = image_cache_root(db_path)
    root.mkdir(parents=True, exist_ok=True)
    rel = local_relpath_for("gid://shopify/MediaImage/9")
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    h = hashlib.sha256(b"x").hexdigest()
    conn.execute(
        """
        INSERT INTO product_image_file_cache (
          image_shopify_id, normalized_url, local_relpath, etag, last_modified,
          content_length, sha256_hex, mime, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        ("gid://shopify/MediaImage/9", "https://cdn.shopify.com/x", rel, None, None, 1, h, "image/jpeg", "2020-01-01"),
    )
    conn.commit()
    invalidate_product_image_cache_entry(db_path, conn, "gid://shopify/MediaImage/9")
    conn.commit()
    assert not p.is_file()
    n = conn.execute("SELECT COUNT(*) FROM product_image_file_cache").fetchone()[0]
    assert n == 0
    conn.close()


def test_catalog_gallery_image_cached_locally(tmp_path: Path) -> None:
    db_path = tmp_path / "db.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    root = image_cache_root(db_path)
    root.mkdir(parents=True, exist_ok=True)
    gid = "gid://shopify/MediaImage/42"
    rel = local_relpath_for(gid)
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"img")
    h = hashlib.sha256(b"img").hexdigest()
    url = "https://cdn.shopify.com/files/foo.png?v=1"
    nu = normalize_shopify_image_url(url)
    conn.execute(
        """
        INSERT INTO product_image_file_cache (
          image_shopify_id, normalized_url, local_relpath, etag, last_modified,
          content_length, sha256_hex, mime, updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (gid, nu, rel, None, None, 3, h, "image/png", "2020-01-01"),
    )
    conn.commit()
    idx = product_image_file_cache_index(conn)
    assert catalog_gallery_image_cached_locally(idx, root, image_shopify_id=gid, catalog_image_url=url) is True
    assert (
        catalog_gallery_image_cached_locally(
            idx, root, image_shopify_id=gid, catalog_image_url="https://other.cdn/file.jpg"
        )
        is False
    )
    assert catalog_gallery_image_cached_locally(idx, root, image_shopify_id="gid://missing/1", catalog_image_url=url) is False
    conn.close()
