"""Real-path tests for product SEO title generation (#115 Round 2).

These tests exercise the actual generation paths with:
- Isolated testdb (SQLite temp file or Postgres schema)
- Stubbed AI client (returns controlled responses)
- Stubbed Shopify write endpoints (raise on call to detect unexpected writes)

Tests cover:
A1. 73-char and 67-char titles through full generation and regenerate-field
A2. Name-never-written with DB snapshot and Shopify write monkeypatching
A3. TVPA end-to-end via generate_field_recommendation
A4. No auto-apply for all generation paths
A5. API-level warnings through FastAPI TestClient
D. Page-title enforcement of 42-65 limits
"""
import json
import pytest
from unittest.mock import Mock, patch, MagicMock

from shopifyseo.dashboard_store import ensure_dashboard_schema
from shopifyseo.dashboard_ai_engine_parts import config
from shopifyseo.dashboard_ai_engine_parts import generation as gen_module
from shopifyseo.dashboard_ai_engine_parts import providers as providers_module


# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------

def _make_test_db(testdb):
    """Create a testdb connection with dashboard schema."""
    conn = testdb.connect()
    ensure_dashboard_schema(conn)
    return conn


def _insert_test_product(conn, handle, title, seo_title="", seo_description="", vendor="TestBrand"):
    """Insert a test product into the database."""
    shopify_id = f"gid://shopify/Product/{hash(handle) % 10000000}"
    conn.execute(
        """
        INSERT OR REPLACE INTO products (
            shopify_id, handle, title, vendor, status, seo_title, seo_description,
            description_html, tags_json, product_type, total_inventory,
            online_store_url, options_json, raw_json, synced_at
        ) VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, '', '[]', 'Vape', 100, ?, '[]', '{}', datetime('now'))
        """,
        (shopify_id, handle, title, vendor, seo_title, seo_description,
         f"https://example.com/products/{handle}"),
    )
    conn.commit()
    return shopify_id


def _insert_test_page(conn, handle, title, seo_title="", seo_description=""):
    """Insert a test page into the database."""
    shopify_id = f"gid://shopify/Page/{hash(handle) % 10000000}"
    conn.execute(
        """
        INSERT OR REPLACE INTO pages (
            shopify_id, handle, title, seo_title, seo_description, body
        ) VALUES (?, ?, ?, ?, ?, '')
        """,
        (shopify_id, handle, title, seo_title, seo_description),
    )
    conn.commit()
    return shopify_id


def _insert_test_collection(conn, handle, title, seo_title="", seo_description="", description_html=""):
    """Insert a test collection into the database."""
    shopify_id = f"gid://shopify/Collection/{hash(handle) % 10000000}"
    conn.execute(
        """
        INSERT OR REPLACE INTO collections (
            shopify_id, handle, title, seo_title, seo_description, description_html,
            raw_json, synced_at
        ) VALUES (?, ?, ?, ?, ?, ?, '{}', datetime('now'))
        """,
        (shopify_id, handle, title, seo_title, seo_description, description_html),
    )
    conn.commit()
    return shopify_id


def _insert_ai_settings(conn):
    """Insert required AI settings for generation."""
    settings = [
        ("generation_provider", "openai"),
        ("generation_model", "gpt-4"),
        ("review_provider", "openai"),
        ("review_model", "gpt-4"),
        ("sidekick_provider", "openai"),
        ("sidekick_model", "gpt-4"),
        ("prompt_version", "v3"),
        ("prompt_profile", "balanced"),
        ("ai_timeout", "60"),
    ]
    for key, value in settings:
        conn.execute(
            "INSERT OR REPLACE INTO service_settings (key, value) VALUES (?, ?)",
            (key, value),
        )
    conn.commit()


def _get_latest_recommendation(conn, object_type, handle):
    """Get the most recent recommendation for an object."""
    row = conn.execute(
        """
        SELECT * FROM seo_recommendations
        WHERE object_type = ? AND object_handle = ?
        ORDER BY created_at DESC LIMIT 1
        """,
        (object_type, handle),
    ).fetchone()
    if row:
        return dict(row)
    return None


@pytest.fixture
def test_db(testdb):
    """Create a temporary test database."""
    conn = _make_test_db(testdb)
    _insert_ai_settings(conn)
    yield conn, testdb
    conn.close()


@pytest.fixture
def mock_store_identity(monkeypatch):
    """Mock store identity to avoid external lookups."""
    monkeypatch.setattr(config, "_STORE_IDENTITY_CACHE", ("Vapely Canada", "vapely.ca"))


