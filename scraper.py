"""
scraper.py — YanHH3D → MonPlayer (STRICT COMPATIBLE VERSION)
- Title: không dấu /, giữ tiếng Việt có dấu
- Streams: 1 URL / tập, không trùng
- JSON: chỉ có field MonPlayer chấp nhận (name, items)
- Sort items ổn định để diff hoạt động đúng
"""

import json
import re
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urljoin, unquote

from playwright.sync_api import sync_playwright, Page

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = "https://yanhh3d.bz"
LIST_URL = f"{BASE_URL}/moi-cap-nhat"
OUTPUT_FILE = "monplayer.json"
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

# ── Helper: Clean title cho MonPlayer ────────────────────────────────────────
def clean_title(raw: str, slug: str) -> str:
    """Convert slug hoặc raw title → title chuẩn cho MonPlayer"""
    if not raw or raw.startswith("/"):
        # Từ slug: /ten-phim-phan-2 → "Ten Phim Phần 2"
        title = slug.strip("/").replace("-", " ").title()
        # Fix tiếng Việt cơ bản (nếu site không trả Unicode)
        replacements = {
            "Thuyet Minh": "Thuyết Minh", "Phan": "Phần", "Tap": "Tập",
            "Dau Pha": "Đấu Phá", "Thuong Khung": "Thương Khung",
            "Tien Nghich": "Tiên Nghịch", "Kiem Lai": "Kiếm Lai"
        }
        for old, new in replacements.items():
            title = title.replace(old, new)
        return title.strip()
    # Nếu có raw title, clean dấu / và suffix
    title = re.sub(r"\s*[-–|]\s*yanhh?3d.*$", "", raw, flags=re.I).strip()
    return title if title else slug.strip("/").replace("-", " ").title()

# ── Helper: Extract stream URL ───────────────────────────────────────────────
def extract_stream_url(html: str, page_url: str) -> Optional[str]:
    # 1. Direct .m3u8/.mp4 (ưu tiên fbcdn.cloud)
    for m in re.finditer(r'(https?://[^\s\'"<>]+?\.(m3u8|mp4)[^\s\'"<>]*)', html, re.I):
        url = m.group(1).strip()
        if url and 'ads' not in url.lower() and 'google' not in url.lower():
            return url
    # 2. JS patterns
    for pattern in [
        r'(?:file|src|source|url)\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)[^"\']*)["\']',
        r'sources\s*:\s*\[\s*\{[^}]*?file\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)[^"\']*)["\']',
    ]:
        for m in re.finditer(pattern, html, re.I | re.S):
            url = m.group(1).strip()
            if not url.startswith('http'):
                url = urljoin(page_url, url)
            if '.m3u8' in url.lower() or '.mp4' in url.lower():
                return url
    return None

# ── Step 1: Get VALID movie slugs only ───────────────────────────────────────
def get_movie_slugs(page: Page, page_num: int = 1) -> List[str]:
    url = LIST_URL if page_num == 1 else f"{LIST_URL}?page={page_num}"
    log.info(f"📄 Loading {url}")
    
    page.goto(url, wait_until="networkidle", timeout=TIMEOUT)
    page.wait_for_timeout(2000)
    
    slugs = []
    seen = set()
    SKIP = {
        "moi-cap-nhat", "the-loai", "dang-nhap", "dang-ky", "lien-he",
        "tim-kiem", "lich-phim", "tag", "actor", "country", "year",
        "phim-le", "phim-bo", "danh-sach", "home", "login", "register",
        "hoat-hinh-3d", "hoat-hinh-2d", "hoat-hinh-4k", "hoan-thanh", 
        "dang-chieu", "vendor", "livewire", "cdn",
    }
    
    for a in page.query_selector_all("a[href]"):
        href = a.get_attribute("href") or ""
        if not href.startswith("/"): continue
        slug = href.strip("/")
        if "/" in slug: continue  # Bỏ /slug/tap-1
        if slug in SKIP or slug in seen: continue
        if "-" not in slug: continue  # Tên phim thật có dash
        text = a.text_content().strip()
        if len(text) < 3 or len(text) > 60: continue
        seen.add(slug)
        slugs.append(f"/{slug}")
        if len(slugs) >= 25: break
    
    log.info(f"✅ Page {page_num}: {len(slugs)} valid slugs")
    return slugs

