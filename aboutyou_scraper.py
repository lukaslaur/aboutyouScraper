import argparse
import asyncio
import csv
import json
import sys
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Optional
from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout

BASE_URL = "https://www.aboutyou.lt"
MENS_CLOTHING_URL = f"{BASE_URL}/c/vyrams"

SCROLL_PAUSE_MS   = 2500
MAX_SCROLL_STALLS = 3
DETAIL_DELAY_S    = 1.0
PAGE_TIMEOUT_MS   = 45_000

@dataclass
class Product:
    name:        str = ""
    brand:       str = ""
    final_price: str = ""
    sale_price:  str = ""
    colors:      str = ""   
    sizes:       str = ""   
    image_urls:  str = ""  
    description: str = ""
    product_url: str = ""
    category:    str = "Men's Clothing"

    def to_dict(self):
        return {f.name: getattr(self, f.name) for f in fields(self)}


CARD_SELECTORS = [
    "[data-testid='product-card']",
    "[class*='productCard']",
    "[class*='ProductCard']",
    "[class*='product-card']",
    "[class*='product_card']",
    "article[class*='product']",
]

LINK_SELECTORS = [
    "a[href*='/p/']",
    "a[data-testid='product-link']",
    "[data-testid='product-card'] a",
    "[class*='productCard'] a",
    "[class*='ProductCard'] a",
    "[class*='product-card'] a",
]


