#!/usr/bin/env python3
"""
Scraper yanhh3d.bz → MonPlayer JSON
Lấy 20 phim trending, có debug log để kiểm tra dữ liệu.
"""

import json
import logging
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CONFIG = {
    "BASE_URL": "https://yanhh3d.bz",
    "OUTPUT_FILE": "ophim.json",
    "MAX_MOVIES": 20,
    "TIMEOUT": 30000,
}

def scrape():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()

        page.set_extra_http_headers({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
            "Referer": "https://www.google.com/",
        })
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
        """)

        try:
            page.goto(CONFIG["BASE_URL"], wait_until="networkidle", timeout=CONFIG["TIMEOUT"])
            page.wait_for_selector(".flw-item.swiper-slide", state="attached", timeout=15000)
            page.wait_for_timeout(1500)

            movies = page.evaluate(f"""() => {{
                const results = [];
                const seen = new Set();
                const cards = document.querySelectorAll('.flw-item.swiper-slide');
                
                for (const card of cards) {{
                    if (results.length >= {CONFIG["MAX_MOVIES"]}) break;
                    
                    const linkEl = card.querySelector('a.film-poster-ahref');
                    if (!linkEl || !linkEl.href) continue;
                    
                    const slug = linkEl.href.split('/').pop().replace(/\\/$/, '');
                    if (!slug || seen.has(slug)) continue;  // ✅ Tránh trùng
                    seen.add(slug);
                    
                    const titleEl = card.querySelector('.tick.ltr h4, .film-name');
                    const title = titleEl ? titleEl.innerText.trim() : linkEl.title || '';
                    if (!title) continue;
                    
                    const thumbEl = card.querySelector('img[data-src], img.film-poster-img');
                    let thumb = thumbEl ? (thumbEl.dataset.src || thumbEl.src) : '';
                    if (thumb && !thumb.startsWith('http')) {{
                        thumb = 'https://yanhh3d.bz' + thumb;
                    }}
                    
                    const epEl = card.querySelector('.tick.tick-rate, .badge');
                    const badge = epEl ? epEl.innerText.trim() : '';
                    
                    results.push({{ slug, title, thumb, badge }});
                }}
                return results;
            }}""")

            logger.debug(f"Raw movies from page: {len(movies)}")
            for i, m in enumerate(movies):
                logger.debug(f"  [{i+1}] {m['slug']} - {m['title'][:40]}...")

            channels = []
            for m in movies:
                channel = {
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
                        "text": m["badge"] if m["badge"] else "Trending",
                        "position": "top-left",
                        "color": "#35ba8b",
                        "text_color": "#ffffff"
                    },
                    "remote_data": {
                        "url": f"{CONFIG['BASE_URL']}/{m['slug']}"
                    },
                    "enable_detail": False
                }
                channels.append(channel)
                logger.info(f"Added: {m['title']}")

            logger.debug(f"Total channels built: {len(channels)}")

        except Exception as e:
            logger.error(f"Scrape failed: {e}", exc_info=True)
            channels = []
        finally:
            browser.close()

    output = {
        "grid_number": 3,
        "channels": channels,
        "meta": {
            "source": CONFIG["BASE_URL"],
            "total_items": len(channels),
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "version": "1.0"
        }
    }

    # ✅ Ghi file với encoding rõ ràng + verify
    with open(CONFIG["OUTPUT_FILE"], "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    # ✅ Verify file vừa ghi
    with open(CONFIG["OUTPUT_FILE"], "r", encoding="utf-8") as f:
        verify = json.load(f)
    logger.info(f"✅ Saved {len(verify['channels'])} movies to {CONFIG['OUTPUT_FILE']}")
    
    return output

if __name__ == "__main__":
    scrape()
