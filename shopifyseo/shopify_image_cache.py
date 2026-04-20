"""Download product gallery images after catalog sync; skip re-fetch when ETag/304 says unchanged."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Any, Literal

import requests

from shopifyseo.catalog_image_work import build_catalog_image_registry_from_db, count_catalog_images_for_cache_db
from shopifyseo.product_image_seo import infer_image_format_from_bytes, normalize_shopify_image_url
from shopifyseo.shopify_catalog_sync.db import now_iso, open_db

logger = logging.getLogger(__name__)

UA = {
    "User-Agent": "ShopifySEO/1.0 (catalog-image-cache)",
    "Accept": "image/webp,image/avif,image/*,*/*",
}


def preferred_mime_from_bytes(body: bytes, header_content_type: str) -> str:
    """Use magic-number sniffing first; Shopify CDN often misreports WebP as image/jpeg."""
    if body:
        chunk = body[:128] if len(body) > 128 else body
        sniff = infer_image_format_from_bytes(chunk)
        if sniff:
            return sniff[1]
    h = (header_content_type or "").split(";")[0].strip().lower()
    return h or "application/octet-stream"


def image_cache_root(db_path: Path) -> Path:
    """Directory for cached binaries (next to the SQLite file)."""
    return db_path.resolve().parent / "shopify_image_cache"


def product_image_file_cache_index(conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """Map ``image_shopify_id`` → (normalized_url, local_relpath) for rows in ``product_image_file_cache``."""
    out: dict[str, tuple[str, str]] = {}
    for row in conn.execute(
        "SELECT image_shopify_id, normalized_url, local_relpath FROM product_image_file_cache"
    ):
        sid = (row[0] or "").strip()
        if sid:
            out[sid] = (row[1] or "", (row[2] or "").strip())
    return out


def catalog_gallery_image_cached_locally(
    cache_index: dict[str, tuple[str, str]],
    cache_root: Path,
    *,
    image_shopify_id: str,
    catalog_image_url: str,
) -> bool:
    """True if sync warmed a file for this product gallery media id and current catalog URL."""
    sid = (image_shopify_id or "").strip()
    if not sid:
        return False
    ent = cache_index.get(sid)
    if not ent:
        return False
    nu_stored, rel = ent
    if not rel or nu_stored != normalize_shopify_image_url(catalog_image_url):
        return False
    return (cache_root / rel).is_file()


def local_relpath_for(image_shopify_id: str) -> str:
    h = hashlib.sha256(image_shopify_id.encode("utf-8")).hexdigest()
    return f"{h[0:2]}/{h[2:4]}/{h}.bin"


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        logger.warning("Could not delete cache file %s", path)


def _parse_content_length(headers: Any) -> int | None:
    raw = headers.get("Content-Length")
    if not raw:
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


def _head_or_peek_headers(session: requests.Session, url: str) -> dict[str, str]:
    try:
        h = session.head(url, timeout=60, allow_redirects=True, headers=UA)
        if h.status_code == 200:
            return {k: v for k, v in h.headers.items()}
    except requests.RequestException:
        pass
    try:
        with session.get(url, timeout=60, stream=True, allow_redirects=True, headers=UA) as g:
            g.raise_for_status()
            return {k: v for k, v in g.headers.items()}
    except requests.RequestException:
        return {}


@dataclass
class _WorkerOutcome:
    image_id: str
    norm_url: str
    kind: Literal["downloaded", "skip_unchanged", "skip_304", "error"]
    error: str | None = None
    body: bytes | None = None
    mime: str = ""
    etag: str | None = None
    last_modified: str | None = None
    content_length: int | None = None
    sha256_hex: str | None = None
    local_relpath: str | None = None


CatalogImageFetchOutcome = _WorkerOutcome


def _worker_fetch(
    image_id: str,
    url: str,
    norm_url: str,
    crow: dict[str, Any] | None,
    cache_root: Path,
    *,
    force_refresh: bool = False,
) -> _WorkerOutcome:
    session = requests.Session()
    try:
        # Force refresh: always re-download bytes (no HEAD etag short-circuit, no conditional GET).
        if crow and not force_refresh:
            rel = crow["local_relpath"]
            path = cache_root / rel
            cr_etag = (crow.get("etag") or "").strip()
            cr_lm = (crow.get("last_modified") or "").strip()

            if cr_etag:
                peek = _head_or_peek_headers(session, url)
                net_etag = (peek.get("ETag") or "").strip()
                if net_etag and net_etag == cr_etag and path.is_file():
                    return _WorkerOutcome(image_id, norm_url, "skip_unchanged", local_relpath=rel)

            req_headers = dict(UA)
            if cr_etag:
                req_headers["If-None-Match"] = cr_etag
            elif cr_lm:
                req_headers["If-Modified-Since"] = cr_lm

            r = session.get(url, timeout=120, allow_redirects=True, headers=req_headers)
            if r.status_code == 304:
                if path.is_file():
                    return _WorkerOutcome(image_id, norm_url, "skip_304", local_relpath=rel)
            elif r.status_code == 200:
                data = r.content
                mime = preferred_mime_from_bytes(data, r.headers.get("Content-Type") or "")
                etag = (r.headers.get("ETag") or "").strip() or None
                lm = r.headers.get("Last-Modified")
                cl = _parse_content_length(r.headers) if r.headers else len(data)
                if cl is None:
                    cl = len(data)
                sh = hashlib.sha256(data).hexdigest()
                return _WorkerOutcome(
                    image_id,
                    norm_url,
                    "downloaded",
                    body=data,
                    mime=mime or "application/octet-stream",
                    etag=etag,
                    last_modified=lm,
                    content_length=cl,
                    sha256_hex=sh,
                )

        r2 = session.get(url, timeout=120, allow_redirects=True, headers=UA)
        r2.raise_for_status()
        data = r2.content
        mime = preferred_mime_from_bytes(data, r2.headers.get("Content-Type") or "")
        etag = (r2.headers.get("ETag") or "").strip() or None
        lm = r2.headers.get("Last-Modified")
        cl = _parse_content_length(r2.headers) if r2.headers else len(data)
        if cl is None:
            cl = len(data)
        sh = hashlib.sha256(data).hexdigest()
        return _WorkerOutcome(
            image_id,
            norm_url,
            "downloaded",
            body=data,
            mime=mime or "application/octet-stream",
            etag=etag,
            last_modified=lm,
            content_length=cl,
            sha256_hex=sh,
        )
    except Exception as exc:
        return _WorkerOutcome(image_id, norm_url, "error", error=str(exc) or "request failed")


def count_catalog_images_for_cache(db_path: Path) -> int:
    """Distinct Shopify-hosted catalog image URLs the cache warm will fetch (products, collections, pages, blogs)."""
    conn = open_db(db_path)
    try:
        return count_catalog_images_for_cache_db(conn)
    finally:
        conn.close()


def warm_product_image_cache(
    db_path: Path,
    *,
    max_workers: int = 6,
    progress_callback: Callable[[int, int], None] | None = None,
    on_fetch_outcome: Callable[[CatalogImageFetchOutcome], None] | None = None,
    queue_scope: str | None = None,
    force_refresh: bool = False,
) -> dict[str, int]:
    """After catalog sync: prune stale rows, then fetch/cache each distinct catalog image URL.

    Covers product gallery rows, collection/page/article images and inline HTML, and theme template URLs.

    When ``force_refresh`` is true, every image is re-fetched with an unconditional GET (no ETag /
    304 short paths). When false, unchanged remote files may be skipped via ``skip_unchanged`` /
    ``skip_304``.

    ``progress_callback`` is invoked as ``(done, total)`` while network fetches complete
    (``done`` from 0 through ``total``). Also called once with ``(0, total)`` before fetches.

    ``on_fetch_outcome``, when set, is called on the calling thread once per completed worker
    fetch (including skips and errors), immediately after ``fut.result()`` — display hooks only.

    When ``queue_scope`` is set (e.g. ``\"shopify\"``), the dashboard sync queue for that scope is
    seeded with pending ``catalog_image`` rows and drained as each fetch completes (success rows
    are removed; failures remain as errors).

    Returns counts: downloaded, skipped, errors, pruned.
    """
    root = image_cache_root(db_path)
    root.mkdir(parents=True, exist_ok=True)
    conn = open_db(db_path)
    stats = {"downloaded": 0, "skipped": 0, "errors": 0, "pruned": 0}
    try:
        registry = build_catalog_image_registry_from_db(conn)
        expected = registry.expected_cache_ids()
        if expected:
            placeholders = ",".join("?" * len(expected))
            stale = conn.execute(
                f"""
                SELECT image_shopify_id, local_relpath FROM product_image_file_cache
                WHERE image_shopify_id NOT IN ({placeholders})
                """,
                tuple(expected),
            ).fetchall()
        else:
            stale = conn.execute(
                "SELECT image_shopify_id, local_relpath FROM product_image_file_cache"
            ).fetchall()
        for sid, rel in stale:
            _safe_unlink(root / rel)
            conn.execute("DELETE FROM product_image_file_cache WHERE image_shopify_id = ?", (sid,))
            stats["pruned"] += 1
        conn.commit()

        work: list[tuple[str, str, str, dict[str, Any] | None, str]] = []
        for cache_id, url, norm in registry.work_items():
            crow = conn.execute(
                "SELECT * FROM product_image_file_cache WHERE image_shopify_id = ?",
                (cache_id,),
            ).fetchone()
            crow_d: dict[str, Any] | None = dict(crow) if crow else None
            if crow_d and crow_d.get("normalized_url") != norm:
                _safe_unlink(root / crow_d["local_relpath"])
                conn.execute("DELETE FROM product_image_file_cache WHERE image_shopify_id = ?", (cache_id,))
                crow_d = None
            work.append((cache_id, url.strip(), norm, crow_d, str(root)))
        conn.commit()

        total_fetch = len(work)
        if queue_scope:
            from shopifyseo.dashboard_actions import _sync_queue as _sq

            _sq.sync_queue_reset(queue_scope)
            if work:
                _sq.sync_queue_seed(
                    queue_scope,
                    [("catalog_image", sid, norm) for sid, _, norm, _, _ in work],
                )
        if progress_callback:
            progress_callback(0, total_fetch)

        def run_one(
            item: tuple[str, str, str, dict[str, Any] | None, str],
        ) -> _WorkerOutcome:
            sid, url, norm, crow_d, root_s = item
            return _worker_fetch(sid, url, norm, crow_d, Path(root_s), force_refresh=force_refresh)

        outcomes: list[_WorkerOutcome] = []
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as ex:
            futs = [ex.submit(run_one, w) for w in work]
            for fut in as_completed(futs):
                oc = fut.result()
                outcomes.append(oc)
                if queue_scope:
                    rk = _sq.catalog_sync_row_key("catalog_image", oc.image_id, oc.norm_url)
                    if oc.kind == "error":
                        _sq.sync_queue_mark_done(queue_scope, rk, False, oc.error)
                    else:
                        _sq.sync_queue_mark_done(queue_scope, rk, True, pop_completed=True)
                if on_fetch_outcome is not None:
                    on_fetch_outcome(oc)
                if progress_callback:
                    progress_callback(len(outcomes), total_fetch)

        now = now_iso()
        for oc in outcomes:
            if oc.kind == "error":
                stats["errors"] += 1
                logger.warning("Image cache error %s: %s", oc.image_id, oc.error)
                continue
            if oc.kind in ("skip_unchanged", "skip_304"):
                rel = oc.local_relpath
                if not rel:
                    stats["errors"] += 1
                    continue
                p = root / rel
                if not p.is_file():
                    stats["errors"] += 1
                    continue
                try:
                    sniff = infer_image_format_from_bytes(p.read_bytes()[:128])
                except OSError:
                    sniff = None
                if sniff:
                    row_m = conn.execute(
                        "SELECT mime FROM product_image_file_cache WHERE image_shopify_id = ?",
                        (oc.image_id,),
                    ).fetchone()
                    old_m = ((row_m[0] if row_m else "") or "").split(";")[0].strip().lower()
                    if old_m != sniff[1]:
                        conn.execute(
                            "UPDATE product_image_file_cache SET mime = ?, updated_at = ? WHERE image_shopify_id = ?",
                            (sniff[1], now, oc.image_id),
                        )
                    else:
                        conn.execute(
                            "UPDATE product_image_file_cache SET updated_at = ? WHERE image_shopify_id = ?",
                            (now, oc.image_id),
                        )
                else:
                    conn.execute(
                        "UPDATE product_image_file_cache SET updated_at = ? WHERE image_shopify_id = ?",
                        (now, oc.image_id),
                    )
                stats["skipped"] += 1
                continue
            if oc.kind == "downloaded" and oc.body is not None and oc.sha256_hex:
                rel = local_relpath_for(oc.image_id)
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(path.suffix + ".tmp")
                tmp.write_bytes(oc.body)
                tmp.replace(path)
                conn.execute(
                    """
                    INSERT OR REPLACE INTO product_image_file_cache (
                      image_shopify_id, normalized_url, local_relpath, etag, last_modified,
                      content_length, sha256_hex, mime, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        oc.image_id,
                        oc.norm_url,
                        rel,
                        oc.etag,
                        oc.last_modified,
                        oc.content_length,
                        oc.sha256_hex,
                        oc.mime,
                        now,
                    ),
                )
                stats["downloaded"] += 1
        conn.commit()
    finally:
        conn.close()
    return stats


