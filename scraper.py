#!/usr/bin/env python3
"""
Scraper yanhh3d.net → MonPlayer JSON (DEBUG VERSION)
✅ Thêm: Click nút Play, chờ lâu hơn, lưu HTML để debug
"""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

CONFIG = {
    "BASE_URL": "https://yanhh3d.net", 
    "OUTPUT_DIR": "ophim",
    "LIST_FILE": "ophim.json",
    "MAX_MOVIES": 5,         # Giảm xuống 5 để test nhanh
    "MAX_EPISODES": 5,       # Chỉ lấy 5 tập đầu để test
    "TIMEOUT_DETAIL": 20000, # Tăng timeout
    "PLAYER_WAIT": 5000,     # Chờ 5 giây cho player load
}

RAW_BASE = "https://raw.githubusercontent.com/xixixius-ai/yanhh3d-mv/refs/heads/main"

def extract_streams_debug(page, ep_url):
    """Hàm lấy stream có debug kỹ lưỡng"""
    collected = []
    
    def on_response(response):
        url = response.url.lower()
        if response.status == 200 and (".m3u8" in url or ".mp4" in url):
            if any(cd in url for cd in ["fbcdn", "opstream", "streamtape", "cdn", "video", "media", "drive"]):
                if not collected:
                    collected.append({"url": response.url, "referer": ep_url, "type": "hls" if ".m3u8" in url else "mp4"})
                    logger.info(f"🎯 BẮT ĐƯỢC STREAM: {response.url[:50]}...")

    page.on("response", on_response)
    try:
        logger.info(f"🌐 Đang tải trang tập: {ep_url}")
        page.goto(ep_url, wait_until="networkidle", timeout=CONFIG["TIMEOUT_DETAIL"])
        
        # ✅ Cố gắng click nút Play để kích hoạt stream
        try:
            play_btn = page.locator("text=Play", "text=▶", ".play-btn", ".jw-icon-display").first
            if play_btn.count() > 0 and play_btn.is_visible():
                logger.info(" Đang click nút Play...")
                play_btn.click(timeout=3000)
        except: pass
        
        # ✅ Chờ player load
        logger.info(f"⏳ Chờ {CONFIG['PLAYER_WAIT']/1000}s để player load...")
        page.wait_for_timeout(CONFIG["PLAYER_WAIT"])

        # ✅ Kiểm tra Iframe
        if not collected:
            frames = page.frames
            for frame in frames:
                video = frame.locator("video").first
                if video.count() > 0:
                    src = video.get_attribute("src")
                    if src:
                        collected.append({"url": src, "referer": ep_url, "type": "hls" if ".m3u8" in src else "mp4"})
                        logger.info(f"🎯 Bắt được từ Iframe: {src[:50]}...")

        # ✅ Kiểm tra tag video trực tiếp
        if not collected:
            video = page.locator("video").first
            if video.count() > 0:
                src = video.get_attribute("src")
                source_tag = page.locator("video source").first.get_attribute("src")
                final_src = src or source_tag
                if final_src and (".m3u8" in final_src or ".mp4" in final_src):
                    collected.append({"url": final_src, "referer": ep_url, "type": "hls" if ".m3u8" in final_src else "mp4"})
                    logger.info(f"🎯 Bắt được từ video tag: {final_src[:50]}...")

    except Exception as e:
        logger.error(f"❌ Lỗi khi tải trang: {e}")
    finally:
        page.remove_listener("response", on_response)

    # 🚨 DEBUG: Nếu không lấy được, lưu HTML lại để phân tích
    if not collected:
        with open("debug_episode.html", "w", encoding="utf-8") as f:
            f.write(page.content())
        logger.warning("⚠️ KHÔNG TÌM THẤY STREAM. Đã lưu file 'debug_episode.html'. Vui lòng gửi file này để mình fix.")
    
    return collected[0] if collected else None

# ... (Các hàm khác giữ nguyên như cũ: get_episodes, build_detail_json, build_list_item) ...