async def make_context(pw, headless: bool):
    browser = await pw.chromium.launch(headless=headless)
    context = await browser.new_context(
        viewport={"width": 1440, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="lt-LT",
        extra_http_headers={"Accept-Language": "lt-LT,lt;q=0.9,en;q=0.8"},
    )
    return browser, context


async def dismiss_overlays(page: Page):
    """Click away cookie banners and newsletter popups."""
    selectors = [
        "button[data-testid='uc-accept-all-button']",
        "#usercentrics-root >> button:has-text('Accept')",
        "button[class*='acceptAll']",
        "button[class*='CookieAccept']",
        "#onetrust-accept-btn-handler",
        "button:has-text('Sutikti')",
        "button:has-text('Priimti')",
        "button:has-text('Accept all')",
        "button:has-text('Alle akzeptieren')",
        "[aria-label='Close']",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2500):
                await btn.click()
                await page.wait_for_timeout(600)
        except Exception:
            pass


async def _first_text(page: Page, selectors: list, min_len: int = 1) -> str:
    """Return inner text of the first visible matching element."""
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count() and await el.is_visible(timeout=1500):
                txt = (await el.inner_text()).strip()
                if len(txt) >= min_len:
                    return txt
        except Exception:
            pass
    return ""


async def count_cards(page: Page) -> int:
    for sel in CARD_SELECTORS:
        n = await page.locator(sel).count()
        if n:
            return n
    for sel in LINK_SELECTORS:
        n = await page.locator(sel).count()
        if n:
            return n
    return 0


async def scroll_to_load_all(page: Page, max_products: Optional[int]):
    print("  Scrolling to load all products...")
    prev_count = 0
    stalls = 0

    while True:
        current = await count_cards(page)

        if max_products and current >= max_products:
            print(f"  Reached --max-products limit ({max_products}).")
            break

        if current > prev_count:
            print(f"  Products visible: {current}", end="\r", flush=True)
            prev_count = current
            stalls = 0
        else:
            stalls += 1
            if stalls >= MAX_SCROLL_STALLS:
                print(f"\n  No new products after {MAX_SCROLL_STALLS} scrolls. Done.")
                break

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(SCROLL_PAUSE_MS)


async def collect_links(page: Page, max_products: Optional[int]) -> list:
    links = []
    for sel in LINK_SELECTORS:
        anchors = await page.locator(sel).all()
        for a in anchors:
            href = await a.get_attribute("href") or ""
            if not href:
                continue
            full = href if href.startswith("http") else BASE_URL + href
            if full not in links:
                links.append(full)
        if links:
            break
    if max_products:
        links = links[:max_products]
    return links


async def parse_product_page(page: Page, url: str) -> Product:
    p = Product(product_url=url)

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
        await dismiss_overlays(page)
        await page.wait_for_timeout(1200)

        p.name = await _first_text(page, [
            "h1[data-testid='product-name']",
            "h1[class*='productName']",
            "h1[class*='ProductName']",
            "h1[class*='name']",
            "h1",
        ])

        p.brand = await _first_text(page, [
            "[data-testid='brand-name']",
            "[data-testid='brandNameContainer']",
            "[class*='brandName']",
            "[class*='BrandName']",
            "a[href*='/brand/']",
            "span[class*='brand']",
            "p[class*='brand']",
        ])

        p.final_price = await _first_text(page, [
            "[data-testid='product-price']",
            "[data-testid='finalPrice']",
            "[data-testid='regular-price']",
            "[class*='regularPrice']",
            "[class*='currentPrice']",
            "[class*='finalPrice']",
            "span[class*='price']:not([class*='old']):not([class*='strike'])",
        ])

        p.sale_price = await _first_text(page, [
            "[data-testid='old-price']",
            "[class*='oldPrice']",
            "[class*='OldPrice']",
            "[class*='strikethrough']",
            "del span",
            "s span",
            "[class*='salePrice']",
        ])

        colors = []
        for sel in [
            "[data-testid='color-name']",
            "[data-testid='productColorOptionsSelectedOptionName']",
            "[data-testid='productColorInfoSelectedOptionName']",
            "[class*='colorName']",
            "[class*='ColorName']",
            "button[class*='color'] [aria-label]",
            "[class*='ColorSwatch'] [aria-label]",
            "li[class*='color'] [aria-label]",
        ]:
            els = await page.locator(sel).all()
            for el in els:
                label = (
                    (await el.get_attribute("aria-label") or "").strip()
                    or (await el.inner_text()).strip()
                )
                if label and label not in colors:
                    colors.append(label)
            if colors:
                break
        p.colors = " | ".join(colors)

        sizes = []
        for sel in [
            "[data-testid='size-selector'] button",
            "[data-testid='sizeOptionList'] button",
            "[class*='sizeSelector'] button",
            "[class*='SizeSelector'] button",
            "[class*='size-button']",
            "button[class*='size']",
            "[data-testid='size-item']",
        ]:
            els = await page.locator(sel).all()
            for el in els:
                txt = (await el.inner_text()).strip()
                if txt and txt not in sizes:
                    sizes.append(txt)
            if sizes:
                break
        p.sizes = " | ".join(sizes)

        images = []
        for sel in [
            "[data-testid='product-image'] img",
            "[data-testid='productImageView'] img",
            "[class*='productImage'] img",
            "[class*='ProductImage'] img",
            "[class*='gallery'] img",
            "[class*='Gallery'] img",
            "img[class*='product']",
        ]:
            els = await page.locator(sel).all()
            for el in els:
                src = (
                    (await el.get_attribute("src") or "")
                    or (await el.get_attribute("data-src") or "")
                ).split("?")[0]
                if src and src.startswith("http") and src not in images:
                    images.append(src)
            if images:
                break
        p.image_urls = " | ".join(images[:5])

        p.description = await _first_text(page, [
            "[data-testid='product-description']",
            "[data-testid='productDetailsOpenerLabel']",
            "[class*='productDescription']",
            "[class*='ProductDescription']",
            "[class*='description']",
            "div[class*='details'] p",
        ], min_len=20)
        if p.description:
            p.description = " ".join(p.description.split())

        if not p.name or not p.final_price:
            scripts = await page.locator("script[type='application/ld+json']").all()
            for script in scripts:
                try:
                    data = json.loads(await script.inner_text())
                    if isinstance(data, list):
                        data = next((d for d in data if d.get("@type") == "Product"), {})
                    if data.get("@type") != "Product":
                        continue

                    p.name        = p.name  or data.get("name", "")
                    p.description = p.description or data.get("description", "")

                    brand = data.get("brand") or {}
                    p.brand = p.brand or (brand.get("name", "") if isinstance(brand, dict) else str(brand))

                    offers = data.get("offers", {})
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    price_val = offers.get("price") or offers.get("lowPrice") or ""
                    currency  = offers.get("priceCurrency", "")
                    if price_val and not p.final_price:
                        p.final_price = f"{price_val} {currency}".strip()

                    imgs = data.get("image", [])
                    if isinstance(imgs, str):
                        imgs = [imgs]
                    if imgs and not p.image_urls:
                        p.image_urls = " | ".join(imgs[:5])

                    break
                except Exception:
                    pass

    except PlaywrightTimeout:
        print(f"\n  ⚠  Timeout: {url}")
    except Exception as exc:
        print(f"\n  ⚠  Error ({type(exc).__name__}): {url} — {exc}")

    return p

async def run_diagnose():
    """Save listing page HTML and print selector counts to help tune selectors."""
    print("🔍 Diagnostic mode — opening listing page in browser...")
    async with async_playwright() as pw:
        browser, ctx = await make_context(pw, headless=False)
        page = await ctx.new_page()
        await page.goto(MENS_CLOTHING_URL, wait_until="networkidle", timeout=60_000)
        await dismiss_overlays(page)
        await page.wait_for_timeout(3000)

        html = await page.content()
        out = Path("diagnose.html")
        out.write_text(html, encoding="utf-8")
        print(f"  HTML saved → {out}  ({len(html):,} chars)")

        print("\n  Selector counts on listing page:")
        for sel in CARD_SELECTORS + LINK_SELECTORS:
            n = await page.locator(sel).count()
            print(f"    {n:4d}  {sel}")

        await browser.close()

    print(
        "\nTip: Open diagnose.html in your browser (or DevTools),"
        " then update CARD_SELECTORS / LINK_SELECTORS at the top of the script."
    )
async def run_scraper(headless: bool, max_products: Optional[int], output_file: str):
    print("=" * 60)
    print("  AboutYou.lt — Men's Clothing Scraper")
    print("=" * 60)
    print(f"  URL        : {MENS_CLOTHING_URL}")
    print(f"  Headless   : {headless}")
    print(f"  Max items  : {max_products or 'all'}")
    print(f"  Output     : {output_file}")
    print()

    products = []

    async with async_playwright() as pw:
        browser, ctx = await make_context(pw, headless=headless)
        listing_page = await ctx.new_page()

        print("[1/3] Opening listing page...")
        await listing_page.goto(MENS_CLOTHING_URL, wait_until="networkidle", timeout=60_000)
        await dismiss_overlays(listing_page)
        await listing_page.wait_for_timeout(2000)

        print("[2/3] Loading products via scroll...")
        await scroll_to_load_all(listing_page, max_products)

        links = await collect_links(listing_page, max_products)
        print(f"\n  Found {len(links)} product link(s).")

        if not links:
            print(
                "\n⚠  No product links found.\n"
                "  The site may have updated its HTML structure.\n"
                "  Run:  python aboutyou_scraper.py --diagnose\n"
                "  to inspect selectors and update CARD_SELECTORS / LINK_SELECTORS."
            )
            await browser.close()
            return

        print(f"\n[3/3] Scraping {len(links)} product pages...\n")
        detail_page = await ctx.new_page()

        for i, url in enumerate(links, 1):
            display = (url[:72] + "...") if len(url) > 75 else url
            print(f"  [{i:>4}/{len(links)}] {display}")
            product = await parse_product_page(detail_page, url)
            products.append(product)
            await asyncio.sleep(DETAIL_DELAY_S)

        await browser.close()

    if not products:
        print("\n⚠  No products scraped.")
        return

    fieldnames = [f.name for f in fields(Product)]
    with open(output_file, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(p.to_dict() for p in products)

    filled = sum(1 for p in products if p.name)
    print(f"\n✅  Done! {filled}/{len(products)} products saved → '{output_file}'")

def main():
    parser = argparse.ArgumentParser(
        description="Scrape men's clothing from aboutyou.lt",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--max-products", type=int, default=None, metavar="N",
        help="Stop after N products (default: all)",
    )
    parser.add_argument(
        "--no-headless", dest="headless", action="store_false", default=True,
        help="Show the browser window",
    )
    parser.add_argument(
        "--output", default="aboutyou_mens_clothing.csv", metavar="FILE",
        help="Output CSV filename (default: aboutyou_mens_clothing.csv)",
    )
    parser.add_argument(
        "--diagnose", action="store_true",
        help="Dump listing page HTML + selector counts, then exit",
    )
    args = parser.parse_args()

    if args.diagnose:
        asyncio.run(run_diagnose())
    else:
        asyncio.run(run_scraper(
            headless=args.headless,
            max_products=args.max_products,
            output_file=args.output,
        ))


if __name__ == "__main__":
    main()
