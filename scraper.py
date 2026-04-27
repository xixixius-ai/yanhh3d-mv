"""
scraper.py — YanHH3D → MonPlayer (HTML DUMP DEBUG MODE)
- Lưu HTML listing page ra file để debug cấu trúc link
- In ra tất cả href tìm được để phân tích pattern
- Slug detection permissive: bắt mọi link có vẻ là phim
"""

import json
import re
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright, Page

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = "https://yanhh3d.bz"
LIST_URL = f"{BASE_URL}/moi-cap-nhat"
OUTPUT_FILE = "monplayer.json"
DEBUG_HTML_FILE = "debug_listing.html"
MAX_PAGES = 1  # Chỉ test 1 trang
DELAY = 2.0
TIMEOUT = 30000

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

@dataclass
class Stream:
    name: str
    url: str

@dataclass
class MovieItem:
    title: str
    image: str
    description: str = ""
    streams: List[Stream] = field(default_factory=list)

# ── Step 1: Get slugs với DEBUG DUMP ─────────────────────────────────────────
def get_movie_slugs(page: Page, page_num: int = 1) -> List[str]:
    url = LIST_URL if page_num == 1 else f"{LIST_URL}?page={page_num}"
    log.info(f"📄 Loading {url}")
    
    page.goto(url, wait_until="networkidle", timeout=TIMEOUT)
    page.wait_for_timeout(5000)  # Đợi 5 giây cho Livewire render
    
    # 🔥 QUAN TRỌNG: Lưu toàn bộ HTML ra file để debug
    html = page.content()
    Path(DEBUG_HTML_FILE).write_text(html, encoding="utf-8")
    log.info(f"💾 Saved full HTML to {DEBUG_HTML_FILE}")
    
    # 🔥 In ra TẤT CẢ href tìm được để phân tích pattern
    log.info("🔍 ALL hrefs found on page (first 50):")
    all_hrefs = []
    for a in page.query_selector_all("a[href]"):
        href = a.get_attribute("href") or ""
        text = a.text_content().strip()[:30]
        if href.startswith("/") and not href.startswith("//"):
            all_hrefs.append((href, text))
            if len(all_hrefs) <= 50:
                log.info(f"   • {href:45s} → '{text}'")
    
    # 🔥 Tìm pattern có vẻ là slug phim
    log.info("\n🎯 Potential movie slugs (permissive filter):")
    slugs = []
    seen = set()
    
    # Từ khóa cần LOẠI (utility pages)
    SKIP = {
        "moi-cap-nhat", "the-loai", "dang-nhap", "dang-ky", "lien-he",
        "tim-kiem", "lich-phim", "tag", "actor", "country", "year",
        "phim-le", "phim-bo", "danh-sach", "home", "login", "register",
        "vendor", "livewire", "cdn", "storage", "uploads", "ajax",
    }
    
    for href, text in all_hrefs:
        # Chuẩn hóa slug
        slug = href.strip("/").split("/")[0]
        if not slug or slug in SKIP or slug in seen:
            continue
        if slug.startswith("page") or slug.isdigit():
            continue
            
        # Permissive: chấp nhận slug có hoặc không có dash
        # Chỉ cần text hợp lý (3-60 ký tự, có chữ)
        if len(text) < 3 or len(text) > 60:
            continue
        if not re.search(r"[a-zA-Z\u00C0-\u017F\u0102\u0110\u01A0\u01AF]", text):
            continue
            
        seen.add(slug)
        slugs.append(f"/{slug}")
        log.info(f"   ✅ Candidate: /{slug:35s} → '{text}'")
    
    log.info(f"\n📊 Total candidate slugs: {len(slugs)}")
    
    # 🔥 Nếu vẫn 0 slug, in thêm info để debug
    if not slugs:
        log.warning("⚠️  Still 0 slugs! Checking for common patterns...")
        # Tìm link có chứa "tap-"
        tap_links = [h for h, t in all_hrefs if "tap-" in h.lower()]
        if tap_links:
            log.info(f"   Found {len(tap_links)} episode links (pattern: /slug/tap-N)")
            log.info(f"   Sample: {tap_links[:3]}")
            log.info("   → Site may use nested routes: /{slug}/tap-{N}")
        # Tìm link có image/poster
        poster_links = [h for h, t in all_hrefs if "poster" in t.lower() or "thumb" in t.lower()]
        if poster_links:
            log.info(f"   Found {len(poster_links)} links with poster/thumb text")
    
    return slugs[:25]  # Giới hạn để test nhanh

