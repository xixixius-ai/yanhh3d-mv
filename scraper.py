#!/usr/bin/env python3
"""
Scraper yanhh3d.bz → MonPlayer JSON
Output: 
- ophim.json (list view)
- ophim/detail/{slug}.json (detail view với episodes)
"""

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CONFIG = {
    "BASE_URL": "https://yanhh3d.bz",
    "OUTPUT_DIR": "ophim",
    "LIST_FILE": "ophim.json",
    "MAX_MOVIES": 10,
    "MAX_EPISODES": 9999,
    "TIMEOUT_HOMEPAGE": 25000,
    "TIMEOUT_DETAIL": 10000,
    "PLAYER_WAIT": 1500,
}

def get_episode_list(page):
    """Lấy danh sách tập từ trang chi tiết phim"""
    try:
        return page.evaluate("""() => {
            const episodes = [];
            const selectors = [
                '.epis_list a', '.episode-list a', '.list-ep a',
                '.episodes a', '.episode-item a', 'a.ep-item',
                'a[href*="/tap-"]', 'a[href*="/tap/"]'
            ];
            let links = [];
            for (const sel of selectors) {
                const found = document.querySelectorAll(sel);
                if (found.length > 0) { links = [...found]; break; }
            }
            if (links.length === 0) {
                links = [...document.querySelectorAll('a')].filter(a => {
                    const text = (a.innerText || '').trim();
                    const href = a.href || '';
                    return /tập\\s*\\d+|tap\\s*\\d+/i.test(text) || /\\/tap-?\\d+/i.test(href);
                });
            }
            const seen = new Set();
            links.forEach(a => {
                const href = a.href;
                const text = (a.innerText.trim() || a.title || '').replace(/\\s+/g, ' ');
                if (!href || !text || seen.has(href)) return;
                if (!/tập|tap|\\d+/i.test(text)) return;
                seen.add(href);
                episodes.push({ name: text, url: href });
            });
            episodes.sort((a, b) => {
                const na = parseInt(a.name.match(/\\d+/)?.[0] || 0);
                const nb = parseInt(b.name.match(/\\d+/)?.[0] || 0);
                return na - nb;
            });
            return episodes;
        }""")
    except Exception as e:
        logger.warning(f"Lỗi lấy danh sách tập: {e}")
        return []

def get_stream_url(page, episode_url):
    """Crawl trang tập để lấy link .m3u8 + referer"""
    collected = []
    
    def on_response(response):
        url = response.url
        if response.status == 200 and ".m3u8" in url and ("fbcdn" in url or "cdn" in url):
            collected.append({
                "url": url,
                "referer": response.request.headers.get("referer") or ""
            })
    
    page.on("response", on_response)
    
    try:
        def block_unnecessary(route):
            if route.request.resource_type in ["image", "stylesheet", "font", "media"]:
                route.abort()
            else:
                route.continue_()
        page.route("**/*", block_unnecessary)
        
        page.goto(episode_url, wait_until="domcontentloaded", timeout=CONFIG["TIMEOUT_DETAIL"])
        page.wait_for_timeout(CONFIG["PLAYER_WAIT"])

        if not collected:
            for selector in ["video[src]", "video source[src]"]:
                try:
                    el = page.locator(selector).first
                    if el.count() > 0:
                        src = el.get_attribute("src")
                        if src and ".m3u8" in src:
                            collected.append({"url": src, "referer": ""})
                            break
                except: pass
                
    except Exception as e:
        logger.warning(f"Không lấy được stream cho {episode_url}: {e}")
    finally:
        page.remove_listener("response", on_response)
        page.route("**/*", lambda route: route.continue_())
        
    return collected[0] if collected else None

def build_detail_json(slug: str, episodes: list) -> dict:
    """Xây dựng JSON detail view đúng schema MonPlayer"""
    streams = []
    for i, ep in enumerate(episodes):
        ep_num = re.search(r'\d+', ep["name"])
        ep_id = f"{slug}--0-{i}"
        stream_item = {
            "id": ep_id,
            "name": ep_num.group(0) if ep_num else str(i + 1),
            "stream_links": [{
                "id": f"{ep_id}-default",
                "name": "Mặc Định",
                "type": "hls",
                "default": False,
                "url": ep["stream"]["url"],
                "request_headers": [
                    {"key": "User-Agent", "value": "MonPlayer"},
                    {"key": "Referer", "value": ep["stream"]["referer"] or "https://yanhh3d.bz"}
                ]
            }]
        }
        streams.append(stream_item)
    
    return {
        "sources": [{
            "id": f"{slug}--0",
            "name": "Vietsub #1",
            "contents": [{
                "id": f"{slug}--0",
                "name": "",
                "grid_number": 3,
                "streams": streams
            }]
        }],
        "subtitle": "Vietsub"
    }

