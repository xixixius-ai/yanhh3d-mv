"""
scraper.py — YanHH3D → MonPlayer (Debug + Network Interception)
- Chụp DOM thực tế nếu không tìm thấy phim để debug
- Intercept network requests để bắt .m3u8/.mp4/json trực tiếp
- Force commit bằng timestamp
"""

import json
import re
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urljoin
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright, Page, Route

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = "https://yanhh3d.bz"
LIST_URL = f"{BASE_URL}/moi-cap-nhat"
OUTPUT_FILE = "monplayer.json"
DEBUG_FILE = "debug_dom.html"
MAX_PAGES = 3
DELAY = 2.0
MAX_EP_PER_FILM = 100
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

# ── Network Interceptor: Bắt stream/API trực tiếp ────────────────────────────
class StreamInterceptor:
    def __init__(self):
        self.streams = []
        self.movie_data = []

    def handle_response(self, response):
        url = response.url
        status = response.status
        try:
            if status == 200 and any(ext in url for ext in [".m3u8", ".mp4"]):
                if "ads" not in url.lower():
                    self.streams.append(url)
            elif status == 200 and "json" in response.headers.get("content-type", "").lower():
                # Thử parse JSON trả về từ Livewire/API
                text = response.text()
                if any(k in text.lower() for k in ['"slug"', '"title"', '"movies"', '"items"', '"/tap-"']):
                    self.movie_data.append(text[:1000])  # Lưu snippet để debug
        except:
            pass

# ── Step 1: Get slugs với DOM fallback ───────────────────────────────────────
def get_movie_slugs(page: Page, interceptor: StreamInterceptor, page_num: int = 1) -> List[str]:
    url = LIST_URL if page_num == 1 else f"{LIST_URL}?page={page_num}"
    log.info(f"📄 Loading {url}")
    
    page.goto(url, wait_until="networkidle", timeout=TIMEOUT)
    page.wait_for_timeout(3000)  # Đợi Livewire render
    
    # Thử nhiều selector phổ biến
    selectors = [
        ".film-list a", ".movie-list a", ".video-list a", ".list-film a",
        ".grid-film a", ".movies a", ".content a[href^='/']", 
        "main a[href^='/']", "article a[href^='/']"
    ]
    
    slugs = []
    seen = set()
    skip = {"moi-cap-nhat", "the-loai", "dang-nhap", "dang-ky", "tim-kiem", 
            "lich-phim", "tag", "actor", "vendor", "livewire", "cdn", "tap-", "sever"}
    
    for sel in selectors:
        elements = page.query_selector_all(sel)
        if not elements:
            continue
        for el in elements:
            href = el.get_attribute("href") or ""
            if not href.startswith("/"): continue
            slug = href.strip("/").split("/")[0]
            if slug in skip or slug in seen or not slug: continue
            seen.add(slug)
            slugs.append(f"/{slug}")
            if len(slugs) >= 25: break
        if slugs: break
    
    # Nếu DOM không có, lưu HTML ra file debug
    if not slugs:
        log.warning("⚠️  DOM không tìm thấy phim. Đang lưu debug_dom.html...")
        Path(DEBUG_FILE).write_text(page.content(), encoding="utf-8")
        
        # Fallback: tìm qua JS evaluation
        js_hrefs = page.evaluate("""() => {
            const links = Array.from(document.querySelectorAll('a[href]'))
                .map(a => a.href)
                .filter(h => h.startsWith(window.location.origin) && 
                             !h.includes('tap-') && !h.includes('sever') &&
                             h.split('/').length === 4); // /base/slug
            return links.map(h => h.replace(window.location.origin, ''));
        }""")
        for href in js_hrefs:
            slug = href.strip("/")
            if slug not in seen and slug not in skip:
                slugs.append(href)
                seen.add(slug)
            if len(slugs) >= 25: break
            
    log.info(f"✅ Found {len(slugs)} slugs via {'DOM' if slugs else 'JS fallback'}")
    return slugs

