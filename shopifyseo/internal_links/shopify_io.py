"""Narrow live reads and body-only writes. No catalog sync or SEO side effects."""
from ..shopify_admin import graphql_request

_TYPES = {"product": ("Product", "descriptionHtml"), "collection": ("Collection", "descriptionHtml"),
          "blog_article": ("Article", "body")}


def fetch_body(source_type: str, row) -> str:
    typename, field = _TYPES[source_type]
    query = f"query LinkBody($id: ID!) {{ node(id: $id) {{ id __typename ... on {typename} {{ {field} }} }} }}"
    data = graphql_request(query, {"id": row["shopify_id"]})
    node = (data.get("data") or {}).get("node")
    if data.get("errors") or not node or node.get("__typename") != typename or not isinstance(node.get(field), str):
        raise RuntimeError("Could not read the current Shopify body. Nothing was written.")
    return node[field]


def push_body(source_type: str, row, new_body: str) -> str:
    typename, field = _TYPES[source_type]
    resource = typename.lower()
    if source_type == "blog_article":
        declaration = "$id: ID!, $article: ArticleUpdateInput!"
        arguments = "id: $id, article: $article"
        variables = {"id": row["shopify_id"], "article": {"body": new_body}}
    else:
        declaration = f"$input: {typename}Input!"
        arguments = "input: $input"
        variables = {"input": {"id": row["shopify_id"], field: new_body}}
    query = f"""mutation LinkBodyUpdate({declaration}) {{
      {resource}Update({arguments}) {{ {resource} {{ id {field} }} userErrors {{ field message }} }}
    }}"""
    data = graphql_request(query, variables)
    result = (data.get("data") or {}).get(resource + "Update") or {}
    if data.get("errors") or result.get("userErrors") or not result.get(resource):
        raise RuntimeError("Shopify did not confirm the body update. Reconciliation is required before retrying.")
    return result[resource][field]
