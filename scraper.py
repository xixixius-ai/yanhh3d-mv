"""
scraper.py — YanHH3D → MonPlayer (20 PHIM MỚI NHẤT)
✅ Chỉ crawl trang "Mới cập nhật" (page 1)
✅ Dừng ngay khi đủ 20 phim hợp lệ
✅ Output gọn, có trường category để app filter
✅ Tốc độ nhanh (~3-5 phút)
"""

import json
import re
import time
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone

from playwright.sync_api import sync_playwright, Page

# ── Config ──────────────────────────────────────────────────────────────────
BASE_URL = os.getenv("YANHH_BASE_URL", "https://yanhh3d.bz")
LIST_URL = f"{BASE_URL}/moi-cap-nhat"
OUTPUT_FILE = "monplayer.json"
MAX_MOVIES = int(os.getenv("MAX_MOVIES", "20"))   # CHỈ 20 PHIM
MAX_PAGES = 1                                     # Chỉ trang mới nhất
MAX_EP_PER_FILM = int(os.getenv("MAX_EP_PER_FILM", "500"))
DELAY = float(os.getenv("SCRAPER_DELAY", "1.0"))
TIMEOUT = 25000

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

@dataclass
class Stream:
    name: str
    url: str

@dataclass
class MovieItem:
    title: str
    poster: str
    banner: str
    description: str
    year: Optional[int]
    status: str  # ongoing | completed
    category: str
    streams: List[Stream] = field(default_factory=list)

# ── Helper: Process Image URL ────────────────────────────────────────────────
def process_image_url(url: str, target_width: int = 300, target_height: int = 450) -> str:
    if not url or url == "null":
        return f"{BASE_URL}/favicon.ico"
    if url.startswith("//"): url = "https:" + url
    elif url.startswith("/"): url = BASE_URL + url
    # Nếu CDN hỗ trợ resize (tùy site), giữ nguyên nếu không
    return url

# ── Helper: Extract Stream URL ───────────────────────────────────────────────
def extract_stream_url(html: str, page_url: str) -> Optional[str]:
    for m in re.finditer(r'(https?://[^\s\'"<>\\|{}]+?\.(m3u8|mp4)(?:[?&][^\s\'"<>#|{}]+)?)', html, re.I):
        url = m.group(1).strip().replace('\\', '').replace('"', '').replace("'", "")
        if url and url.startswith('http') and 'ads' not in url.lower():
            return url
    return None

# ── Step 1: Get latest slugs (STOP AT 20) ────────────────────────────────────
def get_latest_slugs(page: Page) -> List[str]:
    log.info(f"📥 Loading {LIST_URL} (Target: {MAX_MOVIES} movies)")
    page.goto(LIST_URL, wait_until="networkidle", timeout=TIMEOUT)
    page.wait_for_timeout(1500)
    
    slugs = []
    seen = set()
    skip = {"moi-cap-nhat", "the-loai", "dang-nhap", "dang-ky", "tim-kiem", 
            "lich-phim", "tag", "actor", "country", "home", "login", "vendor"}
    
    for a in page.query_selector_all(".film-poster-ahref[href]"):
        if len(slugs) >= MAX_MOVIES: break  # ✅ DỪNG NGAY KHI ĐỦ 20
        
        href = a.get_attribute("href") or ""
        title = a.get_attribute("title") or ""
        if not href.startswith("/") or "/" in href.strip("/"): continue
        
        slug = href.strip("/")
        if slug in skip or slug in seen or len(title) < 3: continue
        
        seen.add(slug)
        slugs.append(f"/{slug}")
        
    log.info(f"✅ Found {len(slugs)} latest movies")
    return slugs