def invalidate_product_image_cache_entry(
    db_path: Path,
    conn: sqlite3.Connection,
    image_shopify_id: str,
) -> None:
    """Remove cache row and file for this gallery media id (e.g. after Shopify replace)."""
    row = conn.execute(
        "SELECT local_relpath FROM product_image_file_cache WHERE image_shopify_id = ?",
        (image_shopify_id,),
    ).fetchone()
    if row:
        _safe_unlink(image_cache_root(db_path) / row["local_relpath"])
    conn.execute("DELETE FROM product_image_file_cache WHERE image_shopify_id = ?", (image_shopify_id,))


def cache_single_product_image(
    db_path: Path,
    conn: sqlite3.Connection,
    image_shopify_id: str,
    url: str,
) -> bool:
    """Download one product image and write it into the local cache. Returns True on success."""
    if not image_shopify_id or not (url or "").strip():
        return False
    url = url.strip()
    norm = normalize_shopify_image_url(url)
    root = image_cache_root(db_path)
    root.mkdir(parents=True, exist_ok=True)
    try:
        r = requests.get(url, timeout=120, allow_redirects=True, headers=UA)
        r.raise_for_status()
    except Exception as exc:
        logger.warning("cache_single_product_image failed for %s: %s", image_shopify_id, exc)
        return False
    data = r.content
    mime = preferred_mime_from_bytes(data, r.headers.get("Content-Type") or "")
    etag = (r.headers.get("ETag") or "").strip() or None
    lm = r.headers.get("Last-Modified")
    return _write_cache_entry(db_path, conn, image_shopify_id, norm, data, mime, etag, lm)


