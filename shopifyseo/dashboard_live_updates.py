import json
from typing import Any

from .shopify_admin import graphql_request
from .shopify_catalog_sync import sync_article, sync_collection, sync_page, sync_product


def _seo_metafields(seo_title: str, seo_description: str) -> list[dict[str, Any]]:
    """Build Shopify resource-owned SEO metafield entries for title_tag / description_tag."""
    mf: list[dict[str, Any]] = []
    if seo_title.strip():
        mf.append({"namespace": "global", "key": "title_tag", "type": "single_line_text_field", "value": seo_title})
    if seo_description.strip():
        mf.append({"namespace": "global", "key": "description_tag", "type": "multi_line_text_field", "value": seo_description})
    return mf


def live_update_collection(db_path: str, collection_id: str, title: str, seo_title: str, seo_description: str, body_html: str) -> dict:
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
    input_data = {"id": collection_id}
    if title.strip():
        input_data["title"] = title
    input_data["seo"] = {
        "title": seo_title,
        "description": seo_description,
    }
    input_data["descriptionHtml"] = body_html
    data = graphql_request(mutation, {"input": input_data})
    result = data["data"]["collectionUpdate"]
    if result["userErrors"]:
        raise RuntimeError(json.dumps(result["userErrors"], ensure_ascii=True))
    sync_collection(db_path, collection_id)
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