def build_list_item(movie: dict) -> dict:
    """Xây dựng item cho list view"""
    return {
        "id": movie["slug"],
        "name": movie["title"],
        "description": "",
        "image": {
            "url": movie["thumb"],
            "type": "cover",
            "width": 480,
            "height": 640
        },
        "type": "playlist",
        "display": "text-below",
        "label": {
            "text": movie["badge"] or "Trending",
            "position": "top-left",
            "color": "#35ba8b",
            "text_color": "#ffffff"
        },
        "remote_data": {
            "url": f"detail/{movie['slug']}.json"  # ✅ Dùng đường dẫn tương đối
        },
        "enable_detail": True
    }

def scrape():
    logger.info(f"▶️ Bắt đầu scrape: {CONFIG['BASE_URL']}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720}
        )
        home_page = context.new_page()
        detail_page = context.new_page()

        for pg in [home_page, detail_page]:
            pg.set_extra_http_headers({"Accept-Language": "vi-VN,vi;q=0.9", "Referer": "https://www.google.com/"})
            pg.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")

        try:
            # 1️⃣ Lấy trending từ homepage
            home_page.goto(CONFIG["BASE_URL"], wait_until="networkidle", timeout=CONFIG["TIMEOUT_HOMEPAGE"])
            home_page.wait_for_selector(".flw-item.swiper-slide", state="attached", timeout=10000)
            home_page.wait_for_timeout(1000)

            movies = home_page.evaluate(f"""() => {{
                const res = [], seen = new Set();
                document.querySelectorAll('.flw-item.swiper-slide').forEach(card => {{
                    if (res.length >= {CONFIG["MAX_MOVIES"]}) return;
                    const a = card.querySelector('a.film-poster-ahref');
                    if (!a?.href) return;
                    const slug = a.href.split('/').pop().replace(/\\/$/, '');
                    if (seen.has(slug)) return;
                    seen.add(slug);
                    const title = card.querySelector('.tick.ltr h4, .film-name')?.innerText.trim() || a.title || '';
                    if (!title) return;
                    let thumb = card.querySelector('img[data-src], img.film-poster-img')?.dataset.src || '';
                    if (thumb && !thumb.startsWith('http')) thumb = 'https://yanhh3d.bz' + thumb;
                    const badge = card.querySelector('.tick.tick-rate, .badge')?.innerText.trim() || '';
                    res.push({{ slug, title, thumb, badge }});
                }});
                return res;
            }}""")
            logger.info(f"✅ Tìm thấy {len(movies)} phim trending")

            # 2️⃣ Xử lý từng phim
            channels = []
            detail_dir = Path(CONFIG["OUTPUT_DIR"]) / "detail"
            detail_dir.mkdir(parents=True, exist_ok=True)
            
            for i, m in enumerate(movies):
                logger.info(f"🔍 Xử lý: {m['title']} ({i+1}/{len(movies)})")
                try:
                    detail_page.goto(f"{CONFIG['BASE_URL']}/{m['slug']}", wait_until="domcontentloaded", timeout=15000)
                    detail_page.wait_for_timeout(1500)

                    ep_list = get_episode_list(detail_page)[:CONFIG["MAX_EPISODES"]]
                    ep_data = []
                    
                    for idx, ep in enumerate(ep_list):
                        stream = get_stream_url(detail_page, ep["url"])
                        if stream:
                            ep_data.append({"name": ep["name"], "stream": stream})
                            if (idx + 1) % 20 == 0:
                                logger.info(f"    Progress: {idx + 1}/{len(ep_list)} tập")
                        detail_page.wait_for_timeout(250)

                    # Xuất detail JSON
                    detail_json = build_detail_json(m["slug"], ep_data)
                    detail_path = detail_dir / f"{m['slug']}.json"
                    with open(detail_path, "w", encoding="utf-8") as f:
                        json.dump(detail_json, f, ensure_ascii=False, indent=2)
                    logger.info(f"  💾 Detail: {detail_path} ({len(ep_data)} tập)")

                    channels.append(build_list_item(m))

                except Exception as e:
                    logger.error(f"❌ Lỗi phim {m['title']}: {e}")
                    continue

        except Exception as e:
            logger.error(f"❌ Lỗi tổng: {e}", exc_info=True)
        finally:
            browser.close()

    # 3️⃣ Xuất list JSON
    list_output = {
        "grid_number": 3,
        "channels": channels,
        "meta": {
            "source": CONFIG["BASE_URL"],
            "total_items": len(channels),
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "version": "4.1"
        }
    }
    
    list_path = Path(CONFIG["LIST_FILE"])
    with open(list_path, "w", encoding="utf-8") as f:
        json.dump(list_output, f, ensure_ascii=False, indent=2)
    
    total_eps = sum(1 for _ in Path(detail_dir).glob("*.json"))
    logger.info(f"💾 Đã lưu: {list_path} + {total_eps} detail files")
    return list_output

if __name__ == "__main__":
    scrape()
