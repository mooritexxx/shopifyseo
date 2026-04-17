#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from typing import Any

import requests

from .dashboard_http import HttpRequestError, request_json


DEFAULT_API_VERSION = "2026-01"


def env(name: str, required: bool = True, default: str = "") -> str:
    value = os.getenv(name, default).strip()
    if required and not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def normalize_shop_domain(shop: str) -> str:
    shop = shop.strip()
    shop = shop.replace("https://", "").replace("http://", "").strip("/")
    if shop.endswith(".myshopify.com"):
        return shop
    if "." in shop:
        return shop
    return f"{shop}.myshopify.com"


def token_request() -> str:
    shop = normalize_shop_domain(env("SHOPIFY_SHOP"))
    client_id = os.getenv("SHOPIFY_CLIENT_ID", "").strip()
    client_secret = os.getenv("SHOPIFY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise SystemExit(
            "Missing Shopify auth. Set both SHOPIFY_CLIENT_ID and SHOPIFY_CLIENT_SECRET."
        )

    url = f"https://{shop}/admin/oauth/access_token"
    try:
        payload = request_json(
            url,
            method="POST",
            form={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
    except HttpRequestError as exc:
        if exc.status:
            raise SystemExit(f"Token request HTTP {exc.status}: {exc.body}") from exc
        raise SystemExit(f"Token request connection error: {exc}") from exc

    token = payload.get("access_token", "").strip()
    if not token:
        raise SystemExit(f"Token request failed: {json.dumps(payload, indent=2)}")
    return token


def graphql_post(query: str, variables: dict | None = None) -> dict:
    """POST Admin GraphQL and return the full JSON body. Raises only on HTTP/connection errors."""
    shop = normalize_shop_domain(env("SHOPIFY_SHOP"))
    token = token_request()
    version = env("SHOPIFY_API_VERSION", required=False, default=DEFAULT_API_VERSION)

    url = f"https://{shop}/admin/api/{version}/graphql.json"
    try:
        return request_json(
            url,
            method="POST",
            headers={"X-Shopify-Access-Token": token},
            payload={"query": query, "variables": variables or {}},
        )
    except HttpRequestError as exc:
        if exc.status:
            raise SystemExit(f"Shopify API HTTP {exc.status}: {exc.body}") from exc
        raise SystemExit(f"Shopify API connection error: {exc}") from exc


def graphql_request(query: str, variables: dict | None = None) -> dict:
    data = graphql_post(query, variables)
    if data.get("errors"):
        raise SystemExit(f"GraphQL errors: {json.dumps(data['errors'], indent=2)}")
    return data


def probe_shopify_admin_with_credentials(
    shop: str,
    client_id: str,
    client_secret: str,
    api_version: str = "",
) -> dict[str, Any]:
    """Client-credentials token + minimal GraphQL ``shop { name }``. Does not read process env."""
    shop_domain = normalize_shop_domain(shop)
    cid = (client_id or "").strip()
    csec = (client_secret or "").strip()
    if not shop_domain or not cid or not csec:
        raise ValueError("Shopify shop, Client ID, and Client Secret are required.")
    ver = (api_version or "").strip() or DEFAULT_API_VERSION
    token_url = f"https://{shop_domain}/admin/oauth/access_token"
    try:
        payload = request_json(
            token_url,
            method="POST",
            form={
                "grant_type": "client_credentials",
                "client_id": cid,
                "client_secret": csec,
            },
            timeout=30,
        )
    except HttpRequestError as exc:
        detail = f"Shopify OAuth token request failed: {exc}"
        if exc.status:
            detail = f"{detail} (HTTP {exc.status})"
        raise RuntimeError(detail) from exc
    token = (payload.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Shopify returned no access token.")
    gql = "query { shop { name } }"
    graphql_url = f"https://{shop_domain}/admin/api/{ver}/graphql.json"
    try:
        raw = request_json(
            graphql_url,
            method="POST",
            headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
            payload={"query": gql},
            timeout=30,
        )
    except HttpRequestError as exc:
        raise RuntimeError(f"Shopify GraphQL failed: {exc}") from exc
    if raw.get("errors"):
        raise RuntimeError(f"Shopify GraphQL errors: {raw.get('errors')}")
    shop_name = ""
    data = raw.get("data") or {}
    shop_node = data.get("shop") or {}
    if isinstance(shop_node, dict):
        shop_name = (shop_node.get("name") or "").strip()
    return {"shop_name": shop_name, "shop_domain": shop_domain}


def _wait_media_image_cdn_url(
    media_image_id: str,
    *,
    timeout_s: float = 90.0,
    interval_s: float = 1.5,
) -> str:
    """Poll until Shopify finishes processing the file and ``image.url`` is available.

    ``articleCreate`` / ``articleUpdate`` often fail to attach images if given a URL before the file is READY.
    """
    mid = (media_image_id or "").strip()
    if not mid:
        raise RuntimeError("Missing MediaImage id for readiness poll")
    query = """
    query BlogHeroMediaReady($id: ID!) {
      node(id: $id) {
        ... on MediaImage {
          fileStatus
          status
          image { url }
          fileErrors { code message }
        }
      }
    }
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        raw = graphql_post(query, {"id": mid})
        if raw.get("errors"):
            raise RuntimeError(f"Shopify media poll: {raw.get('errors')}")
        node = (raw.get("data") or {}).get("node") or {}
        if not node:
            time.sleep(interval_s)
            continue
        errs = node.get("fileErrors") or []
        if errs:
            msg = "; ".join(str(e.get("message") or e) for e in errs)
            raise RuntimeError(f"Shopify file errors: {msg}")
        img = node.get("image") or {}
        url = (img.get("url") or "").strip()
        if url.startswith("https://"):
            return url
        fst = str(node.get("fileStatus") or "").upper()
        if fst == "FAILED":
            raise RuntimeError("Shopify file processing failed (fileStatus=FAILED)")
        time.sleep(interval_s)
    raise RuntimeError(
        f"Timed out after {timeout_s:.0f}s waiting for Shopify CDN URL (MediaImage {mid})."
    )


def stage_image_bytes_post_resource_url(data: bytes, filename: str, mime_type: str) -> str:
    """``stagedUploadsCreate`` + multipart POST; returns ``resourceUrl`` for the next mutation.

    Use this with ``productCreateMedia`` (``originalSource`` = resource URL) **without** ``fileCreate`` in between.
    Otherwise Shopify ends up with two Files / media assets for the same bytes (orphan + product-linked).
    """
    staged_mutation = """
    mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
      stagedUploadsCreate(input: $input) {
        stagedTargets {
          url
          resourceUrl
          parameters { name value }
        }
        userErrors { field message }
      }
    }
    """
    raw = graphql_post(
        staged_mutation,
        {
            "input": [
                {
                    "filename": filename,
                    "mimeType": mime_type,
                    "resource": "IMAGE",
                    "httpMethod": "POST",
                }
            ]
        },
    )
    if raw.get("errors"):
        raise RuntimeError(f"Shopify stagedUploadsCreate: {raw.get('errors')}")
    suc = (raw.get("data") or {}).get("stagedUploadsCreate") or {}
    errs = suc.get("userErrors") or []
    if errs:
        raise RuntimeError("; ".join(str(e.get("message") or e) for e in errs))
    targets = suc.get("stagedTargets") or []
    if not targets:
        raise RuntimeError("stagedUploadsCreate returned no targets")
    t = targets[0]
    upload_url = (t.get("url") or "").strip()
    resource_url = (t.get("resourceUrl") or "").strip()
    if not upload_url or not resource_url:
        raise RuntimeError("Staged upload target missing url or resourceUrl")
    form_data = {p["name"]: p["value"] for p in (t.get("parameters") or []) if p.get("name")}
    resp = requests.post(
        upload_url,
        data=form_data,
        files={"file": (filename, data, mime_type)},
        timeout=120,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Staged upload HTTP {resp.status_code}: {resp.text[:500]}")
    return resource_url


def upload_image_bytes_and_get_url(
    data: bytes,
    filename: str,
    mime_type: str,
    *,
    alt: str | None = None,
) -> str:
    """Staged upload + fileCreate. Returns a stable HTTPS URL (Shopify CDN) for article.image and HTML <img>.

    Pass ``alt`` and a descriptive ``filename`` (e.g. slug-keywords.jpg) so the asset appears in Shopify Files
    with SEO-friendly metadata instead of an anonymous upload name.
    """
    resource_url = stage_image_bytes_post_resource_url(data, filename, mime_type)

    fc_mutation = """
    mutation fileCreate($files: [FileCreateInput!]!) {
      fileCreate(files: $files) {
        files {
          ... on MediaImage {
            id
            fileStatus
            status
            image { url }
          }
        }
        userErrors { field message }
      }
    }
    """
    file_create: dict = {
        "originalSource": resource_url,
        "contentType": "IMAGE",
        "filename": filename,
    }
    alt_clean = (alt or "").strip()
    if alt_clean:
        file_create["alt"] = alt_clean[:512]

    raw2 = graphql_post(
        fc_mutation,
        {"files": [file_create]},
    )
    if raw2.get("errors"):
        raise RuntimeError(f"Shopify fileCreate: {raw2.get('errors')}")
    fc = (raw2.get("data") or {}).get("fileCreate") or {}
    ferrs = fc.get("userErrors") or []
    if ferrs:
        raise RuntimeError("; ".join(str(e.get("message") or e) for e in ferrs))
    files_out = fc.get("files") or []
    if not files_out:
        raise RuntimeError("fileCreate returned no files")
    node = files_out[0] or {}
    file_id = (node.get("id") or "").strip()
    img = node.get("image") or {}
    url = (img.get("url") or "").strip()
    fst = str(node.get("fileStatus") or "").upper()
    # Article APIs re-fetch the image URL; if the file is still PROCESSING they attach nothing with no userError.
    if file_id:
        if fst == "READY" and url.startswith("https://"):
            return url
        return _wait_media_image_cdn_url(file_id, timeout_s=90.0, interval_s=1.5)
    if url.startswith("https://"):
        return url
    status = (node.get("fileStatus") or "").strip()
    raise RuntimeError(
        f"Shopify file has no image URL yet (status={status or 'unknown'}). "
        "If this persists, the file may still be processing — retry draft creation."
    )


def print_json(data: dict) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=True))


def query_products(args: argparse.Namespace) -> None:
    query = """
    query Products($first: Int!, $query: String) {
      products(first: $first, query: $query, sortKey: UPDATED_AT, reverse: true) {
        edges {
          node {
            id
            title
            handle
            vendor
            seo {
              title
              description
            }
          }
        }
      }
    }
    """
    data = graphql_request(query, {"first": args.first, "query": args.query})
    print_json(data["data"]["products"])


def query_collections(args: argparse.Namespace) -> None:
    query = """
    query Collections($first: Int!, $query: String) {
      collections(first: $first, query: $query, sortKey: UPDATED_AT, reverse: true) {
        edges {
          node {
            id
            title
            handle
            ruleSet {
              appliedDisjunctively
              rules {
                column
                condition
                relation
              }
            }
            seo {
              title
              description
            }
          }
        }
      }
    }
    """
    data = graphql_request(query, {"first": args.first, "query": args.query})
    print_json(data["data"]["collections"])


def query_pages(args: argparse.Namespace) -> None:
    query = """
    query Pages($first: Int!, $query: String) {
      pages(first: $first, query: $query, sortKey: UPDATED_AT, reverse: true) {
        edges {
          node {
            id
            title
            handle
            body
            titleTag: metafield(namespace: "global", key: "title_tag") {
              value
            }
            descriptionTag: metafield(namespace: "global", key: "description_tag") {
              value
            }
          }
        }
      }
    }
    """
    data = graphql_request(query, {"first": args.first, "query": args.query})
    print_json(data["data"]["pages"])


def update_product_seo(args: argparse.Namespace) -> None:
    mutation = """
    mutation ProductUpdate($input: ProductInput!) {
      productUpdate(input: $input) {
        product {
          id
          handle
          title
          seo {
            title
            description
          }
          descriptionHtml
          tags
        }
        userErrors {
          field
          message
        }
      }
    }
    """
    input_data = {"id": args.id}
    if args.seo_title or args.seo_description:
        input_data["seo"] = {}
        if args.seo_title:
            input_data["seo"]["title"] = args.seo_title
        if args.seo_description:
            input_data["seo"]["description"] = args.seo_description
    if args.body_html:
        input_data["descriptionHtml"] = args.body_html
    if args.tags:
        input_data["tags"] = [tag.strip() for tag in args.tags.split(",") if tag.strip()]
    data = graphql_request(mutation, {"input": input_data})
    print_json(data["data"]["productUpdate"])


def update_collection_seo(args: argparse.Namespace) -> None:
    mutation = """
    mutation CollectionUpdate($input: CollectionInput!) {
      collectionUpdate(input: $input) {
        collection {
          id
          handle
          title
          seo {
            title
            description
          }
          descriptionHtml
        }
        userErrors {
          field
          message
        }
      }
    }
    """
    input_data = {"id": args.id}
    if args.title:
        input_data["title"] = args.title
    if args.seo_title or args.seo_description:
        input_data["seo"] = {}
        if args.seo_title:
            input_data["seo"]["title"] = args.seo_title
        if args.seo_description:
            input_data["seo"]["description"] = args.seo_description
    if args.body_html:
        input_data["descriptionHtml"] = args.body_html
    data = graphql_request(mutation, {"input": input_data})
    print_json(data["data"]["collectionUpdate"])


def update_page_seo(args: argparse.Namespace) -> None:
    mutation = """
    mutation UpdatePage($id: ID!, $page: PageUpdateInput!) {
      pageUpdate(id: $id, page: $page) {
        page {
          id
          handle
          title
          body
        }
        userErrors {
          field
          message
        }
      }
    }
    """
    page: dict = {}
    if args.title:
        page["title"] = args.title
    if args.body_html:
        page["body"] = args.body_html

    metafields = []
    if args.seo_title:
        metafields.append(
            {
                "namespace": "global",
                "key": "title_tag",
                "type": "single_line_text_field",
                "value": args.seo_title,
            }
        )
    if args.seo_description:
        metafields.append(
            {
                "namespace": "global",
                "key": "description_tag",
                "type": "multi_line_text_field",
                "value": args.seo_description,
            }
        )
    if metafields:
        page["metafields"] = metafields

    data = graphql_request(mutation, {"id": args.id, "page": page})
    print_json(data["data"])


def query_blogs() -> list[dict]:
    """Return all blogs with their GIDs and titles."""
    query = """
    query Blogs {
      blogs(first: 50, sortKey: TITLE) {
        edges {
          node {
            id
            title
            handle
          }
        }
      }
    }
    """
    data = graphql_request(query)
    return [edge["node"] for edge in data["data"]["blogs"]["edges"]]


def create_article(
    *,
    blog_id: str,
    title: str,
    body_html: str,
    author_name: str = "",
    handle: str = "",
    summary: str = "",
    tags: list[str] | None = None,
    is_published: bool = False,
    seo_title: str = "",
    seo_description: str = "",
    image_url: str = "",
    image_alt: str = "",
) -> dict:
    """Create a blog article via the Admin GraphQL API.

    Returns the full mutation response dict (article + userErrors).
    Set is_published=False (the default) to create a draft.
    Pass seo_title and seo_description to set SEO meta fields on creation.
    When image_url is set, Shopify fetches the URL and attaches the featured image (must be HTTPS).
    """
    mutation = """
    mutation CreateArticle($article: ArticleCreateInput!) {
      articleCreate(article: $article) {
        article {
          id
          title
          handle
          body
          summary
          tags
          isPublished
          image {
            url
            altText
          }
          blog {
            id
            title
            handle
          }
          author {
            name
          }
        }
        userErrors {
          code
          field
          message
        }
      }
    }
    """
    article_input: dict = {
        "blogId": blog_id,
        "title": title,
        "body": body_html,
        "isPublished": is_published,
    }
    article_input["author"] = {"name": author_name or os.environ.get("STORE_NAME") or "ShopifySEO"}
    if handle:
        article_input["handle"] = handle
    if summary:
        article_input["summary"] = summary
    if tags:
        article_input["tags"] = tags
    # Article type no longer has `seo` on GraphQL; storefront SEO uses global metafields
    # (same pattern as pageUpdate in this module).
    seo_metafields: list[dict] = []
    if seo_title:
        seo_metafields.append(
            {
                "namespace": "global",
                "key": "title_tag",
                "type": "single_line_text_field",
                "value": seo_title,
            }
        )
    if seo_description:
        seo_metafields.append(
            {
                "namespace": "global",
                "key": "description_tag",
                "type": "multi_line_text_field",
                "value": seo_description,
            }
        )
    if seo_metafields:
        article_input["metafields"] = seo_metafields
    u = (image_url or "").strip()
    if u.startswith("https://"):
        article_input["image"] = {
            "url": u,
            "altText": (image_alt or title or "Blog image")[:512],
        }

    data = graphql_request(mutation, {"article": article_input})
    return data["data"]["articleCreate"]


def update_article_featured_image(article_id: str, image_url: str, image_alt: str) -> dict:
    """Attach featured image via articleUpdate (used when articleCreate returns no image)."""
    mutation = """
    mutation UpdateArticleFeaturedImage($id: ID!, $article: ArticleUpdateInput!) {
      articleUpdate(id: $id, article: $article) {
        article {
          id
          handle
          title
          image { url altText }
        }
        userErrors { field message }
      }
    }
    """
    u = (image_url or "").strip()
    if not u.startswith("https://"):
        raise ValueError("Featured image URL must be HTTPS")
    data = graphql_request(
        mutation,
        {
            "id": article_id,
            "article": {
                "image": {
                    "url": u,
                    "altText": (image_alt or "Article image")[:512],
                }
            },
        },
    )
    return data["data"]["articleUpdate"]


def update_article_body_html(article_id: str, body_html: str) -> dict:
    """Set article body HTML (e.g. after create if Shopify stripped inline hero markup)."""
    mutation = """
    mutation UpdateArticleBody($id: ID!, $article: ArticleUpdateInput!) {
      articleUpdate(id: $id, article: $article) {
        article { id body }
        userErrors { field message }
      }
    }
    """
    data = graphql_request(
        mutation,
        {"id": article_id, "article": {"body": body_html}},
    )
    return data["data"]["articleUpdate"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Shopify Admin API helper for products, collections, and pages."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    products = subparsers.add_parser("query-products", help="List products")
    products.add_argument("--first", type=int, default=20)
    products.add_argument("--query", default="")
    products.set_defaults(func=query_products)

    collections = subparsers.add_parser("query-collections", help="List collections")
    collections.add_argument("--first", type=int, default=20)
    collections.add_argument("--query", default="")
    collections.set_defaults(func=query_collections)

    pages = subparsers.add_parser("query-pages", help="List pages")
    pages.add_argument("--first", type=int, default=20)
    pages.add_argument("--query", default="")
    pages.set_defaults(func=query_pages)

    product_update = subparsers.add_parser("update-product-seo", help="Update product SEO/body/tags")
    product_update.add_argument("--id", required=True, help="GraphQL product ID")
    product_update.add_argument("--seo-title", default="")
    product_update.add_argument("--seo-description", default="")
    product_update.add_argument("--body-html", default="")
    product_update.add_argument("--tags", default="")
    product_update.set_defaults(func=update_product_seo)

    collection_update = subparsers.add_parser("update-collection-seo", help="Update collection SEO/body")
    collection_update.add_argument("--id", required=True, help="GraphQL collection ID")
    collection_update.add_argument("--title", default="")
    collection_update.add_argument("--seo-title", default="")
    collection_update.add_argument("--seo-description", default="")
    collection_update.add_argument("--body-html", default="")
    collection_update.set_defaults(func=update_collection_seo)

    page_update = subparsers.add_parser("update-page-seo", help="Update page SEO/body")
    page_update.add_argument("--id", required=True, help="GraphQL page ID")
    page_update.add_argument("--title", default="")
    page_update.add_argument("--seo-title", default="")
    page_update.add_argument("--seo-description", default="")
    page_update.add_argument("--body-html", default="")
    page_update.set_defaults(func=update_page_seo)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