@pytest.fixture
def mock_ai_client(monkeypatch):
    """Mock AI client to return controlled responses without making real API calls."""
    call_log = []
    
    def extract_product_info_from_messages(messages):
        """Extract product name and flavour from the AI prompt messages."""
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                # Look for product title in the prompt
                import re
                # Try to find "title: ..." or similar patterns
                title_match = re.search(r'"?title"?\s*[:\-]\s*["\']?([^"\'\n,}]+)', content, re.IGNORECASE)
                if title_match:
                    title = title_match.group(1).strip()
                    # Extract flavour - typically after " - " in product name
                    if " - " in title:
                        flavour_part = title.split(" - ", 1)[1]
                        # Clean up common suffixes
                        for suffix in [" Disposable Vape", " Vape", " Disposable"]:
                            if flavour_part.endswith(suffix):
                                flavour_part = flavour_part[:-len(suffix)]
                        return title, flavour_part.strip()
        return "Test Product", "Ice"
    
    def fake_call_ai(settings, provider, model, messages, timeout, json_schema=None, stage=None):
        call_log.append({
            "provider": provider,
            "model": model,
            "stage": stage,
            "messages": messages,
        })
        
        # Extract product info from messages to generate context-aware responses
        product_title, flavour = extract_product_info_from_messages(messages)
        
        # Return appropriate responses based on field being generated
        if stage and "seo_description" in stage:
            # Include the flavour name, keep within 115-160 chars
            desc = f"Shop {flavour} vape at Vapely Canada. Fast shipping on premium disposables with {flavour} flavour."
            # Ensure length is within bounds (pad or trim as needed)
            if len(desc) < 115:
                desc = desc[:-1] + " Order today for great Canadian vape deals."
            return {"seo_description": desc[:160]}
        if stage and "body" in stage:
            # Body needs to be 750+ chars
            body = f"""<h2>Premium {flavour} Vape</h2>
<p>Experience the exceptional taste of {flavour} with this premium disposable vape. Crafted for Canadian vapers who demand quality and convenience, this device delivers smooth, satisfying hits every time.</p>
<h3>Why Choose This {flavour} Vape?</h3>
<p>This premium vaping device features advanced coil technology that ensures consistent flavour delivery from the first puff to the last. The {flavour} profile is carefully balanced to provide a refreshing experience without being overwhelming.</p>
<h3>Perfect for Canadian Vapers</h3>
<p>Designed with portability in mind, this disposable vape fits easily in your pocket or bag. Whether you're commuting through Toronto, exploring Vancouver's trails, or enjoying Montreal's nightlife, this device is your perfect companion.</p>
<h3>Quality You Can Trust</h3>
<p>Every device undergoes rigorous quality testing to ensure safety and performance. With fast Canada-wide shipping from Vapely Canada, you can enjoy your {flavour} vape within days of ordering.</p>"""
            return {"body": body}
        if stage and "tags" in stage:
            return {"tags": "vape, disposable, premium"}
        return {}
    
    # Mock the AI client - must patch in all modules where it's used
    monkeypatch.setattr(providers_module, "_call_ai", fake_call_ai)
    monkeypatch.setattr(gen_module, "_call_ai", fake_call_ai)
    # Skip credential checks
    monkeypatch.setattr(providers_module, "_require_provider_credentials", lambda s, p: None)
    monkeypatch.setattr(gen_module, "_require_provider_credentials", lambda s, p: None)
    
    return call_log


@pytest.fixture
def mock_shopify_writes(monkeypatch):
    """Mock all Shopify write endpoints to raise AssertionError on unexpected calls.
    
    Patches at the locations where functions are USED, not just where they're defined.
    Tests should assert call_count == 0 to prove no writes occurred.
    """
    call_counts = {"live_update_product": 0, "graphql_request": 0}
    
    def raise_on_live_update_product(*args, **kwargs):
        call_counts["live_update_product"] += 1
        raise AssertionError(f"Unexpected live_update_product called with args={args}, kwargs={kwargs}")
    
    def raise_on_graphql_request(*args, **kwargs):
        call_counts["graphql_request"] += 1
        raise AssertionError(f"Unexpected graphql_request called with args={args}, kwargs={kwargs}")
    
    # Patch where the functions are USED (imported into other modules)
    from backend.app.services import product_service
    from shopifyseo import dashboard_live_updates
    from shopifyseo import shopify_admin
    
    # live_update_product is imported into product_service and used in dashboard_live_updates
    monkeypatch.setattr(product_service, "live_update_product", raise_on_live_update_product)
    monkeypatch.setattr(dashboard_live_updates, "live_update_product", raise_on_live_update_product)
    
    # graphql_request is the lowest-level Shopify call
    monkeypatch.setattr(shopify_admin, "graphql_request", raise_on_graphql_request)
    monkeypatch.setattr(dashboard_live_updates, "graphql_request", raise_on_graphql_request)
    
    return call_counts


# ---------------------------------------------------------------------------
# A1: Real-path tests for 73-char and 67-char titles
# ---------------------------------------------------------------------------

