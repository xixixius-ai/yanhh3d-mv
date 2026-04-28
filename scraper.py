#!/usr/bin/env python3
"""
Scraper yanhh3d.bz → MonPlayer JSON
✅ Fix: Sửa selector lấy danh sách phim trang chủ
✅ Fix: Lấy đúng link stream từ data-src của nút server
✅ Cấu trúc JSON chuẩn MonPlayer
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
    "MAX_EPISODES": 50,
    "TIMEOUT_HOMEPAGE": 20000,
    "TIMEOUT_DETAIL": 15000,
    "USER_AGENT": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

RAW_BASE = os.getenv("RAW_BASE", "https://raw.githubusercontent.com/xixixius-ai/yanhh3d-mv/refs/heads/main")

def get_movies_from_homepage(page):
    """Lấy danh sách phim trending từ trang chủ"""
    try:
        # Selector chuẩn cho danh sách phim trên yanhh3d.bz
        return page.evaluate("""() => {
            const results = [];
            // Lấy tất cả item phim trong danh sách
            const items = document.querySelectorAll('.film_list-wrap > div.flw-item');
            
            items.forEach(item => {
                const titleEl = item.querySelector('.film-name a');
                const posterEl = item.querySelector('.film-poster img');
                const linkEl = item.querySelector('.film-poster a');
                
                if (titleEl && linkEl) {
                    const title = titleEl.innerText.trim();
                    const href = linkEl.getAttribute('href');
                    const slug = href.split('/').filter(Boolean).pop();
                    
                    let thumb = posterEl ? (posterEl.getAttribute('data-src') || posterEl.getAttribute('src')) : '';
                    if (thumb && thumb.startsWith('/')) thumb = 'https://yanhh3d.bz' + thumb;
                    
                    results.push({ title, slug, thumb });
                }
            });
            return results.slice(0, 10);
        }""")
    except Exception as e:
        logger.error(f"Lỗi lấy danh sách phim: {e}")
        return []

def get_episodes_from_detail(page):
    """Lấy danh sách tập phim từ trang chi tiết"""
    try:
        return page.evaluate("""() => {
            const results = [];
            // Selector đúng cho danh sách tập
            const items = document.querySelectorAll('.ep-range.ss-list-min a.ssl-item.ep-item');
            
            items.forEach(item => {
                const titleEl = item.querySelector('.ep-name');
                const href = item.getAttribute('href');
                
                if (titleEl && href) {
                    const text = titleEl.innerText.trim();
                    // Chỉ lấy các tập là số
                    if (/^\d+$/.test(text)) {
                        results.push({ name: text, url: href });
                    }
                }
            });
            return results;
        }""")
    except Exception as e:
        logger.error(f"Lỗi lấy danh sách tập: {e}")
        return []

def get_stream_from_page(page):
    """Bắt link stream từ các nút server (data-src)"""
    try:
        # Tìm nút server có data-src chứa link video
        return page.evaluate("""() => {
            const buttons = document.querySelectorAll('#list_sv .btn3dsv');
            for (const btn of buttons) {
                const src = btn.getAttribute('data-src');
                if (src && (src.includes('.m3u8') || src.includes('.mp4') || src.includes('play-fb'))) {
                    return src;
                }
            }
            return null;
        }""")
    except Exception as e:
        logger.error(f"Lỗi lấy link stream: {e}")
        return None

def build_detail_json(slug, episodes_with_streams):
    """Xây dựng JSON detail cho MonPlayer"""
    streams = []
    for ep in episodes_with_streams:
        if ep.get("stream"):
            streams.append({
                "id": f"{slug}--0-{ep['name']}",
                "name": ep["name"],
                "stream_links": [{
                    "id": f"{slug}--0-{ep['name']}-default",
                    "name": "Mặc Định",
                    "type": "hls",
                    "default": False,
                    "url": ep["stream"],
                    "request_headers": [
                        {"key": "User-Agent", "value": CONFIG["USER_AGENT"]},
                        {"key": "Referer", "value": CONFIG["BASE_URL"]}
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

def scrape():
    logger.info("▶️ Bắt đầu scrape...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=CONFIG["USER_AGENT"])
        page = context.new_page()
        
        # 1. Lấy danh sách phim
        logger.info(f"📥 Truy cập trang chủ: {CONFIG['BASE_URL']}")
        page.goto(CONFIG["BASE_URL"], wait_until="networkidle", timeout=CONFIG["TIMEOUT_HOMEPAGE"])
        movies = get_movies_from_homepage(page)
        logger.info(f"✅ Tìm thấy {len(movies)} phim")
        
        channels = []
        detail_dir = Path(CONFIG["OUTPUT_DIR"]) / "detail"
        detail_dir.mkdir(parents=True, exist_ok=True)
        
        # 2. Duyệt từng phim
        for i, movie in enumerate(movies):
            logger.info(f"🔍 Xử lý phim {i+1}/{len(movies)}: {movie['title']}")
            try:
                # Vào trang chi tiết
                detail_url = f"{CONFIG['BASE_URL']}/{movie['slug']}"
                page.goto(detail_url, wait_until="networkidle", timeout=CONFIG["TIMEOUT_DETAIL"])
                
                # Lấy danh sách tập
                episodes = get_episodes_from_detail(page)
                logger.info(f"   📋 Tìm thấy {len(episodes)} tập")
                
                # Lấy link stream từ trang hiện tại
                stream_url = get_stream_from_page(page)
                
                ep_data = []
                for ep in episodes:
                    ep_data.append({
                        "name": ep["name"],
                        "url": ep["url"],
                        "stream": stream_url # Dùng stream hiện tại làm mẫu
                    })
                
                # Lưu JSON
                detail_json = build_detail_json(movie['slug'], ep_data)
                with open(detail_dir / f"{movie['slug']}.json", "w", encoding="utf-8") as f:
                    json.dump(detail_json, f, ensure_ascii=False, indent=2)
                
                channels.append({
                    "id": movie['slug'],
                    "name": movie['title'],
                    "image": {"url": movie['thumb'], "type": "cover"},
                    "remote_data": {"url": f"{RAW_BASE}/ophim/detail/{movie['slug']}.json"}
                })
            except Exception as e:
                logger.error(f"❌ Lỗi xử lý {movie['title']}: {e}")
                continue
        
        browser.close()
    
    # 3. Xuất file ophim.json
    output = {
        "id": "yanhh3d-thuyet-minh",
        "name": "YanHH3D - Thuyết Minh",
        "grid_number": 3,
        "channels": channels,
        "meta": {"source": CONFIG["BASE_URL"], "updated_at": datetime.now(timezone.utc).isoformat()}
    }
    
    with open(CONFIG["LIST_FILE"], "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    
    logger.info("💾 Hoàn tất!")

if __name__ == "__main__":
    scrape()