# ── Step 2: Scrape film ──────────────────────────────────────────────────────
def scrape_film(page: Page, slug: str) -> Optional[MovieItem]:
    url = BASE_URL + slug
    log.info(f"🎬 Scraping {slug}")
    
    try:
        page.goto(url, wait_until="networkidle", timeout=TIMEOUT)
        page.wait_for_timeout(1500)
    except Exception as e:
        log.error(f"❌ Failed {slug}: {e}")
        return None
    
    # Title: dùng clean_title()
    raw_title = ""
    for sel in ["h1.film-title", "h1.title", "h1"]:
        el = page.locator(sel).first
        if el.count() > 0:
            raw_title = el.text_content(timeout=5000) or ""
            break
    title = clean_title(raw_title, slug)
    
    # Image
    image = ""
    try:
        image = page.locator("meta[property='og:image']").first.get_attribute("content") or ""
    except: pass
    if not image:
        try: image = page.locator("img.poster, img[src*='poster']").first.get_attribute("src") or ""
        except: pass
    if image and image.startswith("/"): image = BASE_URL + image
    
    # Episodes: lấy đầy đủ, tránh trùng
    episodes = []
    seen_eps = set()
    for a in page.query_selector_all("a[href*='/tap-']"):
        href = a.get_attribute("href") or ""
        if "/sever" in href.lower(): continue
        full = href if href.startswith("http") else BASE_URL + href.rstrip("/")
        if full in seen_eps: continue
        seen_eps.add(full)
        label = a.text_content().strip() or f"Tập {href.split('/tap-')[-1].split('/')[0]}"
        if not re.search(r"t[ạa]p\s*\d+", label, re.I):
            m = re.search(r"(\d+)", label)
            if m: label = f"Tập {m.group(1)}"
        episodes.append((label, full))
    
    episodes.sort(key=lambda x: int(re.search(r"(\d+)", x[0]).group(1)) if re.search(r"(\d+)", x[0]) else 999)
    
    # Streams: 1 URL / tập, không duplicate
    streams = []
    seen_urls = set()
    
    if not episodes:
        # Phim lẻ
        url = extract_stream_url(page.content(), url)
        if url and url not in seen_urls:
            streams.append(Stream("Xem phim", url))
            seen_urls.add(url)
    else:
        # Phim bộ
        for ep_name, ep_url in episodes[:MAX_EP_PER_FILM]:
            try:
                page.goto(ep_url, wait_until="networkidle", timeout=TIMEOUT)
                page.wait_for_timeout(800)
                url = extract_stream_url(page.content(), ep_url)
                if url and url not in seen_urls:
                    streams.append(Stream(ep_name, url))
                    seen_urls.add(url)
            except:
                pass
            time.sleep(0.3)
    
    if not streams:
        return None
        
    return MovieItem(title=title, image=image or f"{BASE_URL}/favicon.ico", description="", streams=streams)

# ── Main ────────────────────────────────────────────────────────────────────
def main():
    log.info("═" * 60)
    log.info("  🎬 YanHH3D Scraper — MonPlayer Strict Compatible")
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
            if not slugs: break
            all_slugs.extend(slugs)
            time.sleep(DELAY)
            
        for i, slug in enumerate(all_slugs, 1):
            log.info(f"[{i}/{len(all_slugs)}] {slug}")
            item = scrape_film(page, slug)
            if item: items.append(item)
            time.sleep(DELAY)
        browser.close()
        
    # Output JSON — CHỈ field MonPlayer chấp nhận
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
    
    # Sort để diff ổn định + app dễ đọc
    output["items"].sort(key=lambda x: x["title"])
    
    Path(OUTPUT_FILE).write_text(
        json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8"
    )
    
    log.info(f"✨ Exported: {len(items)} movies → {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