class TestTruncationRealPaths:
    """Test that product SEO titles are NEVER truncated through real generation paths."""
    
    # 57-char Glubble product name -> 73-char title (exceeds 60-char SEO advisory)
    GLUBBLE_NAME = "ELFBAR GH20000 - Straw Watermelon Glubble Disposable Vape"
    # " | Vapely Canada" suffix is 16 chars
    GLUBBLE_EXPECTED_TITLE = "ELFBAR GH20000 - Straw Watermelon Glubble Disposable Vape | Vapely Canada"
    
    # 68-char product name -> 84-char title (well over 60-char advisory)
    LONG_NAME = "ELFBAR BC15000 Pro Max Ultra Edition - Tropical Rainbow Blast Vape"
    LONG_EXPECTED_TITLE = "ELFBAR BC15000 Pro Max Ultra Edition - Tropical Rainbow Blast Vape | Vapely Canada"
    
    def test_73char_through_full_generation(self, test_db, mock_store_identity, mock_ai_client, mock_shopify_writes):
        """73-char Glubble title through full generation is NOT truncated."""
        conn, db_path = test_db
        handle = "glubble-73-full-gen"
        _insert_test_product(conn, handle, self.GLUBBLE_NAME)
        
        # Pre-verify: title should be >60 chars (the SEO advisory limit)
        assert len(self.GLUBBLE_EXPECTED_TITLE) > 60
        
        from shopifyseo.dashboard_ai_engine_parts.generation import generate_recommendation
        result = generate_recommendation(conn, "product", handle)
        
        # Verify the seo_title is exactly the expected untruncated title
        assert result["seo_title"] == self.GLUBBLE_EXPECTED_TITLE
        assert len(result["seo_title"]) == len(self.GLUBBLE_EXPECTED_TITLE)
        
        # Verify it's stored in the recommendation
        rec = _get_latest_recommendation(conn, "product", handle)
        assert rec is not None
        details = json.loads(rec["details_json"])
        assert details["seo_title"] == self.GLUBBLE_EXPECTED_TITLE
    
    def test_73char_through_regenerate_field(self, test_db, mock_store_identity, mock_ai_client, mock_shopify_writes):
        """73-char Glubble title through regenerate-field is NOT truncated."""
        conn, db_path = test_db
        handle = "glubble-73-regen"
        _insert_test_product(conn, handle, self.GLUBBLE_NAME)
        
        from shopifyseo.dashboard_ai_engine_parts.generation import generate_field_recommendation
        result = generate_field_recommendation(
            conn, "product", handle, "seo_title", accepted_fields={}
        )
        
        # Verify the seo_title is exactly the expected untruncated title
        assert result["value"] == self.GLUBBLE_EXPECTED_TITLE
        assert len(result["value"]) == len(self.GLUBBLE_EXPECTED_TITLE)
    
    def test_longer_title_through_full_generation(self, test_db, mock_store_identity, mock_ai_client, mock_shopify_writes):
        """Long title (>80 chars) through full generation is NOT truncated."""
        conn, db_path = test_db
        handle = "long-title-full-gen"
        _insert_test_product(conn, handle, self.LONG_NAME)
        
        # Pre-verify: title should be >80 chars (well over advisory)
        assert len(self.LONG_EXPECTED_TITLE) > 80
        
        from shopifyseo.dashboard_ai_engine_parts.generation import generate_recommendation
        result = generate_recommendation(conn, "product", handle)
        
        # Verify the seo_title is exactly the expected untruncated title
        assert result["seo_title"] == self.LONG_EXPECTED_TITLE
        assert len(result["seo_title"]) == len(self.LONG_EXPECTED_TITLE)
    
    def test_longer_title_through_regenerate_field(self, test_db, mock_store_identity, mock_ai_client, mock_shopify_writes):
        """Long title (>80 chars) through regenerate-field is NOT truncated."""
        conn, db_path = test_db
        handle = "long-title-regen"
        _insert_test_product(conn, handle, self.LONG_NAME)
        
        from shopifyseo.dashboard_ai_engine_parts.generation import generate_field_recommendation
        result = generate_field_recommendation(
            conn, "product", handle, "seo_title", accepted_fields={}
        )
        
        # Verify the seo_title is exactly the expected untruncated title
        assert result["value"] == self.LONG_EXPECTED_TITLE
        assert len(result["value"]) == len(self.LONG_EXPECTED_TITLE)


# ---------------------------------------------------------------------------
# A2: Name-never-written tests with DB snapshot and Shopify write patching
# ---------------------------------------------------------------------------

