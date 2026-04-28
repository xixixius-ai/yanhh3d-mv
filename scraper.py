#!/usr/bin/env python3
"""
Scraper yanhh3d.bz → MonPlayer JSON
✅ Fix: Dùng raw.githubusercontent.com URLs
✅ Fix: Chỉ bắt stream Thuyết Minh (loại Vietsub)
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
    "MAX_MOVIES": 3,
    "MAX_EPISODES": 3,
    "TIMEOUT_HOMEPAGE": 20000,
    "TIMEOUT_DETAIL": 15000,
    "PLAYER_WAIT": 2000,  # ✅ Tăng thời gian chờ player
    "EPISODE_DELAY": 200,
}

# ✅ Fix: Dùng raw.githubusercontent.com
RAW_BASE = "https://raw.githubusercontent.com/xixixius-ai/yanhh3d-mv/refs/heads/main"

def get_thuyet_minh_episodes(page):
    try:
        # Click "Xem Thuyết Minh" và chờ đủ lâu để server switch
        try:
            btn = page.locator("text=Xem Thuyết Minh").first
            if btn.count() > 0 and btn.is_visible():
                btn.click(timeout=3000)
                page.wait_for_timeout(1500)  # ✅ Chờ lâu hơn để load server2
        except: pass

        episodes = page.evaluate("""() => {
            const results = [], seen = new Set();
            // Chỉ lấy links từ server2 (Thuyết Minh)
            document.querySelectorAll('a[href*="/sever2/"][href*="/tap-"]').forEach(a => {
                const href = a.href;
                if (!href || seen.has(href)) return;
                const epName = a.querySelector('.ep-name, .ssli-order');
                let text = epName ? epName.innerText.trim() : a.innerText.trim();
                if (!text) text = a.getAttribute('data-jp') || a.title || '';
                text = text.trim();
                if (!/^\\d+$/.test(text)) return;
                seen.add(href);
                results.push({ name: text, url: href });
            });
            results.sort((a, b) => parseInt(a.name) - parseInt(b.name));
            return results;
        }""")
        return episodes
    except Exception as e:
        logger.warning(f"Lỗi lấy episodes: {e}")
        return []

def get_stream_url(page, episode_url, episode_name):
    """Chỉ bắt stream từ Thuyết Minh server (sever2)"""
    collected = []
    
    def on_response(response):
        url = response.url.lower()
        # ✅ Chỉ bắt .m3u8 từ CDN + có trong request từ sever2
        if response.status == 200 and ".m3u8" in url:
            if any(cd in url for cd in ["fbcdn", "opstream", "streamtape", "cdn", "video", "media"]):
                # ✅ Kiểm tra referer/request URL có chứa sever2 (Thuyết Minh)
                req_url = response.request.url.lower()
                if "sever2" in req_url or "sever2" in episode_url.lower():
                    collected.append({
                        "url": response.url,
                        "referer": episode_url  # ✅ Dùng episode URL làm referer
                    })
    
    page.on("response", on_response)
    try:
        page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "font"] else route.continue_())
        
        # ✅ Load trang tập với wait_until networkidle để đảm bảo JS chạy xong
        page.goto(episode_url, wait_until="networkidle", timeout=CONFIG["TIMEOUT_DETAIL"])
        page.wait_for_timeout(CONFIG["PLAYER_WAIT"])
        
        # ✅ Fallback: tìm trong video element nếu network không bắt được
        if not collected:
            try:
                video = page.locator("video").first
                if video.count() > 0:
                    src = video.get_attribute("src")
                    if src and ".m3u8" in src:
                        collected.append({"url": src, "referer": episode_url})
            except: pass
            try:
                source = page.locator("video source").first
                if source.count() > 0:
                    src = source.get_attribute("src")
                    if src and ".m3u8" in src:
                        collected.append({"url": src, "referer": episode_url})
            except: pass
                
    except Exception as e:
        logger.debug(f"Stream error {episode_name}: {e}")
    finally:
        page.remove_listener("response", on_response)
        page.route("**/*", lambda route: route.continue_())
    
    return collected[0] if collected else None

def build_detail_json(slug, episodes):
    streams_list = []
    for i, ep in enumerate(episodes):
        stream_item = {
            "id": f"{slug}--0-{i}",
            "name": ep["name"],
            "stream_links": [{
                "id": f"{slug}--0-{i}-default",
                "name": "Mặc Định",
                "type": "hls",
                "default": False,
                "url": ep["stream"]["url"],
                "request_headers": [
                    {"key": "User-Agent", "value": "MonPlayer"},
                    {"key": "Referer", "value": ep["stream"]["referer"] or CONFIG["BASE_URL"]}
                ]
            }]
        }
        streams_list.append(stream_item)
    
    return {
        "sources": [{
            "id": f"{slug}--0",
            "name": "Thuyết Minh #1",
            "contents": [{
                "id": f"{slug}--0",
                "name": "",
                "grid_number": 3,
                "streams": streams_list
            }]
        }],
        "subtitle": "Thuyết Minh"
    }

def build_list_item(movie):
    # ✅ Fix: Dùng raw.githubusercontent.com URL
    detail_url = f"{RAW_BASE}/ophim/detail/{movie['slug']}.json"
    return {
        "id": movie["slug"],
        "name": movie["title"],
        "description": "",
        "image": {"url": movie["thumb"], "type": "cover", "width": 480, "height": 640},
        "type": "playlist",
        "display": "text-below",
        "label": {"text": movie["badge"] or "Trending", "position": "top-left", "color": "#35ba8b", "text_color": "#ffffff"},
        "remote_data": {"url": detail_url},
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
            home_page.goto(CONFIG["BASE_URL"], wait_until="networkidle", timeout=CONFIG["TIMEOUT_HOMEPAGE"])
            home_page.wait_for_selector(".flw-item.swiper-slide", state="attached", timeout=8000)
            home_page.wait_for_timeout(500)
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
            channels = []
            detail_dir = Path(CONFIG["OUTPUT_DIR"]) / "detail"
            detail_dir.mkdir(parents=True, exist_ok=True)
            for i, m in enumerate(movies):
                logger.info(f"🔍 Xử lý: {m['title']} ({i+1}/{len(movies)})")
                try:
                    detail_page.goto(f"{CONFIG['BASE_URL']}/{m['slug']}", wait_until="domcontentloaded", timeout=10000)
                    detail_page.wait_for_timeout(1000)
                    ep_list = get_thuyet_minh_episodes(detail_page)
                    logger.info(f"  📋 Tìm thấy {len(ep_list)} episodes Thuyết Minh")
                    ep_data = []
                    total_to_crawl = min(len(ep_list), CONFIG["MAX_EPISODES"])
                    for idx, ep in enumerate(ep_list[:total_to_crawl]):
                        stream = get_stream_url(detail_page, ep["url"], ep["name"])
                        if stream:
                            ep_data.append({"name": ep["name"], "stream": stream})
                            if (idx + 1) % 25 == 0:
                                logger.info(f"    ✅ Progress: {idx + 1}/{total_to_crawl}")
                        detail_page.wait_for_timeout(CONFIG["EPISODE_DELAY"])
                    detail_json = build_detail_json(m["slug"], ep_data)
                    detail_path = detail_dir / f"{m['slug']}.json"
                    with open(detail_path, "w", encoding="utf-8") as f:
                        json.dump(detail_json, f, ensure_ascii=False, indent=2)
                    logger.info(f"  💾 Detail: {detail_path} ({len(ep_data)}/{len(ep_list)} tập)")
                    channels.append(build_list_item(m))
                except Exception as e:
                    logger.error(f"❌ Lỗi phim {m['title']}: {e}", exc_info=True)
                    continue
        except Exception as e:
            logger.error(f"❌ Lỗi tổng: {e}", exc_info=True)
        finally:
            browser.close()
    list_output = {
        "id": "yanhh3d-thuyet-minh",
        "name": "YanHH3D - Thuyết Minh",
        "url": f"{RAW_BASE}/ophim",
        "color": "#004444",
        "image": {"url": "https://yanhh3d.bz/static/img/logo.png", "type": "cover"},
        "description": "Phim thuyết minh chất lượng cao từ YanHH3D.bz",
        "grid_number": 3,
        "channels": channels,
        "sorts": [{"text": "Mới nhất", "type": "radio", "url": f"{RAW_BASE}/ophim"}],
        "meta": {
            "source": CONFIG["BASE_URL"],
            "total_items": len(channels),
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "version": "7.2"
        }
    }
    with open(CONFIG["LIST_FILE"], "w", encoding="utf-8") as f:
        json.dump(list_output, f, ensure_ascii=False, indent=2)
    total_eps = sum(1 for _ in Path(detail_dir).glob("*.json"))
    logger.info(f"💾 Đã lưu: {CONFIG['LIST_FILE']} + {total_eps} detail files")
    return list_output

if __name__ == "__main__":
    scrape()