# ── Step 2: Scrape phim + lấy stream ─────────────────────────────────────────
def scrape_film(page: Page, slug: str, interceptor: StreamInterceptor) -> Optional[MovieItem]:
    url = BASE_URL + slug
    log.info(f"🎬 Scraping {slug}")
    
    # Reset interceptor cho từng phim
    interceptor.streams.clear()
    
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
        if el.count():
            title = el.text_content(timeout=5000) or ""
            break
    title = re.sub(r"\s*[-–|]\s*yanhh?3d.*$", "", title, flags=re.I).strip() or slug.replace("-", " ").title()
    
    # Image
    image = ""
    try:
        image = page.locator("meta[property='og:image']").first.get_attribute("content") or ""
    except: pass
    if not image:
        try: image = page.locator("img.poster, img[src*='poster']").first.get_attribute("src") or ""
        except: pass
    if image.startswith("/"): image = BASE_URL + image
    
    # Episodes
    episodes = []
    for el in page.query_selector_all("a[href*='/tap-']"):
        href = el.get_attribute("href") or ""
        if "/sever" in href.lower(): continue
        full = href if href.startswith("http") else BASE_URL + href.rstrip("/")
        label = el.text_content().strip() or f"Tập {href.split('/tap-')[-1].split('/')[0]}"
        episodes.append((label, full))
    episodes.sort(key=lambda x: int(re.search(r"(\d+)", x[0]).group(1)) if re.search(r"(\d+)", x[0]) else 999)
    
    # Extract streams
    streams = []
    if not episodes:
        # Phim lẻ: ưu tiên interceptor bắt được, fallback DOM
        if interceptor.streams:
            streams.append(Stream("Xem phim", interceptor.streams[0]))
        else:
            html = page.content()
            m = re.search(r'(https?://[^\s\'"<>]+?\.(m3u8|mp4)[^\s\'"<>]*)', html)
            if m: streams.append(Stream("Xem phim", m.group(1)))
    else:
        for ep_name, ep_url in episodes[:MAX_EP_PER_FILM]:
            page.goto(ep_url, wait_until="networkidle", timeout=TIMEOUT)
            page.wait_for_timeout(1000)
            stream_url = None
            if interceptor.streams:
                stream_url = interceptor.streams[0]
            else:
                m = re.search(r'(https?://[^\s\'"<>]+?\.(m3u8|mp4)[^\s\'"<>]*)', page.content())
                if m: stream_url = m.group(1)
            if stream_url:
                streams.append(Stream(ep_name, stream_url))
            time.sleep(0.5)
            
    if not streams:
        log.warning(f"⚠️  No stream for {title[:40]}")
        return None
        
    return MovieItem(title, image or f"{BASE_URL}/favicon.ico", "", streams)

# ── Main ────────────────────────────────────────────────────────────────────
def main():
    log.info("═" * 60)
    log.info("  🎬 YanHH3D Scraper — Debug + Network Interception")
    log.info("═" * 60)
    
    items = []
    interceptor = StreamInterceptor()
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()
        page.on("response", interceptor.handle_response)
        
        all_slugs = []
        for i in range(1, MAX_PAGES + 1):
            slugs = get_movie_slugs(page, interceptor, i)
            if not slugs: break
            all_slugs.extend(slugs)
            time.sleep(DELAY)
            
        if not all_slugs:
            log.error("❌ 0 movies found. Check debug_dom.html artifact.")
            browser.close()
            return
            
        for i, slug in enumerate(all_slugs, 1):
            log.info(f"[{i}/{len(all_slugs)}] {slug}")
            item = scrape_film(page, slug, interceptor)
            if item: items.append(item)
            time.sleep(DELAY)
        browser.close()
        
    # Output
    output = {
        "name": "YanHH3D — Hoạt Hình 3D/4K Thuyết Minh",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "items": [
            {
                "title": m.title, "image": m.image, "description": m.description,
                "streams": [{"name": s.name, "url": s.url} for s in m.streams]
            } for m in items
        ]
    }
    
    Path(OUTPUT_FILE).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"✨ Exported: {len(items)} movies, {sum(len(i['streams']) for i in output['items'])} streams")

if __name__ == "__main__":
    main()