class TestNameNeverWritten:
    """Test that product name/title is NEVER modified by generation."""
    
    PRODUCT_NAME = "ELFBAR BC5000 - Blue Razz Ice Disposable Vape"
    PRODUCT_HANDLE = "elfbar-bc5000-blue-razz"
    
    def test_regenerate_field_seo_title_preserves_product_name(
        self, test_db, mock_store_identity, mock_ai_client, mock_shopify_writes
    ):
        """Regenerate seo_title preserves product name in DB."""
        conn, db_path = test_db
        shopify_id = _insert_test_product(conn, self.PRODUCT_HANDLE, self.PRODUCT_NAME)
        
        # Snapshot before
        before = conn.execute(
            "SELECT title, handle, seo_title FROM products WHERE shopify_id = ?",
            (shopify_id,)
        ).fetchone()
        before_dict = dict(before)
        
        from shopifyseo.dashboard_ai_engine_parts.generation import generate_field_recommendation
        generate_field_recommendation(conn, "product", self.PRODUCT_HANDLE, "seo_title", {})
        
        # Snapshot after
        after = conn.execute(
            "SELECT title, handle, seo_title FROM products WHERE shopify_id = ?",
            (shopify_id,)
        ).fetchone()
        after_dict = dict(after)
        
        # title and handle must be byte-identical
        assert before_dict["title"] == after_dict["title"]
        assert before_dict["handle"] == after_dict["handle"]
        # seo_title in products table should NOT be updated by generation
        assert before_dict["seo_title"] == after_dict["seo_title"]
        # No Shopify writes should have occurred - assert call count is 0
        assert mock_shopify_writes["live_update_product"] == 0
        assert mock_shopify_writes["graphql_request"] == 0
    
    def test_regenerate_field_seo_description_preserves_product_name(
        self, test_db, mock_store_identity, mock_ai_client, mock_shopify_writes
    ):
        """Regenerate seo_description preserves product name and seo_title in DB."""
        conn, db_path = test_db
        shopify_id = _insert_test_product(conn, self.PRODUCT_HANDLE, self.PRODUCT_NAME)
        
        # Snapshot before - include seo_title for complete comparison
        before = conn.execute(
            "SELECT title, handle, seo_title FROM products WHERE shopify_id = ?",
            (shopify_id,)
        ).fetchone()
        
        from shopifyseo.dashboard_ai_engine_parts.generation import generate_field_recommendation
        generate_field_recommendation(conn, "product", self.PRODUCT_HANDLE, "seo_description", {})
        
        # Snapshot after
        after = conn.execute(
            "SELECT title, handle, seo_title FROM products WHERE shopify_id = ?",
            (shopify_id,)
        ).fetchone()
        
        # All fields must be byte-identical (title, handle, and seo_title)
        assert dict(before) == dict(after)
        # No Shopify writes should have occurred - assert call count is 0
        assert mock_shopify_writes["live_update_product"] == 0
        assert mock_shopify_writes["graphql_request"] == 0
    
    def test_regenerate_field_body_preserves_product_name(
        self, test_db, mock_store_identity, mock_ai_client, mock_shopify_writes
    ):
        """Regenerate body preserves product name and seo_title in DB."""
        conn, db_path = test_db
        shopify_id = _insert_test_product(conn, self.PRODUCT_HANDLE, self.PRODUCT_NAME)
        
        # Snapshot before - include seo_title for complete comparison
        before = conn.execute(
            "SELECT title, handle, seo_title FROM products WHERE shopify_id = ?",
            (shopify_id,)
        ).fetchone()
        
        from shopifyseo.dashboard_ai_engine_parts.generation import generate_field_recommendation
        generate_field_recommendation(conn, "product", self.PRODUCT_HANDLE, "body", {})
        
        # Snapshot after
        after = conn.execute(
            "SELECT title, handle, seo_title FROM products WHERE shopify_id = ?",
            (shopify_id,)
        ).fetchone()
        
        # All fields must be byte-identical (title, handle, and seo_title)
        assert dict(before) == dict(after)
        # No Shopify writes should have occurred - assert call count is 0
        assert mock_shopify_writes["live_update_product"] == 0
        assert mock_shopify_writes["graphql_request"] == 0
    
    def test_full_generation_preserves_product_name(
        self, test_db, mock_store_identity, mock_ai_client, mock_shopify_writes
    ):
        """Full generation preserves product name in DB."""
        conn, db_path = test_db
        shopify_id = _insert_test_product(conn, self.PRODUCT_HANDLE, self.PRODUCT_NAME)
        
        before = conn.execute(
            "SELECT title, handle, seo_title, seo_description FROM products WHERE shopify_id = ?",
            (shopify_id,)
        ).fetchone()
        before_dict = dict(before)
        
        from shopifyseo.dashboard_ai_engine_parts.generation import generate_recommendation
        generate_recommendation(conn, "product", self.PRODUCT_HANDLE)
        
        after = conn.execute(
            "SELECT title, handle, seo_title, seo_description FROM products WHERE shopify_id = ?",
            (shopify_id,)
        ).fetchone()
        after_dict = dict(after)
        
        # Product fields in products table must be unchanged
        assert before_dict["title"] == after_dict["title"]
        assert before_dict["handle"] == after_dict["handle"]
        assert before_dict["seo_title"] == after_dict["seo_title"]
        assert before_dict["seo_description"] == after_dict["seo_description"]
        # No Shopify writes - assert call count is 0
        assert mock_shopify_writes["live_update_product"] == 0
        assert mock_shopify_writes["graphql_request"] == 0


# ---------------------------------------------------------------------------
# A3: TVPA end-to-end via generate_field_recommendation
# ---------------------------------------------------------------------------

