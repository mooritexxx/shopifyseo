from shopifyseo import shopify_admin


def test_clear_collection_featured_image_sends_null_image(monkeypatch) -> None:
    captured = {}

    def fake_graphql_request(mutation, variables):
        captured["mutation"] = mutation
        captured["variables"] = variables
        return {
            "data": {
                "collectionUpdate": {
                    "collection": {
                        "id": "gid://shopify/Collection/123",
                        "handle": "replacement-pods",
                        "image": None,
                    },
                    "userErrors": [],
                }
            }
        }

    monkeypatch.setattr(shopify_admin, "graphql_request", fake_graphql_request)

    result = shopify_admin.clear_collection_featured_image("gid://shopify/Collection/123")

    assert "collectionUpdate" in captured["mutation"]
    assert captured["variables"] == {
        "input": {
            "id": "gid://shopify/Collection/123",
            "image": None,
        }
    }
    assert result["collection"]["image"] is None
