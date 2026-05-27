AboutYou.lt Men's Clothing Scraper
====================================
Scrapes men's clothing from aboutyou.lt using Playwright (async, Chromium).

Collects per product:
  name, brand, price, sale_price, colors, sizes, image_urls, description, product_url, category

Output: aboutyou_mens_clothing.csv  (or custom filename via --output)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Requirements
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    pip install playwright
    playwright install chromium

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Usage
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Scrape everything (headless)
python aboutyou_scraper.py

# Scrape first 50 products only
python aboutyou_scraper.py --max-products 50

# Show the browser (useful for debugging / inspecting the site)
python aboutyou_scraper.py --no-headless

# Custom output file
python aboutyou_scraper.py --output mydata.csv

# Dump raw HTML of the listing page (helps tune selectors)
python aboutyou_scraper.py --diagnose
