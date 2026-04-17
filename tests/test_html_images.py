from shopifyseo.html_images import extract_shopify_images_from_html, is_shopify_hosted_image_url


def test_is_shopify_hosted_image_url():
    assert is_shopify_hosted_image_url("https://cdn.shopify.com/s/files/1/2/3/x.jpg") is True
    assert is_shopify_hosted_image_url("https://example.com/x.jpg") is False
    assert is_shopify_hosted_image_url("//cdn.shopify.com/x.png") is True


def test_extract_shopify_images_from_html_order_and_alt():
    html = """
    <div><img src="https://cdn.shopify.com/a.jpg" alt="One" /></div>
    <img src="https://evil.com/skip.jpg" alt="no" />
    <img src="//cdn.shopify.com/b.webp" alt="" />
    """
    out = extract_shopify_images_from_html(html)
    assert len(out) == 2
    assert out[0] == ("https://cdn.shopify.com/a.jpg", "One")
    assert out[1][0] == "https://cdn.shopify.com/b.webp"
