import json
from typing import Any

from .shopify_admin import graphql_request
from .shopify_catalog_sync import sync_article, sync_collection, sync_page, sync_product


class ShopifyPartialCollectionUpdateError(RuntimeError):
    """SEO metafields saved on the collection, but ``collectionUpdate`` (title/body) was rejected.

    Raised for smart/automated collections whose existing rule set fails Shopify's
    re-validation during ``collectionUpdate``. The SEO title/description still persist
    because they are written via ``metafieldsSet`` first, which does not touch the
    collection's rule set.
    """


def _seo_metafields(seo_title: str, seo_description: str) -> list[dict[str, Any]]:
    """Build Shopify resource-owned SEO metafield entries for title_tag / description_tag."""
    mf: list[dict[str, Any]] = []
    if seo_title.strip():
        mf.append({"namespace": "global", "key": "title_tag", "type": "single_line_text_field", "value": seo_title})
    if seo_description.strip():
        mf.append({"namespace": "global", "key": "description_tag", "type": "multi_line_text_field", "value": seo_description})
    return mf


def _set_seo_metafields(owner_id: str, seo_title: str, seo_description: str) -> None:
    """Write resource-owned SEO metafields (global.title_tag / description_tag) via ``metafieldsSet``.

    Works for any owner type (Collection, Product, …) and does NOT invoke the owner's
    ``*Update`` mutation — so it is unaffected by smart-collection rule re-validation.
    No-op when both SEO fields are empty.
    """
    metafields = _seo_metafields(seo_title, seo_description)
    if not metafields:
        return
    mutation = """
    mutation SetSeoMetafields($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) {
        metafields { id namespace key }
        userErrors { field message }
      }
    }
    """
    payload = [
        {
            "ownerId": owner_id,
            "namespace": mf["namespace"],
            "key": mf["key"],
            "type": mf["type"],
            "value": mf["value"],
        }
        for mf in metafields
    ]
    data = graphql_request(mutation, {"metafields": payload})
    result = data["data"]["metafieldsSet"]
    if result["userErrors"]:
        raise RuntimeError(json.dumps(result["userErrors"], ensure_ascii=True))


def live_update_collection(db_path: str, collection_id: str, title: str, seo_title: str, seo_description: str, body_html: str) -> dict:
    """Push collection SEO + body to Shopify.

    SEO title/description go through ``metafieldsSet`` first so they persist even when
    the collection is a smart collection with a stale rule set. The title + body then
    go through ``collectionUpdate``; if Shopify rejects that step (rule re-validation),
    a :class:`ShopifyPartialCollectionUpdateError` is raised so the caller can report a
    partial save instead of losing the SEO write.
    """
    # 1. SEO metafields — independent of the collection's rule set.
    _set_seo_metafields(collection_id, seo_title, seo_description)

    # 2. title + descriptionHtml via collectionUpdate. For smart collections Shopify
    #    re-validates the rule set here; a stale rule yields a userError.
    mutation = """
    mutation CollectionUpdate($input: CollectionInput!) {
      collectionUpdate(input: $input) {
        collection {
          id
          handle
          title
          descriptionHtml
        }
        userErrors {
          field
          message
        }
      }
    }
    """
    input_data = {"id": collection_id, "descriptionHtml": body_html}
    if title.strip():
        input_data["title"] = title
    data = graphql_request(mutation, {"input": input_data})
    result = data["data"]["collectionUpdate"]
    body_errors = result.get("userErrors") or []
    # Re-sync regardless so the local cache reflects whatever did land in Shopify
    # (the SEO metafields always, plus title/body when collectionUpdate succeeded).
    sync_collection(db_path, collection_id)
    if body_errors:
        raise ShopifyPartialCollectionUpdateError(json.dumps(body_errors, ensure_ascii=True))
    return result


def live_update_product(db_path: str, product_id: str, title: str, seo_title: str, seo_description: str, body_html: str, tags: str) -> dict:
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
    input_data = {
        "id": product_id,
        "seo": {
            "title": seo_title,
            "description": seo_description,
        },
        "descriptionHtml": body_html,
        "tags": [tag.strip() for tag in tags.split(",") if tag.strip()],
    }
    if title.strip():
        input_data["title"] = title
    data = graphql_request(mutation, {"input": input_data})
    result = data["data"]["productUpdate"]
    if result["userErrors"]:
        raise RuntimeError(json.dumps(result["userErrors"], ensure_ascii=True))
    sync_product(db_path, product_id)
    return result


def live_update_page(db_path: str, page_id: str, title: str, seo_title: str, seo_description: str, body_html: str) -> dict:
    # Single pageUpdate with PageUpdateInput.metafields (Shopify-recommended for resource-owned SEO metafields).
    # Avoids relying on a separate metafieldsSet in the same request, which can be flaky for page SEO read-back.
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
    page: dict = {"body": body_html}
    if title.strip():
        page["title"] = title
    metafields = _seo_metafields(seo_title, seo_description)
    if metafields:
        page["metafields"] = metafields
    data = graphql_request(mutation, {"id": page_id, "page": page})
    result = data["data"]["pageUpdate"]
    if result["userErrors"]:
        raise RuntimeError(json.dumps(result["userErrors"], ensure_ascii=True))
    sync_page(db_path, page_id)
    return result


def publish_article(db_path: str, article_id: str, *, is_published: bool) -> dict:
    """Toggle Shopify article visibility (published / hidden draft)."""
    mutation = """
    mutation UpdateArticlePublish($id: ID!, $article: ArticleUpdateInput!) {
      articleUpdate(id: $id, article: $article) {
        article { id handle title isPublished publishedAt }
        userErrors { field message }
      }
    }
    """
    data = graphql_request(
        mutation,
        {"id": article_id, "article": {"isPublished": is_published}},
    )
    result = data["data"]["articleUpdate"]
    if result["userErrors"]:
        raise RuntimeError(json.dumps(result["userErrors"], ensure_ascii=True))
    sync_article(db_path, article_id)
    return result


def live_update_article(
    db_path: str,
    article_id: str,
    title: str,
    seo_title: str,
    seo_description: str,
    body_html: str,
) -> dict:
    mutation = """
    mutation UpdateArticle($id: ID!, $article: ArticleUpdateInput!) {
      articleUpdate(id: $id, article: $article) {
        article {
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
    article: dict = {"body": body_html}
    if title.strip():
        article["title"] = title
    metafields = _seo_metafields(seo_title, seo_description)
    if metafields:
        article["metafields"] = metafields
    data = graphql_request(mutation, {"id": article_id, "article": article})
    result = data["data"]["articleUpdate"]
    if result["userErrors"]:
        raise RuntimeError(json.dumps(result["userErrors"], ensure_ascii=True))
    sync_article(db_path, article_id)
    return result