class TestTVPAEndToEnd:
    """Test TVPA compliance through real generation paths."""
    
    PRODUCT_NAME = "ELFBAR BC5000 - Blue Razz Ice Vape"
    PRODUCT_HANDLE = "elfbar-tvpa-test"
    
    @pytest.fixture
    def tvpa_mock_ai(self, monkeypatch):
        """Mock AI that returns TVPA-violating content on first call, clean on second."""
        call_count = {"body": 0, "seo_description": 0}
        
        def fake_call_ai(settings, provider, model, messages, timeout, json_schema=None, stage=None):
            if stage and "body" in stage:
                call_count["body"] += 1
                if call_count["body"] == 1:
                    # First call: TVPA violation (candy comparison) - must be 750+ chars
                    return {"body": "<h2>Tastes Just Like Candy!</h2><p>This vape tastes just like candy! Sweet and delicious treats from your childhood. Remember those sugary candies from the corner store? This vape brings back all those memories with every puff.</p><h3>Sweet as Sugar</h3><p>The Blue Razz Ice flavour is reminiscent of your favourite candy treats. Each inhale delivers that familiar sweetness that makes you think of desserts and confections. It's like having a candy shop in your pocket.</p><h3>Dessert-Like Experience</h3><p>Experience the joy of dessert without the calories. This vape provides a confectionery experience that satisfies your sweet tooth. Perfect for those who love candy and sweet treats.</p><h3>Canada's Favourite</h3><p>Available across Canada with fast shipping to all provinces and territories.</p>"}
                else:
                    # Second call: Clean - must be 750+ chars (adding buffer)
                    return {"body": "<h2>Premium Blue Razz Ice Vape</h2><p>This vape offers a premium Blue Razz Ice flavour experience. Crafted for discerning Canadian vapers who appreciate quality and authentic taste profiles. Perfect for daily use.</p><h3>Exceptional Blue Razz Ice Flavour</h3><p>The Blue Razz Ice profile delivers a crisp, refreshing experience with every puff. Carefully balanced to provide satisfaction without being overwhelming, this blend is perfect for all-day vaping. Canadian vapers love this flavour.</p><h3>Quality Construction</h3><p>Built with premium components and advanced coil technology, this device ensures consistent performance from the first puff to the last. Every device undergoes rigorous quality testing before shipping to customers across Canada.</p><h3>Fast Canada-Wide Shipping</h3><p>Get your Blue Razz Ice vape delivered fast across Canada. Vapely Canada offers reliable shipping to all provinces and territories with tracking included.</p>"}
            if stage and "seo_description" in stage:
                call_count["seo_description"] += 1
                return {"seo_description": "Shop Blue Razz Ice vape in Canada. Premium quality disposable vape with fast Canada-wide shipping at Vapely Canada."}
            return {}
        
        monkeypatch.setattr(providers_module, "_call_ai", fake_call_ai)
        monkeypatch.setattr(gen_module, "_call_ai", fake_call_ai)
        monkeypatch.setattr(providers_module, "_require_provider_credentials", lambda s, p: None)
        monkeypatch.setattr(gen_module, "_require_provider_credentials", lambda s, p: None)
        return call_count
    
    @pytest.fixture
    def tvpa_always_banned_ai(self, monkeypatch):
        """Mock AI that always returns TVPA-violating content."""
        def fake_call_ai(settings, provider, model, messages, timeout, json_schema=None, stage=None):
            if stage and "body" in stage:
                # TVPA violation with candy/dessert words - must be 750+ chars for body length validation
                return {"body": "<h2>Tastes Just Like Candy!</h2><p>This vape is like candy! It tastes like dessert and brings back memories of childhood sweets. Every puff delivers that familiar sugary sweetness that reminds you of confectionery treats.</p><h3>Sweet Confectionery Experience</h3><p>Experience the joy of candy and confections with every inhale. This vape provides a dessert-like experience that satisfies your sweet tooth completely. The flavour is reminiscent of your favourite candy treats from childhood.</p><h3>Like Your Favourite Treats</h3><p>Remember those sugary treats from the corner store? This vape captures that exact same sensation. Sweet, delicious, and reminiscent of childhood memories of candy shops and dessert counters.</p><h3>Order Today</h3><p>Available for fast shipping across all Canadian provinces and territories. Get your candy-flavoured vape experience today from Vapely Canada!</p>"}
            if stage and "seo_description" in stage:
                return {"seo_description": "Candy-flavoured vape that tastes like dessert sweets. Premium quality available at Vapely Canada with fast shipping."}
            return {}
        
        monkeypatch.setattr(providers_module, "_call_ai", fake_call_ai)
        monkeypatch.setattr(gen_module, "_call_ai", fake_call_ai)
        monkeypatch.setattr(providers_module, "_require_provider_credentials", lambda s, p: None)
        monkeypatch.setattr(gen_module, "_require_provider_credentials", lambda s, p: None)
    
    @pytest.fixture
    def tvpa_style_only_ai(self, monkeypatch):
        """Mock AI that returns only style-group TVPA issues (nostalgic)."""
        def fake_call_ai(settings, provider, model, messages, timeout, json_schema=None, stage=None):
            if stage and "body" in stage:
                # Nostalgic is style-group, not category - should be warning only
                # Must be 750+ chars for body length validation
                return {"body": "<h2>A Nostalgic Experience</h2><p>A nostalgic Blue Razz Ice flavour that brings back memories of simpler times. This premium vape captures that classic essence Canadian vapers love. Experience the nostalgic feeling with every puff.</p><h3>Nostalgic Memories</h3><p>Experience the nostalgic sensation of classic Blue Razz with every puff. The carefully crafted flavour profile evokes a nostalgic feeling that takes you back to memorable moments and simpler times in Canada.</p><h3>Premium Quality</h3><p>Built with premium components and advanced coil technology for consistent performance. Every device undergoes rigorous quality testing before shipping to ensure the best nostalgic experience.</p><h3>Fast Canadian Shipping</h3><p>Get your Blue Razz Ice vape delivered fast across Canada from Vapely Canada. Available in all provinces and territories with reliable tracking.</p>"}
            return {}
        
        monkeypatch.setattr(providers_module, "_call_ai", fake_call_ai)
        monkeypatch.setattr(gen_module, "_call_ai", fake_call_ai)
        monkeypatch.setattr(providers_module, "_require_provider_credentials", lambda s, p: None)
        monkeypatch.setattr(gen_module, "_require_provider_credentials", lambda s, p: None)
    
    def test_body_banned_then_clean_succeeds_after_retry(
        self, test_db, mock_store_identity, tvpa_mock_ai, mock_shopify_writes
    ):
        """Body with banned word on first attempt, clean on retry -> succeeds."""
        conn, db_path = test_db
        _insert_test_product(conn, self.PRODUCT_HANDLE, self.PRODUCT_NAME)
        
        from shopifyseo.dashboard_ai_engine_parts.generation import generate_field_recommendation
        result = generate_field_recommendation(
            conn, "product", self.PRODUCT_HANDLE, "body", {}
        )
        
        # Should succeed with clean body (after retry)
        assert "candy" not in result["value"].lower()
        assert "Blue Razz Ice" in result["value"]
        # AI should have been called twice (first banned, then retry)
        assert tvpa_mock_ai["body"] == 2
    
    def test_body_banned_both_attempts_rejected(
        self, test_db, mock_store_identity, tvpa_always_banned_ai, mock_shopify_writes
    ):
        """Body with banned word on both attempts -> rejected with error."""
        conn, db_path = test_db
        _insert_test_product(conn, self.PRODUCT_HANDLE, self.PRODUCT_NAME)
        
        from shopifyseo.dashboard_ai_engine_parts.generation import generate_field_recommendation
        
        with pytest.raises(RuntimeError) as exc_info:
            generate_field_recommendation(
                conn, "product", self.PRODUCT_HANDLE, "body", {}
            )
        
        # Error should mention TVPA
        assert "TVPA" in str(exc_info.value) or "flavour" in str(exc_info.value).lower()
    
    def test_style_issue_nostalgic_is_warning_only(
        self, test_db, mock_store_identity, tvpa_style_only_ai, mock_shopify_writes
    ):
        """'nostalgic' is a style issue -> warning only, not rejection."""
        conn, db_path = test_db
        _insert_test_product(conn, self.PRODUCT_HANDLE, self.PRODUCT_NAME)
        
        from shopifyseo.dashboard_ai_engine_parts.generation import generate_field_recommendation
        result = generate_field_recommendation(
            conn, "product", self.PRODUCT_HANDLE, "body", {}
        )
        
        # Should succeed (nostalgic is style, not category) - no exception raised
        assert "nostalgic" in result["value"].lower()
        # TVPA style warnings should be present (not empty)
        assert "tvpa_flavour_warnings" in result
        assert len(result["tvpa_flavour_warnings"]) >= 1, "Expected style warning for 'nostalgic'"
        # At least one warning should mention nostalgic
        assert any("nostalgic" in w.lower() for w in result["tvpa_flavour_warnings"])
    
    @pytest.fixture
    def tvpa_meta_banned_ai(self, monkeypatch):
        """Mock AI that returns TVPA-violating content in meta description only."""
        def fake_call_ai(settings, provider, model, messages, timeout, json_schema=None, stage=None):
            if stage and "seo_description" in stage:
                # TVPA violation in meta: "candy" is a category violation
                # Must include Blue Razz Ice flavour AND be 115-160 chars to pass length validation
                return {"seo_description": "Shop Blue Razz Ice vape that tastes just like candy at Vapely Canada. This sweet candy-like flavour is perfect for Canadian vapers."}
            return {}
        
        monkeypatch.setattr(providers_module, "_call_ai", fake_call_ai)
        monkeypatch.setattr(gen_module, "_call_ai", fake_call_ai)
        monkeypatch.setattr(providers_module, "_require_provider_credentials", lambda s, p: None)
        monkeypatch.setattr(gen_module, "_require_provider_credentials", lambda s, p: None)
    
    def test_meta_banned_rejected(
        self, test_db, mock_store_identity, tvpa_meta_banned_ai, mock_shopify_writes
    ):
        """Meta description with TVPA banned word (candy) is rejected."""
        conn, db_path = test_db
        _insert_test_product(conn, self.PRODUCT_HANDLE, self.PRODUCT_NAME)
        
        from shopifyseo.dashboard_ai_engine_parts.generation import generate_field_recommendation
        
        with pytest.raises(RuntimeError) as exc_info:
            generate_field_recommendation(
                conn, "product", self.PRODUCT_HANDLE, "seo_description", {}
            )
        
        # Error should mention TVPA or the banned term
        error_str = str(exc_info.value).lower()
        assert "tvpa" in error_str or "candy" in error_str or "flavour" in error_str


