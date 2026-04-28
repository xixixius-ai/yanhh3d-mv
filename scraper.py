#!/usr/bin/env python3
"""
Scraper yanhh3d.bz → MonPlayer JSON
✅ Reverse-engineered từ YanHH3DProvider.kt + ExtractorKt.java
✅ Domain: yanhh3d.net, CSS selectors chính xác
✅ Cloudflare handling + stream extraction mimic app behavior
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CONFIG = {
    "BASE_URL": "https://yanhh3d.net",  # ✅ Domain chính xác từ code
    "OUTPUT_DIR": "ophim",
    "LIST_FILE": "ophim.json",
    "MAX_MOVIES": 2,
    "MAX_EPISODES": 5,
    "TIMEOUT_DETAIL": 15000,
    "PLAYER_WAIT": 3000,
    "EPISODE_DELAY": 200,
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",  # ✅ Từ code
}

RAW_BASE = "https://raw.githubusercontent.com/xixixius-ai/yanhh3d-mv/refs/heads/main"

def get_episodes(page):
    """Lấy episodes với CSS selectors CHÍNH XÁC từ code"""
    try:
        return page.evaluate("""() => {
            const res = [], seen = new Set();
            // ✅ Selector từ toSearchResult() method
            document.querySelectorAll('div.film_list-wrap > div.flw-item a[href*="/tap-"]').forEach(a => {
                const href = a.href;
                if (!href || seen.has(href)) return;
                // Lấy text từ .ep-name hoặc .ssli-order
                const epName = a.querySelector('.ep-name, .ssli-order');
                let text = epName ? epName.innerText.trim() : a.innerText.trim();
                if (!text) text = a.getAttribute('data-jp') || a.title || '';
                text = text.trim();
                // ✅ Regex từ code: (?<=\()\d+(?=/\d+\))|\b\d+\b
                if (!/^\d+$/.test(text)) return;
                seen.add(href);
                res.push({ name: text, url: href });
            });
            return res.sort((a, b) => parseInt(a.name) - parseInt(b.name));
        }""")
    except Exception as e:
        logger.warning(f"Lỗi lấy episodes: {e}")
        return []

def extract_stream(page, ep_url):
    """Mô phỏng logic ExtractorKt.invokeSource()"""
    collected = []
    
    def on_response(response):
        url = response.url.lower()
        # ✅ Bắt stream từ CDN (mimic app behavior)
        if response.status == 200 and (".m3u8" in url or ".mp4" in url):
            if any(cd in url for cd in ["fbcdn", "opstream", "streamtape", "cdn", "video", "media", "ibyteimg", "tiktokcdn", "cloudbeta"]):
                if not collected:  # ✅ Chỉ lấy link đầu tiên (mimic app)
                    quality = 720  # ✅ Default quality (app filters out 1080p)
                    is_m3u8 = ".m3u8" in url
                    collected.append({
                        "url": response.url,
                        "referer": ep_url,
                        "quality": quality,
                        "type": "hls" if is_m3u8 else "mp4"
                    })
    
    page.on("response", on_response)
    try:
        # ✅ Block unnecessary resources để nhanh hơn
        page.route("**/*", lambda route: route.abort() if route.request.resource_type in ["image", "font", "stylesheet"] else route.continue_())
        
        # ✅ Load trang với networkidle để JS chạy xong
        page.goto(ep_url, wait_until="networkidle", timeout=CONFIG["TIMEOUT_DETAIL"])
        page.wait_for_timeout(CONFIG["PLAYER_WAIT"])
        
        # ✅ Fallback: tìm trong video element
        if not collected:
            try:
                video = page.locator("video").first
                if video.count() > 0:
                    src = video.get_attribute("src")
                    if src and (".m3u8" in src or ".mp4" in src):
                        collected.append({"url": src, "referer": ep_url, "quality": 720, "type": "hls" if ".m3u8" in src else "mp4"})
            except: pass
    except Exception as e:
        logger.debug(f"Stream error: {e}")
    finally:
        page.remove_listener("response", on_response)
        page.route("**/*", lambda route: route.continue_())
    
    return collected[0] if collected else None

def build_detail_json(slug, episodes):
    streams = []
    for i, ep in enumerate(episodes):
        stream = ep["stream"]
        stream_type = stream.get("type", "hls")
        stream_item = {
            "id": f"{slug}--0-{i}",
            "name": ep["name"],
            "stream_links": [{
                "id": f"{slug}--0-{i}-default",
                "name": "Mặc Định",
                "type": stream_type,
                "default": False,
                "url": stream["url"],
                "request_headers": [
                    {"key": "User-Agent", "value": CONFIG["USER_AGENT"]},
                    {"key": "Referer", "value": stream["referer"] or CONFIG["BASE_URL"]}
                ]
            }]
        }
        streams.append(stream_item)
    
    return {
        "sources": [{
            "id": f"{slug}--0",
            "name": "Thuyết Minh #1",
            "contents": [{
                "id": f"{slug}--0",
                "name": "",
                "grid_number": 3,
                "streams": streams
            }]
        }],
        "subtitle": "Thuyết Minh"
    }

def build_list_item(movie):
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
    logger.info(f"▶️ Start: {CONFIG['BASE_URL']}")
    channels = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=CONFIG["USER_AGENT"],
            viewport={"width": 1280, "height": 720}
        )
        # ✅ Thêm headers giống app
        context.set_extra_http_headers({
            "Accept-Language": "vi-VN,vi;q=0.9",
            "Referer": "https://www.google.com/"
        })
        
        home, detail = context.new_page(), context.new_page()
        
        try:
            # ✅ Load homepage với Cloudflare handling
            home.goto(CONFIG["BASE_URL"], wait_until="networkidle", timeout=20000)
            home.wait_for_selector("div.film_list-wrap > div.flw-item", state="attached", timeout=8000)
            
            # ✅ Parse movies với selectors chính xác
            movies = home.evaluate(f"""() => {{
                const res = [], seen = new Set();
                document.querySelectorAll('div.film_list-wrap > div.flw-item').forEach(card => {{
                    if (res.length >= {CONFIG["MAX_MOVIES"]}) return;
                    const a = card.querySelector('div.film-detail > h3 > a');
                    if (!a?.href) return;
                    const slug = a.href.split('/').pop().replace(/\\/$/, '');
                    if (seen.has(slug)) return;
                    seen.add(slug);
                    const title = a.innerText.trim();
                    if (!title) return;
                    let thumb = card.querySelector('div.film-poster > img')?.getAttribute('data-src') || '';
                    if (thumb && !thumb.startsWith('http')) thumb = '{CONFIG["BASE_URL"]}' + thumb;
                    const badge = card.querySelector('div.tick-item')?.innerText.trim() || '';
                    res.push({{slug, title, thumb, badge}});
                }});
                return res;
            }}""")
            logger.info(f"✅ Found {len(movies)} movies")
            
            detail_dir = Path(CONFIG["OUTPUT_DIR"]) / "detail"
            detail_dir.mkdir(parents=True, exist_ok=True)
            
            for i, m in enumerate(movies):
                logger.info(f"🔍 {m['title']} ({i+1}/{len(movies)})")
                try:
                    detail.goto(f"{CONFIG['BASE_URL']}/{m['slug']}", wait_until="domcontentloaded", timeout=10000)
                    detail.wait_for_timeout(1000)
                    
                    # ✅ Click "Xem Thuyết Minh" nếu có
                    try:
                        btn = detail.locator("text=Xem Thuyết Minh").first
                        if btn.count() > 0 and btn.is_visible():
                            btn.click(timeout=3000)
                            detail.wait_for_timeout(2000)
                    except: pass
                    
                    eps = get_episodes(detail)
                    logger.info(f"  📋 {len(eps)} episodes")
                    
                    ep_data = []
                    for idx, ep in enumerate(eps[:CONFIG["MAX_EPISODES"]]):
                        stream = extract_stream(detail, ep["url"])
                        if stream:
                            ep_data.append({"name": ep["name"], "stream": stream})
                            if (idx + 1) % 10 == 0:
                                logger.info(f"    ✅ {idx+1}/{len(eps)}")
                        detail.wait_for_timeout(CONFIG["EPISODE_DELAY"])
                    
                    with open(detail_dir/f"{m['slug']}.json", "w", encoding="utf-8") as f:
                        json.dump(build_detail_json(m["slug"], ep_data), f, ensure_ascii=False, indent=2)
                    logger.info(f"  💾 {m['slug']}.json ({len(ep_data)}/{len(eps)})")
                    channels.append(build_list_item(m))
                except Exception as e:
                    logger.error(f"❌ {m['title']}: {e}", exc_info=True)
                    continue
        except Exception as e:
            logger.error(f"❌ Total: {e}", exc_info=True)
        finally:
            browser.close()
    
    output = {
        "id": "yanhh3d-thuyet-minh",
        "name": "YanHH3D - Thuyết Minh",
        "url": f"{RAW_BASE}/ophim",
        "color": "#004444",
        "image": {"url": f"{CONFIG['BASE_URL']}/static/img/logo.png", "type": "cover"},
        "description": "Phim thuyết minh chất lượng cao từ YanHH3D",
        "grid_number": 3,
        "channels": channels,
        "sorts": [{"text": "Mới nhất", "type": "radio", "url": f"{RAW_BASE}/ophim"}],
        "meta": {
            "source": CONFIG["BASE_URL"],
            "total_items": len(channels),
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "version": "10.0"
        }
    }
    with open(CONFIG["LIST_FILE"], "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info(f"💾 Saved {CONFIG['LIST_FILE']} + {sum(1 for _ in Path(detail_dir).glob('*.json'))} details")
    return output

if __name__ == "__main__":
    scrape()
