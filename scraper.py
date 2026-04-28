#!/usr/bin/env python3
"""
Scraper yanhh3d.bz → MonPlayer JSON (List View)
Chỉ lấy phim ĐÃ HOÀN THÀNH, output đúng schema để app hiển thị ngay
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
    "LISTING_PATH": "/",
    "MAX_MOVIES": 20,
    "REQUIRE_COMPLETE": True,
    "OUTPUT_FILE": "ophim.json",  # ✅ Đổi tên cho dễ nhận diện
    "TIMEOUT": 45000,
}

SELECTORS = {
    "card": ".flw-item.swiper-slide",
    "title": ".tick.ltr h4",
    "link": "a.film-poster-ahref",
    "thumb": "img.film-poster-img[data-src]",
    "episode": ".tick.tick-rate",
}

def is_completed(episode_text: str) -> bool:
    if not episode_text: return False
    if "hoàn tất" in episode_text.lower(): return True
    match = re.search(r'(\d+)\s*/\s*(\d+)', episode_text)
    if match:
        return int(match.group(1)) >= int(match.group(2)) > 0
    return False

def parse_card(card, index: int) -> dict | None:
    try:
        link_el = card.select_one(SELECTORS["link"])
        if not link_el or not link_el.get("href"): return None
        
        # Extract slug from URL for MonPlayer id
        href = link_el["href"]
        slug = href.rstrip("/").split("/")[-1] if href.startswith("http") else href.rstrip("/").split("/")[-1]
        
        title_el = card.select_one(SELECTORS["title"])
        title = title_el.get_text(strip=True) if title_el else link_el.get("title", "").strip()
        if not title: return None

        thumb_el = card.select_one(SELECTORS["thumb"])
        thumb = thumb_el.get("data-src") if thumb_el else None
        if thumb and not thumb.startswith("http"):
            thumb = CONFIG["BASE_URL"] + thumb

        ep_el = card.select_one(SELECTORS["episode"])
        episode_text = ep_el.get_text(strip=True) if ep_el else ""
        
        if CONFIG["REQUIRE_COMPLETE"] and not is_completed(episode_text):
            return None

        # ✅ Format label text cho MonPlayer
        label_text = episode_text if episode_text else "Hoàn tất"
        if re.search(r'\d+/\d+', episode_text) and "hoàn tất" not in episode_text.lower():
            label_text = f"Hoàn tất ({episode_text})"

        return {
            "id": slug,
            "name": title,
            "description": "",  # Có thể scrape thêm sau
            "image": {
                "url": thumb or "",
                "type": "cover",
                "width": 480,
                "height": 640
            },
            "type": "playlist",
            "display": "text-below",
            "label": {
                "text": label_text,
                "position": "top-left",
                "color": "#35ba8b",
                "text_color": "#ffffff"
            },
            "remote_data": {
                "url": f"{CONFIG['BASE_URL']}/{slug}"  # ✅ Link gốc, app có thể dùng để redirect
            },
            "enable_detail": False  # ✅ Tạm tắt, bật khi có scraper detail
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
            page.set_extra_http_headers({
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.7,en;q=0.3",
                "Referer": "https://www.google.com/",
                "Upgrade-Insecure-Requests": "1",
            })
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
                Object.defineProperty(navigator, 'languages', {get: () => ['vi-VN','vi','en-US','en']});
            """)

            page.goto(url, wait_until="networkidle", timeout=CONFIG["TIMEOUT"])
            page.wait_for_selector(SELECTORS["card"], timeout=10000)
            page.wait_for_timeout(1500)

            soup = BeautifulSoup(page.content(), "lxml")
            cards = soup.select(SELECTORS["card"])

            for i, card in enumerate(cards):
                if len(results) >= CONFIG["MAX_MOVIES"]: break
                item = parse_card(card, i)
                if item:
                    results.append(item)
                    logger.info(f"Added: {item['name']}")

        except Exception as e:
            logger.error(f"Scrape failed: {e}")
        finally:
            browser.close()

    # ✅ Output đúng MonPlayer list schema
    output = {
        "grid_number": 3,
        "channels": results,
        "meta": {
            "source": CONFIG["BASE_URL"],
            "total": len(results),
            "completed_only": CONFIG["REQUIRE_COMPLETE"],
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
    }

    with open(CONFIG["OUTPUT_FILE"], "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    logger.info(f"Saved {len(results)} movies to {CONFIG['OUTPUT_FILE']}")
    return output

if __name__ == "__main__":
    scrape()