class TestCollectionTVPA:
    """Test TVPA compliance for collections."""
    
    COLLECTION_TITLE = "Blue Razz Ice Vapes Collection"
    COLLECTION_HANDLE = "blue-razz-ice-vapes"
    
    @pytest.fixture
    def tvpa_collection_seo_title_banned_ai(self, monkeypatch):
        """Mock AI that returns TVPA-violating content in collection seo_title."""
        def fake_call_ai(settings, provider, model, messages, timeout, json_schema=None, stage=None):
            if stage and "seo_title" in stage:
                # Collection seo_title with TVPA category violation: "candy"
                # Must be 40-65 chars for collections (this is 46 chars)
                return {"seo_title": "Candy-Flavoured Blue Razz Ice Vapes Collection"}
            return {}
        
        monkeypatch.setattr(providers_module, "_call_ai", fake_call_ai)
        monkeypatch.setattr(gen_module, "_call_ai", fake_call_ai)
        monkeypatch.setattr(providers_module, "_require_provider_credentials", lambda s, p: None)
        monkeypatch.setattr(gen_module, "_require_provider_credentials", lambda s, p: None)
    
    def test_collection_title_banned_rejected(
        self, test_db, mock_store_identity, tvpa_collection_seo_title_banned_ai, mock_shopify_writes
    ):
        """Collection seo_title with TVPA banned word (candy) is rejected."""
        conn, db_path = test_db
        _insert_test_collection(conn, self.COLLECTION_HANDLE, self.COLLECTION_TITLE)
        
        from shopifyseo.dashboard_ai_engine_parts.generation import generate_field_recommendation
        
        with pytest.raises(RuntimeError) as exc_info:
            generate_field_recommendation(
                conn, "collection", self.COLLECTION_HANDLE, "seo_title", {}
            )
        
        # Error should mention TVPA or the banned term
        error_str = str(exc_info.value).lower()
        assert "tvpa" in error_str or "candy" in error_str or "flavour" in error_str