# ── Step 2: Scrape Film Metadata + All Episodes ──────────────────────────────
def scrape_film(page: Page, slug: str) -> Optional[MovieItem]:
    url = BASE_URL + slug
    log.info(f"🎬 Scraping: {slug}")
    
    try:
        page.goto(url, wait_until="networkidle", timeout=TIMEOUT)
        page.wait_for_timeout(1500)
    except Exception as e:
        log.error(f"❌ Failed {slug}: {e}")
        return None
    
    # Title & Year
    raw_title = ""
    for sel in ["h1.film-title", "h1.title", "h1"]:
        el = page.locator(sel).first
        if el.count() > 0:
            raw_title = el.text_content(timeout=5000) or ""
            break
    title = re.sub(r"\s*[-–|]\s*yanhh?3d.*$", "", raw_title, flags=re.I).strip()
    if not title: title = slug.replace("-", " ").title()
    
    year_match = re.search(r'\b(19|20)\d{2}\b', raw_title)
    year = int(year_match.group(0)) if year_match else None
    
    # Status
    status = "ongoing"
    try:
        status_text = page.locator(".film-status, .status, .badge, .completed").first.text_content(timeout=3000) or ""
        if any(x in status_text.lower() for x in ["hoàn thành", "completed", "full", "end", "tập cuối"]):
            status = "completed"
    except: pass
    
    # Images
    poster = ""
    try: poster = page.locator("meta[property='og:image']").first.get_attribute("content") or ""
    except: pass
    if not poster:
        try: poster = page.locator("img.film-poster-img").first.get_attribute("data-src") or page.locator("img.film-poster-img").first.get_attribute("src") or ""
        except: pass
    poster = process_image_url(poster, 300, 450)
    banner = poster  # Fallback banner

    # Description
    desc = ""
    try:
        desc = page.locator("meta[name='description']").first.get_attribute("content") or ""
        if not desc: desc = page.locator(".film-content, .description").first.text_content(timeout=3000) or ""
    except: pass
    desc = desc.strip()[:300]

    # Episodes & Streams
    episodes = []
    seen_eps = set()
    for a in page.query_selector_all("a[href*='/tap-']"):
        href = a.get_attribute("href") or ""
        if any(x in href.lower() for x in ["/sever2", "/server2"]): continue
        full = href if href.startswith("http") else BASE_URL + href.rstrip("/")
        if full in seen_eps: continue
        seen_eps.add(full)
        
        label = a.text_content().strip() or f"Tập {href.split('/tap-')[-1].split('/')[0]}"
        if not re.search(r"t[ạa]p\s*\d+", label, re.I):
            m = re.search(r"(\d+)", label)
            if m: label = f"Tập {m.group(1)}"
        episodes.append((label, full))
    
    # Sort numerically: Tập 1, 2, 3, ..., 10, 11...
    episodes.sort(key=lambda x: int(re.search(r"(\d+)", x[0]).group(1)) if re.search(r"(\d+)", x[0]) else 999)
    
    streams = []
    if not episodes:
        url_stream = extract_stream_url(page.content(), url)
        if url_stream: streams.append(Stream("Xem phim", url_stream))
    else:
        for ep_name, ep_url in episodes[:MAX_EP_PER_FILM]:
            try:
                page.goto(ep_url, wait_until="networkidle", timeout=TIMEOUT)
                page.wait_for_timeout(500)
                url_stream = extract_stream_url(page.content(), ep_url)
                if url_stream: streams.append(Stream(ep_name, url_stream))
            except: pass
            time.sleep(0.15)  # Tốc độ cao hơn vì chỉ crawl 20 phim
    
    if not streams:
        log.warning(f"⚠️ Skip {title[:30]} (No streams)")
        return None
        
    log.info(f"✅ {title[:30]} — {len(streams)} eps collected")
    return MovieItem(title, poster, banner, desc, year, status, "Mới cập nhật", streams)

# ── Main ────────────────────────────────────────────────────────────────────
def main():
    log.info("═" * 50)
    log.info(f"  🚀 YanHH3D → Top {MAX_MOVIES} Mới Nhất")
    log.info("═" * 50)
    
    items = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()
        
        slugs = get_latest_slugs(page)
        for i, slug in enumerate(slugs, 1):
            item = scrape_film(page, slug)
            if item: items.append(item)
            time.sleep(DELAY)
        browser.close()
        
    # Build JSON
    output: Dict[str, Any] = {
        "name": "YanHH3D — Top 20 Mới Nhất",
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "items": [
            {
                "title": m.title,
                "poster": m.poster,
                "banner": m.banner,
                "image": m.poster,  # Backward compat
                "description": m.description,
                "year": m.year,
                "status": m.status,
                "category": m.category,
                "total_episodes": len(m.streams),
                "streams": [{"name": s.name, "url": s.url} for s in m.streams]
            }
            for m in items
        ]
    }
    
    Path(OUTPUT_FILE).write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    
    log.info(f"\n✨ Done: {len(items)}/{MAX_MOVIES} movies → {OUTPUT_FILE}")
    log.info(f"📅 Updated: {output['updated_at']}")

if __name__ == "__main__":
    main()
