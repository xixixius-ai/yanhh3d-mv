#!/usr/bin/env python3
"""
Scraper yanhh3d.bz → MonPlayer JSON
Chỉ lấy phim ĐÃ HOÀN THÀNH (số tập hiện tại = tổng tập)
"""

import json
import os
import re
import logging
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CONFIG = {
    "BASE_URL": "https://yanhh3d.bz",
    "LISTING_PATH": "/moi-cap-nhat",
    "MAX_MOVIES": 20,
    "REQUIRE_COMPLETE": True,
    "OUTPUT_FILE": "monplayer.json",
    "TIMEOUT": 30000,
}

SELECTORS = {
    "card": ".flw-item.swiper-slide",
    "title": ".tick.ltr h4",
    "link": "a.film-poster-ahref",
    "thumb": "img.film-poster-img[data-src]",
    "episode": ".tick.tick-rate",
}

def is_completed(episode_text: str) -> bool:
    if not episode_text:
        return False
    if "hoàn tất" in episode_text.lower():
        return True
    match = re.search(r'(\d+)\s*/\s*(\d+)', episode_text)
    if match:
        current, total = int(match.group(1)), int(match.group(2))
        return current >= total and total > 0
    return False

def parse_card(card) -> dict | None:
    try:
        link_el = card.select_one(SELECTORS["link"])
        if not link_el or not link_el.get("href"):
            return None
        link = link_el["href"]
        if not link.startswith("http"):
            link = CONFIG["BASE_URL"] + link

        title_el = card.select_one(SELECTORS["title"])
        title = title_el.get_text(strip=True) if title_el else link_el.get("title", "").strip()
        if not title:
            return None

        thumb_el = card.select_one(SELECTORS["thumb"])
        thumb = thumb_el.get("data-src") if thumb_el else None
        if thumb and not thumb.startswith("http"):
            thumb = CONFIG["BASE_URL"] + thumb

        ep_el = card.select_one(SELECTORS["episode"])
        episode_text = ep_el.get_text(strip=True) if ep_el else ""

        if CONFIG["REQUIRE_COMPLETE"] and not is_completed(episode_text):
            return None

        return {
            "title": title,
            "link": link,
            "thumb": thumb or "",
        }
    except Exception as e:
        logger.warning(f"Parse error: {e}")
        return None

def scrape():
    url = CONFIG["BASE_URL"] + CONFIG["LISTING_PATH"]
    logger.info(f"Scraping: {url}")
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        try:
            page.goto(url, wait_until="networkidle", timeout=CONFIG["TIMEOUT"])
            page.wait_for_selector(SELECTORS["card"], timeout=10000)
            page.wait_for_timeout(1500)

            soup = BeautifulSoup(page.content(), "lxml")
            cards = soup.select(SELECTORS["card"])

            for card in cards:
                if len(results) >= CONFIG["MAX_MOVIES"]:
                    break
                item = parse_card(card)
                if item:
                    results.append(item)
                    logger.info(f"Added: {item['title']}")

        except Exception as e:
            logger.error(f"Scrape failed: {e}")
        finally:
            browser.close()

    output = {
        "name": "YanHH3D",
        "items": [
            {
                "title": r["title"],
                "image": r["thumb"],
                "description": "",
                "streams": []
            }
            for r in results
        ],
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "meta": {
            "source": CONFIG["BASE_URL"],
            "total": len(results),
            "completed_only": CONFIG["REQUIRE_COMPLETE"]
        }
    }

    with open(CONFIG["OUTPUT_FILE"], "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    logger.info(f"Saved {len(results)} movies to {CONFIG['OUTPUT_FILE']}")
    return output

if __name__ == "__main__":
    scrape()
