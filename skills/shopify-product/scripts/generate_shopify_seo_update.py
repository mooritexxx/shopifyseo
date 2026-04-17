#!/usr/bin/env python3
import argparse
import csv
import html
import re
from pathlib import Path

# Set this to your store's brand name — used in SEO titles, meta descriptions, and body copy.
STORE_NAME = "ShopifySEO"
STORE_REGION = "Canada"  # Set to your target market region (used in SEO copy)


def slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value


def smart_trim(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit + 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,.;:-")


def first_nonempty_within_limit(candidates, limit: int) -> str:
    for candidate in candidates:
        candidate = re.sub(r"\s+", " ", candidate).strip()
        if candidate and len(candidate) <= limit:
            return candidate
    return smart_trim(re.sub(r"\s+", " ", candidates[-1]).strip(), limit)


def extract_flavor(title: str) -> str:
    title = title or ""
    prefixes = [
        "ELFBAR GH20000 - ",
        "ELFBAR BC10000 - ",
        "ALLO NUUD 50K INTENSE - ",
        "ALLO ULTRA 2500 ",
        "ALLO ULTRA 25K - ",
    ]
    for prefix in prefixes:
        if title.startswith(prefix):
            title = title[len(prefix) :]
    title = re.sub(r" Disposable Vape.*", "", title, flags=re.I)
    return title.strip()


def flavor_families(flavor: str):
    f = flavor.lower()
    families = []
    if "mint" in f:
        families.append("flavor_family_mint")
    if "ice" in f or "iced" in f:
        families.append("flavor_family_ice")
    fruit_terms = [
        "apple", "banana", "berry", "blue", "blueberry", "cherry", "cranapple",
        "dragonfruit", "fruit", "fuji", "grape", "guava", "kiwi", "lemon",
        "lychee", "mango", "melon", "orange", "passionfruit", "peach", "pear",
        "pineapple", "pomegranate", "razz", "sakura", "straw", "strawberry",
        "tropical", "watermelon",
    ]
    if any(term in f for term in fruit_terms):
        families.append("flavor_family_fruit")
    if any(term in f for term in ["cloudz", "blast", "burst", "glubble", "prism"]):
        families.append("flavor_family_candy")
    if "classic" in f or "og" in f:
        families.append("flavor_family_classic")
    unique = []
    for family in families:
        if family not in unique:
            unique.append(family)
    return unique


def build_tags(row: dict) -> str:
    vendor = row.get("Vendor", "")
    model = row.get("Collection (product.metafields.custom.collection)", "")
    puff = row.get("Puff Count (product.metafields.custom.puff_count)", "")
    nicotine = row.get("Nicotine Strength (product.metafields.custom.nicotine_strength)", "")
    charging = row.get("Charging Port (product.metafields.custom.charging_port)", "")
    device = row.get("Device Type (product.metafields.custom.device_type)", "")
    flavor = extract_flavor(row.get("Title", ""))
    tags = [
        f"brand_{slugify(vendor)}" if vendor else "",
        f"model_{slugify(model)}" if model else "",
        f"device_{slugify(device)}" if device else "",
        f"puff_{slugify(puff)}" if puff else "",
        f"nicotine_{slugify(nicotine.lower().replace(' nicotine salt', '').replace(' ', ''))}" if nicotine else "",
        "rechargeable_yes" if charging not in ("", "NONE") else "rechargeable_no",
        f"flavor_{slugify(flavor)}" if flavor else "",
    ]
    tags.extend(flavor_families(flavor))
    unique = []
    for tag in tags:
        if tag and tag not in unique:
            unique.append(tag)
    return ", ".join(unique)


def build_title(row: dict) -> str:
    vendor = row.get("Vendor", "")
    model = row.get("Collection (product.metafields.custom.collection)", "")
    flavor = extract_flavor(row.get("Title", ""))
    puff = row.get("Puff Count (product.metafields.custom.puff_count)", "")
    candidates = [
        f"{vendor} {model} {flavor} {puff} Puff {STORE_REGION} | {STORE_NAME}",
        f"{vendor} {model} {flavor} {STORE_REGION} | {STORE_NAME}",
        f"{vendor} {model} {flavor} | {STORE_NAME}",
        f"{vendor} {model} {STORE_REGION} | {STORE_NAME}",
    ]
    return first_nonempty_within_limit(candidates, 65)


def build_meta(row: dict) -> str:
    vendor = row.get("Vendor", "")
    model = row.get("Collection (product.metafields.custom.collection)", "")
    flavor = extract_flavor(row.get("Title", ""))
    puff = row.get("Puff Count (product.metafields.custom.puff_count)", "")
    nicotine = row.get("Nicotine Strength (product.metafields.custom.nicotine_strength)", "")
    rechargeable = row.get("Charging Port (product.metafields.custom.charging_port)", "") not in ("", "NONE")
    recharge_text = "rechargeable disposable vape" if rechargeable else "disposable vape"
    candidates = [
        f"Shop {vendor} {model} {flavor} in {STORE_REGION}. {puff} puff {recharge_text} with {nicotine.lower()} and fast shipping from {STORE_NAME}.",
        f"Shop {vendor} {model} {flavor} in {STORE_REGION}. {puff} puff {recharge_text} and fast shipping from {STORE_NAME}.",
        f"Buy {vendor} {model} {flavor} in {STORE_REGION}. {puff} puffs and fast shipping from {STORE_NAME}.",
        f"Buy {vendor} {model} {flavor} in {STORE_REGION} at {STORE_NAME}.",
    ]
    return first_nonempty_within_limit(candidates, 155)


def build_body(row: dict) -> str:
    vendor = row.get("Vendor", "")
    model = row.get("Collection (product.metafields.custom.collection)", "")
    flavor = extract_flavor(row.get("Title", ""))
    puff = row.get("Puff Count (product.metafields.custom.puff_count)", "")
    nicotine = row.get("Nicotine Strength (product.metafields.custom.nicotine_strength)", "")
    size = row.get("Size (product.metafields.custom.size)", "")
    battery = row.get("Battery Size (product.metafields.custom.battery_size)", "")
    charging = row.get("Charging Port (product.metafields.custom.charging_port)", "")
    coil = row.get("Coil (product.metafields.custom.coil)", "")
    rechargeable = charging not in ("", "NONE")

    spec_items = []
    if puff:
        spec_items.append(f"<li>Up to {html.escape(puff)} puffs</li>")
    if nicotine:
        spec_items.append(f"<li>{html.escape(nicotine)}</li>")
    if size:
        spec_items.append(f"<li>{html.escape(size)} e-liquid capacity</li>")
    if battery:
        spec_items.append(f"<li>{html.escape(battery)} battery</li>")
    if rechargeable and charging:
        spec_items.append(f"<li>{html.escape(charging)}</li>")
    if coil:
        spec_items.append(f"<li>{html.escape(coil)} coil</li>")

    recharge_phrase = "rechargeable disposable vape" if rechargeable else "disposable vape"
    intro = (
        f"<p>Buy the <strong>{html.escape(vendor)} {html.escape(model)} {html.escape(flavor)}</strong> "
        f"{recharge_phrase} in {STORE_REGION} at {STORE_NAME}.</p>"
    )
    mid = (
        f"<p>The {html.escape(vendor)} {html.escape(model)} {html.escape(flavor)} is a "
        f"<strong>{html.escape(puff)} puff {recharge_phrase}</strong> for adult consumers who want strong flavor, "
        f"portable convenience, and consistent day-to-day performance.</p>"
    )
    features = "<h2>Product Features</h2><ul>" + "".join(spec_items) + "</ul>"
    outro = (
        f"<p>If you are shopping for <strong>{html.escape(vendor)} {html.escape(model)} flavors in {STORE_REGION}</strong>, "
        f"{html.escape(flavor)} is a strong option for adult users looking for a high-puff product in this range.</p>"
    )
    return intro + mid + features + outro


def selected(row: dict, args) -> bool:
    if args.handle_contains and args.handle_contains not in row.get("Handle", ""):
        return False
    if args.vendor and row.get("Vendor", "") != args.vendor:
        return False
    if args.model and row.get("Collection (product.metafields.custom.collection)", "") != args.model:
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="Generate Shopify-ready SEO update CSVs.")
    parser.add_argument("--input", required=True, help="Path to Shopify export CSV")
    parser.add_argument("--output", required=True, help="Path to write update CSV")
    parser.add_argument("--notes", help="Optional notes file path")
    parser.add_argument("--audit", help="Optional preflight audit file path")
    parser.add_argument("--handle-contains", help="Select products whose handle contains this value")
    parser.add_argument("--vendor", help="Select products by exact vendor")
    parser.add_argument("--model", help="Select products by exact collection metafield")
    parser.add_argument(
        "--mode",
        choices=["missing-only", "normalize-scope", "full-regenerate"],
        default="missing-only",
        help="How aggressively to update the selected fields",
    )
    parser.add_argument("--include-tags", action="store_true", help="Update standardized Tags and normalize them across the selected scope")
    parser.add_argument("--include-body", action="store_true", help="Update Body (HTML)")
    parser.add_argument("--include-seo", action="store_true", help="Update SEO Title and SEO Description")
    args = parser.parse_args()

    with open(args.input, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    selected_handles = []
    seen = set()
    output_rows = []
    first_rows = []

    for row in rows:
        row = row.copy()
        handle = row.get("Handle", "")
        if handle and selected(row, args) and handle not in seen:
            seen.add(handle)
            selected_handles.append(handle)
            first_rows.append(row.copy())
            if args.include_tags and (
                args.mode in ("normalize-scope", "full-regenerate") or not row.get("Tags", "").strip()
            ):
                row["Tags"] = build_tags(row)
            if args.include_body and (
                args.mode == "full-regenerate" or not row.get("Body (HTML)", "").strip()
            ):
                row["Body (HTML)"] = build_body(row)
            if args.include_seo:
                if args.mode == "full-regenerate" or not row.get("SEO Title", "").strip():
                    row["SEO Title"] = build_title(row)
                if args.mode == "full-regenerate" or not row.get("SEO Description", "").strip():
                    row["SEO Description"] = build_meta(row)
        output_rows.append(row)

    with open(args.output, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(output_rows)

    if args.audit:
        body_missing = sum(1 for row in first_rows if not row.get("Body (HTML)", "").strip())
        title_missing = sum(1 for row in first_rows if not row.get("SEO Title", "").strip())
        desc_missing = sum(1 for row in first_rows if not row.get("SEO Description", "").strip())
        tags_missing = sum(1 for row in first_rows if not row.get("Tags", "").strip())
        tags_legacy = sum(1 for row in first_rows if row.get("Tags", "").strip() and "brand_" not in row.get("Tags", ""))
        audit_lines = [
            "# Shopify SEO Preflight Audit",
            "",
            f"- Input: `{Path(args.input).name}`",
            f"- Mode: `{args.mode}`",
            f"- Selected products: {len(selected_handles)}",
            f"- Missing `Body (HTML)`: {body_missing}",
            f"- Missing `SEO Title`: {title_missing}",
            f"- Missing `SEO Description`: {desc_missing}",
            f"- Missing `Tags`: {tags_missing}",
            f"- Products with mixed or legacy tags in scope: {tags_legacy}",
        ]
        Path(args.audit).write_text("\n".join(audit_lines), encoding="utf-8")

    if args.notes:
        lines = [
            "# Shopify SEO Update Notes",
            "",
            f"- Input: `{Path(args.input).name}`",
            f"- Output: `{Path(args.output).name}`",
            f"- Mode: `{args.mode}`",
            f"- Selected products: {len(selected_handles)}",
            f"- Include body: {'yes' if args.include_body else 'no'}",
            f"- Include SEO fields: {'yes' if args.include_seo else 'no'}",
            f"- Include tags: {'yes' if args.include_tags else 'no'}",
        ]
        Path(args.notes).write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