# ---------------------------------------------------------------------------
# A4: No auto-apply tests for all generation paths
# ---------------------------------------------------------------------------

class TestNoAutoApply:
    """Test that product seo_title is NEVER auto-applied to Shopify."""
    
    PRODUCT_NAME = "ELFBAR BC5000 - Blue Razz Ice Vape"
    PRODUCT_HANDLE = "elfbar-no-auto-apply"
    EXISTING_SEO_TITLE = "Old SEO Title That Should Not Change"
    
    def test_full_generation_does_not_update_live_seo_title(
        self, test_db, mock_store_identity, mock_ai_client, mock_shopify_writes
    ):
        """Full generation does NOT update the live seo_title in products table."""
        conn, db_path = test_db
        shopify_id = _insert_test_product(
            conn, self.PRODUCT_HANDLE, self.PRODUCT_NAME,
            seo_title=self.EXISTING_SEO_TITLE
        )
        
        # Snapshot before
        before = conn.execute(
            "SELECT seo_title FROM products WHERE shopify_id = ?",
            (shopify_id,)
        ).fetchone()
        
        from shopifyseo.dashboard_ai_engine_parts.generation import generate_recommendation
        result = generate_recommendation(conn, "product", self.PRODUCT_HANDLE)
        
        # The generated title should be the new deterministic format
        expected_new = f"{self.PRODUCT_NAME} | Vapely Canada"
        assert result["seo_title"] == expected_new
        
        # But the live seo_title in products table should be UNCHANGED
        after = conn.execute(
            "SELECT seo_title FROM products WHERE shopify_id = ?",
            (shopify_id,)
        ).fetchone()
        assert after["seo_title"] == self.EXISTING_SEO_TITLE
        
        # No Shopify writes - assert call count is 0
        assert mock_shopify_writes["live_update_product"] == 0
        assert mock_shopify_writes["graphql_request"] == 0
    
    def test_regenerate_field_does_not_update_live_seo_title(
        self, test_db, mock_store_identity, mock_ai_client, mock_shopify_writes
    ):
        """Regenerate-field does NOT update the live seo_title in products table."""
        conn, db_path = test_db
        shopify_id = _insert_test_product(
            conn, self.PRODUCT_HANDLE, self.PRODUCT_NAME,
            seo_title=self.EXISTING_SEO_TITLE
        )
        
        from shopifyseo.dashboard_ai_engine_parts.generation import generate_field_recommendation
        result = generate_field_recommendation(
            conn, "product", self.PRODUCT_HANDLE, "seo_title", {}
        )
        
        # The generated title should be the new deterministic format
        expected_new = f"{self.PRODUCT_NAME} | Vapely Canada"
        assert result["value"] == expected_new
        
        # But the live seo_title in products table should be UNCHANGED
        after = conn.execute(
            "SELECT seo_title FROM products WHERE shopify_id = ?",
            (shopify_id,)
        ).fetchone()
        assert after["seo_title"] == self.EXISTING_SEO_TITLE
        
        # No Shopify writes - assert call count is 0
        assert mock_shopify_writes["live_update_product"] == 0
        assert mock_shopify_writes["graphql_request"] == 0


# ---------------------------------------------------------------------------
# A5: API-level warnings through FastAPI TestClient
# ---------------------------------------------------------------------------