# ── Step 2: Scrape film (giữ nguyên, chỉ thêm log) ───────────────────────────
def extract_stream_url(html: str, page_url: str) -> Optional[str]:
    # Aggressive regex để bắt mọi .m3u8/.mp4
    for m in re.finditer(r'(https?://[^\s\'"<>\\|{}]+?\.(m3u8|mp4)(?:[?&][^\s\'"<>#|{}]+)?)', html, re.I):
        url = m.group(1).strip().replace('\\', '').replace('"', '').replace("'", "")
        if url and url.startswith('http') and 'ads' not in url.lower():
            return url
    return None

def scrape_film(page: Page, slug: str) -> Optional[MovieItem]:
    url = BASE_URL + slug
    log.info(f"🎬 Scraping {slug}")
    
    try:
        page.goto(url, wait_until="networkidle", timeout=TIMEOUT)
        page.wait_for_timeout(2000)
    except Exception as e:
        log.error(f"❌ Failed {slug}: {e}")
        return None
    
    # Title
    title = ""
    for sel in ["h1.film-title", "h1.title", "h1"]:
        el = page.locator(sel).first
        if el.count() > 0:
            title = el.text_content(timeout=5000) or ""
            break
    title = re.sub(r"\s*[-–|]\s*yanhh?3d.*$", "", title, flags=re.I).strip() or slug.strip("/").replace("-", " ").title()
    
    # Image
    image = ""
    try:
        image = page.locator("meta[property='og:image']").first.get_attribute("content") or ""
    except: pass
    if not image:
        try: image = page.locator("img.poster, img[src*='poster']").first.get_attribute("src") or ""
        except: pass
    if image and image.startswith("/"): image = BASE_URL + image
    
    # Episodes: tìm link /tap-N
    episodes = []
    for a in page.query_selector_all("a[href*='/tap-']"):
        href = a.get_attribute("href") or ""
        if "/sever" in href.lower(): continue
        full = href if href.startswith("http") else BASE_URL + href.rstrip("/")
        label = a.text_content().strip() or f"Tập {href.split('/tap-')[-1].split('/')[0]}"
        if not re.search(r"t[ạa]p\s*\d+", label, re.I):
            m = re.search(r"(\d+)", label)
            if m: label = f"Tập {m.group(1)}"
        episodes.append((label, full))
    
    episodes.sort(key=lambda x: int(re.search(r"(\d+)", x[0]).group(1)) if re.search(r"(\d+)", x[0]) else 999)
    log.info(f"  🔍 Found {len(episodes)} episode links")
    
    # Streams
    streams = []
    if not episodes:
        url = extract_stream_url(page.content(), url)
        if url:
            streams.append(Stream("Xem phim", url))
            log.info(f"  ✅ [lẻ] Found stream")
    else:
        for ep_name, ep_url in episodes[:3]:  # Test 3 tập đầu
            try:
                page.goto(ep_url, wait_until="networkidle", timeout=TIMEOUT)
                page.wait_for_timeout(800)
                url = extract_stream_url(page.content(), ep_url)
                if url:
                    streams.append(Stream(ep_name, url))
                    log.info(f"  ✅ {ep_name}: {url[:70]}...")
            except: pass
            time.sleep(0.5)
    
    if not streams:
        return None
    return MovieItem(title=title, image=image or f"{BASE_URL}/favicon.ico", description="", streams=streams)

# ── Main ────────────────────────────────────────────────────────────────────
def main():
    log.info("═" * 60)
    log.info("  🎬 YanHH3D Scraper — HTML DUMP DEBUG MODE")
    log.info("═" * 60)
    
    items = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()
        
        all_slugs = []
        for i in range(1, MAX_PAGES + 1):
            slugs = get_movie_slugs(page, i)
            if not slugs:
                log.info("⚠️  No slugs found — check debug_listing.html")
                break
            all_slugs.extend(slugs)
            time.sleep(DELAY)
        
        if not all_slugs:
            log.error("❌ Cannot proceed without slugs. Please check debug_listing.html")
            browser.close()
            return
        
        for i, slug in enumerate(all_slugs, 1):
            log.info(f"[{i}/{len(all_slugs)}] {slug}")
            item = scrape_film(page, slug)
            if item:
                items.append(item)
                log.info(f"  🎉 Added: {item.title}")
                break  # Test 1 phim thôi
            time.sleep(DELAY)
        browser.close()
        
    # Output
    output = {
        "name": "YanHH3D — Hoạt Hình 3D/4K Thuyết Minh",
        "items": [
            {
                "title": m.title,
                "image": m.image,
                "description": m.description,
                "streams": [{"name": s.name, "url": s.url} for s in m.streams]
            } for m in items
        ]
    }
    
    Path(OUTPUT_FILE).write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    
    log.info(f"✨ Exported: {len(items)} movies → {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
