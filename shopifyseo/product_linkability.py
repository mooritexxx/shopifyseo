"""Shared product linkability rules.

A product is **linkable** when all of the following are true:

1. **Active status.** The ``status`` column is ``'ACTIVE'``, or is NULL/empty
   (legacy rows default to active for backwards compatibility).
2. **Has a handle.** The ``handle`` column is not NULL and not blank after
   trimming whitespace.
3. **Has an Online Store URL.** If the ``online_store_url`` column exists in
   the schema, a linkable product must have a non-empty value. A NULL value
   (as opposed to empty string) is tolerated because older syncs may not have
   populated the column, and such rows should remain linkable until the next
   catalog sync fills in the value. An explicit empty string means the product
   is not published to the Online Store sales channel and is therefore not
   linkable.

Stock / inventory is **never** checked. Out-of-stock products are fully valid
link targets; do not filter, order, swap, or reject based on inventory.

The functions in this module are the single source of truth for linkability.
Any query, filter, or validation that needs to decide whether a product is a
valid link target should use these helpers rather than duplicating the logic.
"""
from __future__ import annotations

import re
import sqlite3
from urllib.parse import urlparse

__all__ = [
    "linkable_product_sql",
    "linkable_product_handles",
    "is_product_linkable",
    "product_handle_from_href",
]


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    """Return True if *column* exists in *table*."""
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    names = {r[1] if isinstance(r, (list, tuple)) else r["name"] for r in rows}
    return column in names


def linkable_product_sql(conn: sqlite3.Connection, alias: str = "") -> str:
    """Return a SQL boolean expression for linkable products.

    The returned string has **no leading AND** and can be used directly in a
    WHERE clause or combined with other conditions using ``AND (...)``.

    Parameters
    ----------
    conn : sqlite3.Connection
        Used to check schema (e.g. whether ``online_store_url`` exists).
    alias : str, optional
        Table alias prefix (e.g. ``"p."``). Include the trailing dot if needed.

    Returns
    -------
    str
        A SQL expression that evaluates to true for linkable products.
    """
    alias = alias.strip()
    if alias and not alias.endswith("."):
        alias = alias + "."

    clauses = [
        f"({alias}handle IS NOT NULL AND TRIM({alias}handle) != '')",
        f"({alias}status IS NULL OR {alias}status = '' OR UPPER({alias}status) = 'ACTIVE')",
    ]

    if _column_exists(conn, "products", "online_store_url"):
        clauses.append(
            f"({alias}online_store_url IS NULL OR TRIM({alias}online_store_url) != '')"
        )

    return " AND ".join(clauses)


def linkable_product_handles(
    conn: sqlite3.Connection,
    handles: list[str] | set[str] | None = None,
) -> set[str]:
    """Return the set of linkable product handles.

    Parameters
    ----------
    conn : sqlite3.Connection
        Database connection.
    handles : list or set of str, optional
        If provided, only check these handles (chunked by 500 to avoid SQL
        parameter limits). If None, return all linkable handles.

    Returns
    -------
    set[str]
        Handles of linkable products.
    """
    expr = linkable_product_sql(conn, alias="")
    base_query = f"SELECT handle FROM products WHERE {expr}"

    if handles is None:
        rows = conn.execute(base_query).fetchall()
        return {r[0] if isinstance(r, (list, tuple)) else r["handle"] for r in rows}

    handle_list = list(handles)
    if not handle_list:
        return set()

    result: set[str] = set()
    chunk_size = 500
    for i in range(0, len(handle_list), chunk_size):
        chunk = handle_list[i : i + chunk_size]
        placeholders = ",".join("?" for _ in chunk)
        query = f"{base_query} AND handle IN ({placeholders})"
        rows = conn.execute(query, chunk).fetchall()
        for r in rows:
            h = r[0] if isinstance(r, (list, tuple)) else r["handle"]
            result.add(h)
    return result


def is_product_linkable(conn: sqlite3.Connection, handle: str) -> bool:
    """Return True if the product with *handle* is linkable.

    Returns False if the product does not exist or is not linkable.
    """
    handle = (handle or "").strip()
    if not handle:
        return False
    expr = linkable_product_sql(conn, alias="")
    query = f"SELECT 1 FROM products WHERE handle = ? AND {expr}"
    row = conn.execute(query, (handle,)).fetchone()
    return row is not None


_PRODUCT_PATH_RE = re.compile(
    r"^(?:/collections/[^/]+)?/products/([^/?#]+)",
    re.IGNORECASE,
)


def product_handle_from_href(
    href: str,
    store_hosts: tuple[str, ...] | list[str] | set[str] | frozenset[str] = (),
) -> str | None:
    """Extract a product handle from a storefront link.

    Recognises:
    - ``/products/<handle>``
    - ``/collections/<coll>/products/<handle>``
    - Absolute ``http(s)://`` URLs whose host is in *store_hosts*.

    Query strings and fragments are stripped before matching. Returns None for:
    - ``mailto:``, ``tel:``, ``javascript:`` links
    - Collection URLs without a ``/products/`` segment
    - Absolute URLs whose host is not in *store_hosts*
    - Any other format that doesn't match the product path pattern

    Parameters
    ----------
    href : str
        The href attribute value from an anchor tag.
    store_hosts : tuple/list/set of str
        Hosts (without scheme) that belong to the store. Case-insensitive.

    Returns
    -------
    str or None
        The product handle, or None if the href is not a valid store product link.
    """
    href = (href or "").strip()
    if not href:
        return None

    lower = href.lower()
    if lower.startswith(("mailto:", "tel:", "javascript:", "#")):
        return None

    store_hosts_lower = frozenset(h.lower() for h in store_hosts)

    path: str
    if href.startswith("/"):
        path = href
    elif lower.startswith("http"):
        parsed = urlparse(href)
        host = (parsed.netloc or "").lower()
        if host and store_hosts_lower and host not in store_hosts_lower:
            return None
        path = parsed.path or ""
    else:
        return None

    m = _PRODUCT_PATH_RE.match(path)
    if not m:
        return None

    return m.group(1)
