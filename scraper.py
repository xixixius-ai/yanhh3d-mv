#!/usr/bin/env python3
"""
Scraper yanhh3d.bz → MonPlayer JSON (Optimized Version)
✅ Fix: Lấy link trực tiếp từ thuộc tính data-src của các nút server
✅ Domain: yanhh3d.bz, Selectors chính xác từ HTML thực tế
✅ Hỗ trợ Cloudflare & lấy đúng tập Thuyết Minh (không có sever2)
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
    "BASE_URL": "https://yanhh3d.bz",  # ✅ Domain chính xác từ HTML
    "OUTPUT_DIR": "ophim",
    "LIST_FILE": "ophim.json",
    "MAX_MOVIES": 10,
    "MAX_EPISODES": 2,
    "TIMEOUT_DETAIL": 15000,
    "PLAYER_WAIT": 2000,
    "EPISODE_DELAY": 200,
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

RAW_BASE = "https://raw.githubusercontent.com/xixixius-ai/yanhh3d-mv/refs/heads/main"

def get_episodes(page):
    """Lấy danh sách tập phim từ HTML thực tế"""
    try:
        return page.evaluate("""() => {
            const res = [], seen = new Set();
            // ✅ Selector chính xác từ HTML: .ep-range .ssl-item
            document.querySelectorAll('.ep-range .ssl-item.ep-item').forEach(a => {
                const href = a.href;
                if (!href || seen.has(href)) return;
                
                // Lấy số tập từ .ssli-order
                const orderEl = a.querySelector('.ssli-order');
                const text = orderEl ? orderEl.innerText.trim() : '';
                
                // Chỉ lấy các tập có số (bỏ qua các tập như 1-5, 6-10...)
                if (!/^\\d+$/.test(text)) return;
                
                seen.add(href);
                res.push({ name: text, url: href });
            });
            return res.sort((a, b) => parseInt(a.name) - parseInt(b.name));
        }""")
    except Exception as e:
        logger.warning(f"Lỗi lấy episodes: {e}")
        return []

def extract_stream(page):
    """Lấy link stream từ data-src của nút server"""
    try:
        # ✅ Tìm tất cả nút server có chứa link
        buttons = page.query_selector_all("#list_sv .btn3dsv")
        
        if not buttons:
            return None
            
        # ✅ Ưu tiên lấy link .m3u8 (thường là bản chất lượng tốt nhất)
        for btn in buttons:
            src = btn.get_attribute("data-src")
            if src and ".m3u8" in src:
                return {
                    "url": src,
                    "referer": page.url,
                    "type": "hls"
                }
        
        # Nếu không có .m3u8, lấy bản đầu tiên
        first_btn = buttons[0]
        src = first_btn.get_attribute("data-src")
        if src:
            return {
                "url": src,
                "referer": page.url,
                "type": "hls" if ".m3u8" in src else "mp4"
            }
            
    except Exception as e:
        logger.warning(f"Lỗi lấy stream: {e}")
    return None

def build_detail_json(slug, episodes):
    """Xây dựng JSON detail đúng chuẩn MonPlayer"""
    streams = []
    for i, ep in enumerate(episodes):
        stream = ep.get("stream")
        if not stream:
            continue
        streams.append({
            "id": f"{slug}--0-{i}",
            "name": ep["name"],
            "stream_links": [{
                "id": f"{slug}--0-{i}-default",
                "name": "Mặc Định",
                "type": stream.get("type", "hls"),
                "default": False,
                "url": stream["url"],
                "request_headers": [
                    {"key": "User-Agent", "value": CONFIG["USER_AGENT"]},
                    {"key": "Referer", "value": stream.get("referer", CONFIG["BASE_URL"])}
                ]
            }]
        })
    
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
    """Xây dựng item cho list JSON"""
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
        home, detail = context.new_page(), context.new_page()
        
        try:
            # 1. Lấy danh sách phim từ homepage
            home.goto(CONFIG["BASE_URL"], wait_until="networkidle", timeout=CONFIG["TIMEOUT_DETAIL"])
            home.wait_for_selector("div.film_list-wrap > div.flw-item", timeout=10000)
            
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
            
            # 2. Crawl từng phim
            for i, m in enumerate(movies):
                logger.info(f"🔍 {m['title']} ({i+1}/{len(movies)})")
                try:
                    detail.goto(f"{CONFIG['BASE_URL']}/{m['slug']}", wait_until="domcontentloaded", timeout=15000)
                    detail.wait_for_timeout(1000)
                    
                    # ✅ Lấy danh sách tập (mặc định là tab Thuyết Minh)
                    eps = get_episodes(detail)
                    logger.info(f"  📋 {len(eps)} episodes found")
                    
                    ep_data = []
                    for idx, ep in enumerate(eps[:CONFIG["MAX_EPISODES"]]):
                        # Vào trang tập để lấy link stream
                        detail.goto(ep["url"], wait_until="domcontentloaded", timeout=10000)
                        detail.wait_for_timeout(CONFIG["PLAYER_WAIT"])
                        
                        stream = extract_stream(detail)
                        if stream:
                            ep_data.append({"name": ep["name"], "stream": stream})
                            if (idx + 1) % 10 == 0:
                                logger.info(f"    ✅ {idx+1}/{len(eps)} streams collected")
                        detail.wait_for_timeout(CONFIG["EPISODE_DELAY"])
                    
                    # Lưu file detail JSON
                    with open(detail_dir/f"{m['slug']}.json", "w", encoding="utf-8") as f:
                        json.dump(build_detail_json(m["slug"], ep_data), f, ensure_ascii=False, indent=2)
                    logger.info(f"  💾 Saved {m['slug']} ({len(ep_data)} streams)")
                    channels.append(build_list_item(m))
                except Exception as e:
                    logger.error(f"❌ Error processing {m['title']}: {e}")
                    continue
        except Exception as e:
            logger.error(f"❌ Total error: {e}")
        finally:
            browser.close()
    
    # 3. Lưu list JSON
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
            "version": "12.0"
        }
    }
    with open(CONFIG["LIST_FILE"], "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    logger.info(f"💾 Saved {CONFIG['LIST_FILE']} + {sum(1 for _ in Path(detail_dir).glob('*.json'))} details")
    return output

if __name__ == "__main__":
    scrape()