class TestAPIWarnings:
    """Test that warnings are returned through the API endpoints."""
    
    # 57-char product name (produces 73-char title, >60 char warning)
    LONG_PRODUCT_NAME = "ELFBAR GH20000 - Straw Watermelon Glubble Disposable Vape"
    LONG_HANDLE = "glubble-api-warning"
    
    @pytest.fixture
    def api_client(self, testdb, monkeypatch, mock_store_identity, mock_ai_client):
        """Create a FastAPI test client with mocked dependencies."""
        from fastapi.testclient import TestClient
        from backend.app.main import app
        from backend.app import db as db_module
        from backend.app.services import product_service
        
        conn = _make_test_db(testdb)
        _insert_ai_settings(conn)
        _insert_test_product(conn, self.LONG_HANDLE, self.LONG_PRODUCT_NAME)
        conn.close()
        
        def connect():
            return testdb.connect()
        
        # Patch open_db_connection in db module and product_service
        monkeypatch.setattr(db_module, "open_db_connection", connect)
        monkeypatch.setattr(product_service, "open_db_connection", connect)
        
        # Mock Shopify writes to prevent errors
        from shopifyseo import dashboard_live_updates
        monkeypatch.setattr(dashboard_live_updates, "live_update_product", Mock())
        
        yield TestClient(app), connect
    
    def test_regenerate_field_returns_title_warning(self, api_client):
        """Regenerate-field endpoint returns warning for >60 char title."""
        client, connect = api_client
        
        response = client.post(
            f"/api/products/{self.LONG_HANDLE}/regenerate-field",
            json={"field": "seo_title", "accepted_fields": {}},
        )
        
        # Should succeed
        assert response.status_code == 200
        json_response = response.json()
        # API response is wrapped in SuccessResponse with 'data' key
        assert "data" in json_response
        data = json_response["data"]
        
        # Should have the full untruncated title
        expected_title = f"{self.LONG_PRODUCT_NAME} | Vapely Canada"
        assert data["value"] == expected_title
        assert len(data["value"]) == 73
        
        # Should have warnings about length (>60 chars advisory)
        assert "warnings" in data
        assert len(data["warnings"]) >= 1
        assert any("60" in w or "exceed" in w.lower() for w in data["warnings"])


# ---------------------------------------------------------------------------
# Full generation _qa.warnings test
# ---------------------------------------------------------------------------

class TestFullGenerationQAWarnings:
    """Test that full generation result carries _qa.warnings and meta_strength_warnings."""
    
    # 57-char product name (produces 73-char title, >60 char warning)
    LONG_PRODUCT_NAME = "ELFBAR GH20000 - Straw Watermelon Glubble Disposable Vape"
    LONG_HANDLE = "glubble-qa-warnings"
    
    def test_full_generation_result_has_qa_warnings(
        self, test_db, mock_store_identity, mock_ai_client, mock_shopify_writes
    ):
        """Full generation result carries _qa.warnings and title_length_warnings."""
        conn, db_path = test_db
        _insert_test_product(conn, self.LONG_HANDLE, self.LONG_PRODUCT_NAME)
        
        from shopifyseo.dashboard_ai_engine_parts.generation import generate_recommendation
        result = generate_recommendation(conn, "product", self.LONG_HANDLE)
        
        # Result should have _qa key
        assert "_qa" in result
        qa = result["_qa"]
        
        # _qa should have warnings and title_length_warnings
        assert "warnings" in qa
        assert "title_length_warnings" in qa
        assert "meta_strength_warnings" in qa
        
        # Title is 73 chars (>60), so should have title length warning
        expected_title = f"{self.LONG_PRODUCT_NAME} | Vapely Canada"
        assert result["seo_title"] == expected_title
        assert len(result["seo_title"]) == 73
        
        # title_length_warnings should have the >60 char warning
        assert len(qa["title_length_warnings"]) >= 1
        assert any("60" in w or "exceed" in w.lower() for w in qa["title_length_warnings"])
        
        # warnings should include title_length_warnings
        assert len(qa["warnings"]) >= 1


# ---------------------------------------------------------------------------
# D: Page-title enforcement of 42-65 limits
# ---------------------------------------------------------------------------

class TestPageTitleEnforcement:
    """Test that pages still enforce the 42-65 character limit."""
    
    def test_page_seo_title_over_65_fails_validation(self, test_db, mock_store_identity):
        """Page seo_title over 65 chars MUST fail validation."""
        from shopifyseo.seo_quality import validate_metadata
        
        # 70-char page title (too long - over 65 limit)
        fields = {
            "seo_title": "Ultimate Guide to Vaping in Canada: Everything You Need to Know Today!"
        }
        assert len(fields["seo_title"]) == 70
        assert len(fields["seo_title"]) > 65  # Over the page limit
        
        # Should raise for page
        with pytest.raises(ValueError) as exc_info:
            validate_metadata("page", fields)
        
        assert "too long" in str(exc_info.value).lower()
    
    def test_page_seo_title_under_42_fails_validation(self, test_db, mock_store_identity):
        """Page seo_title under 42 chars MUST fail validation."""
        from shopifyseo.seo_quality import validate_metadata
        
        # 34-char page title (too short - under 42 limit)
        fields = {
            "seo_title": "Vaping Guide for Canadian Shoppers"
        }
        assert len(fields["seo_title"]) == 34
        assert len(fields["seo_title"]) < 42  # Under the page limit
        
        # Should raise for page
        with pytest.raises(ValueError) as exc_info:
            validate_metadata("page", fields)
        
        assert "too short" in str(exc_info.value).lower()
    
    def test_page_seo_title_in_range_passes(self, test_db, mock_store_identity):
        """Page seo_title in 42-65 range MUST pass validation."""
        from shopifyseo.seo_quality import validate_metadata
        
        # 55-char page title (in range)
        fields = {
            "seo_title": "Complete Guide to Vaping in Canada - Everything to Know"
        }
        assert 42 <= len(fields["seo_title"]) <= 65
        
        # Should NOT raise for page
        validate_metadata("page", fields)  # No exception = pass