def get_episodes(page):
    try:
        return page.evaluate("""() => {
            const res = [], seen = new Set();
            document.querySelectorAll('div.film_list-wrap > div.flw-item a[href*="/tap-"]').forEach(a => {
                const href = a.href;
                if (!href || seen.has(href)) return;
                const epName = a.querySelector('.ep-name, .ssli-order');
                let text = epName ? epName.innerText.trim() : a.innerText.trim();
                if (!text) text = a.getAttribute('data-jp') || a.title || '';
                text = text.trim();
                if (!/^\d+$/.test(text)) return;
                seen.add(href);
                res.push({ name: text, url: href });
            });
            return res.sort((a, b) => parseInt(a.name) - parseInt(b.name));
        }""")
    except Exception as e:
        logger.warning(f"Lỗi lấy episodes: {e}")
        return []

def build_detail_json(slug, episodes):
    streams = []
    for i, ep in enumerate(episodes):
        stream = ep.get("stream")
        if not stream: continue
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
                    {"key": "User-Agent", "value": "Mozilla/5.0"},
                    {"key": "Referer", "value": stream.get("referer", "")}
                ]
            }]
        })
    return {
        "sources": [{"id": f"{slug}--0", "name": "Thuyết Minh #1", "contents": [{"id": f"{slug}--0", "name": "", "grid_number": 3, "streams": streams}]}],
        "subtitle": "Thuyết Minh"
    }

def build_list_item(movie):
    return {
        "id": movie["slug"], "name": movie["title"], "description": "",
        "image": {"url": movie["thumb"], "type": "cover", "width": 480, "height": 640},
        "type": "playlist", "display": "text-below",
        "label": {"text": movie["badge"] or "Trending", "position": "top-left", "color": "#35ba8b", "text_color": "#ffffff"},
        "remote_data": {"url": f"{RAW_BASE}/ophim/detail/{movie['slug']}.json"},
        "enable_detail": True
    }

def scrape():
    logger.info(f"▶️ Start: {CONFIG['BASE_URL']}")
    channels = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36", viewport={"width": 1280, "height": 720})
        home, detail = context.new_page(), context.new_page()
        
        try:
            home.goto(CONFIG["BASE_URL"], wait_until="networkidle", timeout=20000)
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
                    let thumb = card.querySelector('div.film-poster > img')?.getAttribute('data-src') || '';
                    if (thumb && !thumb.startsWith('http')) thumb = '{CONFIG["BASE_URL"]}' + thumb;
                    res.push({{slug, title: a.innerText.trim(), thumb, badge: ''}});
                }});
                return res;
            }}""")
            logger.info(f"✅ Found {len(movies)} movies")
            
            detail_dir = Path(CONFIG["OUTPUT_DIR"]) / "detail"
            detail_dir.mkdir(parents=True, exist_ok=True)
            
            for i, m in enumerate(movies):
                logger.info(f"🔍 {m['title']} ({i+1}/{len(movies)})")
                try:
                    detail.goto(f"{CONFIG['BASE_URL']}/{m['slug']}", wait_until="domcontentloaded", timeout=15000)
                    detail.wait_for_timeout(1000)
                    
                    # Click Thuyết Minh
                    try:
                        btn = detail.locator("text=Xem Thuyết Minh").first
                        if btn.count() > 0: btn.click(timeout=3000); detail.wait_for_timeout(1500)
                    except: pass
                    
                    eps = get_episodes(detail)
                    ep_data = []
                    for idx, ep in enumerate(eps[:CONFIG["MAX_EPISODES"]]):
                        stream = extract_streams_debug(detail, ep["url"])
                        if stream:
                            ep_data.append({"name": ep["name"], "stream": stream})
                        detail.wait_for_timeout(1000) # Nghỉ 1s giữa các tập
                    
                    with open(detail_dir/f"{m['slug']}.json", "w", encoding="utf-8") as f:
                        json.dump(build_detail_json(m["slug"], ep_data), f, ensure_ascii=False, indent=2)
                    logger.info(f"💾 Saved {m['slug']} ({len(ep_data)} streams)")
                    channels.append(build_list_item(m))
                except Exception as e:
                    logger.error(f"❌ Error: {e}")
                    continue
        finally:
            browser.close()
    
    # Save list
    output = {"id": "yanhh3d-tm", "name": "YanHH3D", "url": f"{RAW_BASE}/ophim", "channels": channels, "grid_number": 3}
    with open(CONFIG["LIST_FILE"], "w", encoding="utf-8") as f: json.dump(output, f, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    scrape()
