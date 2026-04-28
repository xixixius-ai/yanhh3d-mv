#!/usr/bin/env python3
"""
Scraper yanhh3d.bz → MonPlayer JSON
Lấy 10 phim trending + crawl chi tiết từng phim để lấy tổng tập & link tập.
"""

import json
import logging
import re
from datetime import datetime, timezone
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CONFIG = {
    "BASE_URL": "https://yanhh3d.bz",
    "OUTPUT_FILE": "ophim.json",
    "MAX_MOVIES": 10,
    "TIMEOUT_HOMEPAGE": 30000,
    "TIMEOUT_DETAIL": 15000,
}

def extract_episodes(page, slug):
    """Crawl trang chi tiết phim để lấy danh sách tập"""
    url = f"{CONFIG['BASE_URL']}/{slug}"
    try:
        page.goto(url, wait_until="networkidle", timeout=CONFIG["TIMEOUT_DETAIL"])
        # Đợi container tập phim load (thay selector nếu site đổi cấu trúc)
        page.wait_for_selector(".epis_list a, .episode-list a, .server-list .item a", state="attached", timeout=5000)
        
        episodes = page.evaluate("""() => {
            const eps = [];
            const selectors = ['.epis_list a', '.episode-list a', '.server-list .item a', '.episodes a'];
            let links = [];
            for (const sel of selectors) {
                const els = document.querySelectorAll(sel);
                if (els.length > 0) { links = [...els]; break; }
            }
            if (links.length === 0) {
                links = [...document.querySelectorAll('a[href*="/tap"], a[href*="/tập"], a[href*="episode"]')];
            }
            links.forEach(a => {
                const href = a.href || '';
                const text = (a.innerText.trim() || a.getAttribute('title') || '').replace(/\s+/g, ' ');
                if (text && href) eps.push({ name: text, link: href });
            });
            // Lọc trùng link
            const seen = new Set();
            return eps.filter(e => !seen.has(e.link) && seen.add(e.link));
        }""")
        
        # Sắp xếp tự nhiên theo số tập (Tập 1, Tập 2, Tập 10...)
        def sort_key(ep):
            match = re.search(r'(\d+)', ep["name"])
            return int(match.group(1)) if match else 0
        episodes.sort(key=sort_key)
        
        return episodes
    except Exception as e:
        logger.warning(f"️ Lỗi crawl tập cho {slug}: {e}")
        return []

def scrape():
    logger.info(f"Bắt đầu scrape homepage: {CONFIG['BASE_URL']}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        detail_page = context.new_page()  # Trang riêng để crawl chi tiết

        # Stealth setup
        page.set_extra_http_headers({"Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8", "Referer": "https://www.google.com/"})
        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        detail_page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

        try:
            # 1️⃣ Lấy danh sách trending từ homepage
            page.goto(CONFIG["BASE_URL"], wait_until="networkidle", timeout=CONFIG["TIMEOUT_HOMEPAGE"])
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
                    if (!slug || seen.has(slug)) continue;
                    seen.add(slug);
                    const titleEl = card.querySelector('.tick.ltr h4, .film-name');
                    const title = titleEl ? titleEl.innerText.trim() : linkEl.title || '';
                    if (!title) continue;
                    const thumbEl = card.querySelector('img[data-src], img.film-poster-img');
                    let thumb = thumbEl ? (thumbEl.dataset.src || thumbEl.src) : '';
                    if (thumb && !thumb.startsWith('http')) thumb = 'https://yanhh3d.bz' + thumb;
                    const epEl = card.querySelector('.tick.tick-rate, .badge');
                    results.push({{ slug, title, thumb, badge: epEl ? epEl.innerText.trim() : '' }});
                }}
                return results;
            }}""")
            logger.info(f"✅ Tìm thấy {len(movies)} phim trending")

            # 2️ Crawl chi tiết từng phim
            channels = []
            for i, m in enumerate(movies):
                logger.info(f"🔍 Đang crawl chi tiết: {m['title']} ({i+1}/{len(movies)})")
                episodes = extract_episodes(detail_page, m["slug"])
                detail_page.wait_for_timeout(800)  # Giảm tải server
                
                channels.append({
                    "id": m["slug"],
                    "name": m["title"],
                    "description": "",
                    "image": {"url": m["thumb"], "type": "cover", "width": 480, "height": 640},
                    "type": "playlist",
                    "display": "text-below",
                    "label": {"text": m["badge"] or "Trending", "position": "top-left", "color": "#35ba8b", "text_color": "#ffffff"},
                    "remote_data": {"url": f"{CONFIG['BASE_URL']}/{m['slug']}"},
                    "enable_detail": False,
                    "total_episodes": len(episodes),
                    "episodes": episodes  # ✅ Danh sách tập: [{name, link}, ...]
                })

        except Exception as e:
            logger.error(f"❌ Lỗi scrape: {e}", exc_info=True)
            channels = []
        finally:
            browser.close()

    # 3️ Xuất JSON
    output = {
        "grid_number": 3,
        "channels": channels,
        "meta": {
            "source": CONFIG["BASE_URL"],
            "total_items": len(channels),
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "version": "2.0"
        }
    }
    with open(CONFIG["OUTPUT_FILE"], "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info(f"💾 Đã lưu {len(channels)} phim vào {CONFIG['OUTPUT_FILE']}")
    return output

if __name__ == "__main__":
    scrape()