def cache_product_image_bytes(
    db_path: Path,
    conn: sqlite3.Connection,
    image_shopify_id: str,
    url: str,
    data: bytes,
    mime: str,
) -> bool:
    """Write already-in-memory image bytes into the local cache (no HTTP download)."""
    if not image_shopify_id or not data:
        return False
    norm = normalize_shopify_image_url((url or "").strip())
    return _write_cache_entry(db_path, conn, image_shopify_id, norm, data, mime, None, None)


def _write_cache_entry(
    db_path: Path,
    conn: sqlite3.Connection,
    image_shopify_id: str,
    norm_url: str,
    data: bytes,
    mime: str,
    etag: str | None,
    last_modified: str | None,
) -> bool:
    root = image_cache_root(db_path)
    root.mkdir(parents=True, exist_ok=True)
    cl = len(data)
    sh = hashlib.sha256(data).hexdigest()
    rel = local_relpath_for(image_shopify_id)
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    conn.execute(
        """
        INSERT OR REPLACE INTO product_image_file_cache (
          image_shopify_id, normalized_url, local_relpath, etag, last_modified,
          content_length, sha256_hex, mime, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (image_shopify_id, norm_url, rel, etag, last_modified, cl, sh, mime, now_iso()),
    )
    conn.commit()
    return True


def read_cached_product_image(
    db_path: Path,
    conn: sqlite3.Connection,
    image_shopify_id: str,
    url: str,
) -> tuple[bytes, str] | None:
    """Return (bytes, mime) if we have a valid on-disk cache for this gallery image and URL."""
    norm = normalize_shopify_image_url(url.strip())
    row = conn.execute(
        "SELECT * FROM product_image_file_cache WHERE image_shopify_id = ?",
        (image_shopify_id,),
    ).fetchone()
    if not row:
        return None
    if row["normalized_url"] != norm:
        return None
    root = image_cache_root(db_path)
    path = root / row["local_relpath"]
    if not path.is_file():
        return None
    data = path.read_bytes()
    expected = row["sha256_hex"]
    if expected and hashlib.sha256(data).hexdigest() != expected:
        return None
    mime = (row["mime"] or "").strip() or "image/jpeg"
    return data, mime
