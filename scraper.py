#!/usr/bin/env python3
"""
Scraper yanhh3d.bz → MonPlayer JSON (List View)
- Dùng page.evaluate() để trích xuất trong browser context (tránh vấn đề visibility)
- Chỉ lấy phim hoàn thành (X/X hoặc "Hoàn tất")
- Output đúng schema MonPlayer với cấu trúc nhiều lớp
"""

import json
import os
import re
import logging
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CONFIG = {
    "BASE_URL": "https://yanhh3d.bz",
    "LISTING_PATH": "/",
    "MAX_MOVIES": 20,
    "REQUIRE_COMPLETE": True,
    "OUTPUT_FILE": "ophim.json",
    "TIMEOUT": 30000,
}

def is_completed(episode_text: str) -> bool:
    if not episode_text:
        return False
    if "hoàn tất" in episode_text.lower():
        return True
    match = re.search(r'(\d+)\s*/\s*(\d+)', episode_text)
    if match:
        return int(match.group(1)) >= int(match.group(2)) > 0
    return False

def scrape():
    url = CONFIG["BASE_URL"] + CONFIG["LISTING_PATH"]
    logger.info(f"Scraping: {url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        # Headers + stealth để tránh Cloudflare
        page.set_extra_http_headers({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
            "Referer": "https://www.google.com/",
            "Upgrade-Insecure-Requests": "1",
        })
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
            Object.defineProperty(navigator, 'languages', {get: () => ['vi-VN', 'vi', 'en-US', 'en']});
        """)

        try:
            page.goto(url, wait_until="networkidle", timeout=CONFIG["TIMEOUT"])
            # Chờ element tồn tại trong DOM (không cần visible)
            page.wait_for_selector(".flw-item.swiper-slide", state="attached", timeout=15000)
            page.wait_for_timeout(2000)  # Đợi thêm cho JS render xong

            # ✅ Trích xuất dữ liệu TRONG browser context (tránh vấn đề parsing HTML sau)
            movies = page.evaluate("""() => {
                const results = [];
                const cards = document.querySelectorAll('.flw-item.swiper-slide');
                
                cards.forEach(card => {
                    const linkEl = card.querySelector('a.film-poster-ahref');
                    const titleEl = card.querySelector('.tick.ltr h4');
                    const thumbEl = card.querySelector('img.film-poster-img');
                    const epEl = card.querySelector('.tick.tick-rate');
                    
                    if (!linkEl || !linkEl.href) return;
                    
                    const href = linkEl.href;
                    const slug = href.split('/').pop().replace(/\\/$/, '');
                    const title = titleEl ? titleEl.innerText.trim() : linkEl.title || '';
                    if (!title) return;
                    
                    let thumb = thumbEl ? (thumbEl.dataset.src || thumbEl.src) : '';
                    if (thumb && !thumb.startsWith('http')) {
                        thumb = 'https://yanhh3d.bz' + thumb;
                    }
                    
                    const episodeText = epEl ? epEl.innerText.trim() : '';
                    
                    results.push({
                        slug: slug,
                        title: title,
                        thumb: thumb,
                        episode: episodeText
                    });
                });
                
                return results;
            }""")

            # Lọc phim hoàn thành + format output
            channels = []
            for m in movies:
                if CONFIG["REQUIRE_COMPLETE"] and not is_completed(m["episode"]):
                    continue
                if len(channels) >= CONFIG["MAX_MOVIES"]:
                    break
                
                label_text = m["episode"] if m["episode"] else "Hoàn tất"
                if re.search(r'\\d+/\\d+', m["episode"]) and "hoàn tất" not in m["episode"].lower():
                    label_text = f"Hoàn tất ({m['episode']})"

                channels.append({
                    "id": m["slug"],
                    "name": m["title"],
                    "description": "",
                    "image": {
                        "url": m["thumb"],
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
                        "url": f"{CONFIG['BASE_URL']}/{m['slug']}"
                    },
                    "enable_detail": False
                })
                logger.info(f"Added: {m['title']}")

        except Exception as e:
            logger.error(f"Scrape failed: {e}")
            channels = []
        finally:
            browser.close()

    # ✅ Output đúng MonPlayer schema với cấu trúc nhiều lớp
    output = {
        "grid_number": 3,
        "channels": channels,
        "meta": {
            "source": CONFIG["BASE_URL"],
            "total_items": len(channels),
            "completed_only": CONFIG["REQUIRE_COMPLETE"],
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "version": "1.0"
        }
    }

    with open(CONFIG["OUTPUT_FILE"], "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    logger.info(f"Saved {len(channels)} movies to {CONFIG['OUTPUT_FILE']}")
    return output

if __name__ == "__main__":
    scrape()
