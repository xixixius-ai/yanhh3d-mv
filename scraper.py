"""
scraper.py — YanHH3D → MonPlayer JSON (Playwright version)
Xử lý site dùng Livewire/JS dynamic bằng headless browser
"""

import json
import re
import time
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright, Page, BrowserContext

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = "https://yanhh3d.bz"
LIST_URL = f"{BASE_URL}/moi-cap-nhat"
OUTPUT_FILE = "monplayer.json"
MAX_PAGES = 3
DELAY = 2.0
MAX_EP_PER_FILM = 100
TIMEOUT = 30000  # 30s

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

def extract_stream_from_html(html: str, page_url: str) -> Optional[str]:
    """Extract .m3u8/.mp4 từ HTML đã render JS"""
    # 1. Direct URLs
    for match in re.finditer(r'(https?://[^\s\'"<>]+?\.(m3u8|mp4)[^\s\'"<>]*)', html, re.I):
        url = match.group(1).strip()
        if url and not any(x in url.lower() for x in ["googlesyndication", "doubleclick", "ads."]):
            return url
    # 2. JS patterns: src="...", file: "..."
    for pattern in [
        r'(?:src|file|source|url)\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)[^"\']*)["\']',
        r'sources\s*:\s*\[\s*\{[^}]*?file\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)[^"\']*)["\']',
    ]:
        for match in re.finditer(pattern, html, re.I | re.S):
            url = match.group(1).strip()
            if url and not url.startswith('http'):
                url = urljoin(page_url, url)
            if '.m3u8' in url.lower() or '.mp4' in url.lower():
                return url
    return None

def get_movie_slugs_playwright(page: Page, page_num: int = 1) -> List[str]:
    """Crawl listing page với JS rendering"""
    url = LIST_URL if page_num == 1 else f"{LIST_URL}?page={page_num}"
    log.info(f"📄 Loading {url}")
    
    page.goto(url, wait_until="networkidle", timeout=TIMEOUT)
    page.wait_for_timeout(2000)  # Đợi Livewire render xong
    
    slugs = []
    seen = set()
    
    # Tìm tất cả link, lọc theo pattern slug phim
    for a in page.query_selector_all("a[href]"):
        href = a.get_attribute("href") or ""
        if not href.startswith("/") or href.startswith("//"):
            continue
        if any(x in href.lower() for x in ["#", "?", "javascript", ".css", ".js", ".png", "vendor", "livewire", "cdn", "tap-", "sever"]):
            continue
        
        slug = href.rstrip("/")
        parts = slug.strip("/").split("/")
        if len(parts) != 1:
            continue
        
        skip = {"moi-cap-nhat", "the-loai", "dang-nhap", "dang-ky", "tim-kiem", "lich-phim", "tag", "actor", ""}
        if parts[0] in skip:
            continue
        
        text = a.text_content().strip()
        if len(text) < 3:
            continue
        
        if slug not in seen:
            seen.add(slug)
            slugs.append(slug)
            if len(slugs) >= 25:  # Giới hạn để tránh quá tải
                break
    
    log.info(f"✅ Found {len(slugs)} movies on page {page_num}")
    return slugs

def scrape_film_playwright(page: Page, slug: str) -> Optional[MovieItem]:
    """Crawl 1 phim với JS rendering"""
    film_url = BASE_URL + slug
    log.info(f"🎬 Scraping {slug}")
    
    page.goto(film_url, wait_until="networkidle", timeout=TIMEOUT)
    page.wait_for_timeout(1500)
    
    html = page.content()
    
    # Title
    title = page.locator("h1").first.text_content(timeout=5000) or slug.replace("-", " ").title()
    title = re.sub(r"\s*[-–|]\s*yanhh?3d.*$", "", title, flags=re.I).strip()
    
    # Image
    image = ""
    og_img = page.locator("meta[property='og:image']").first
    if og_img.count() > 0:
        image = og_img.get_attribute("content") or ""
    if not image:
        poster = page.locator(".film-poster img, .poster img, img[src*='poster']").first
        if poster.count() > 0:
            image = poster.get_attribute("src") or poster.get_attribute("data-src") or ""
    if image and image.startswith("/"):
        image = BASE_URL + image
    
    # Description
    desc = ""
    desc_meta = page.locator("meta[name='description']").first
    if desc_meta.count() > 0:
        desc = desc_meta.get_attribute("content") or ""
    
    # Episodes
    episodes = []
    for a in page.query_selector_all("a[href]"):
        href = a.get_attribute("href") or ""
        if "/tap-" not in href.lower():
            continue
        full_url = href if href.startswith("http") else BASE_URL + href.rstrip("/")
        if any(x in full_url.lower() for x in ["/sever", "/server", "?server="]):
            continue
        label = a.text_content().strip() or f"Tập {href.split('/tap-')[-1].split('/')[0]}"
        if not re.search(r"t[ạa]p\s*\d+", label, re.I):
            m = re.search(r"(\d+)", label)
            if m:
                label = f"Tập {m.group(1)}"
        episodes.append((label, full_url))
    
    episodes.sort(key=lambda x: int(re.search(r"(\d+)", x[0]).group(1)) if re.search(r"(\d+)", x[0]) else 999)
    episodes = episodes[:MAX_EP_PER_FILM]
    
    # Extract streams
    streams = []
    if not episodes:
        # Phim lẻ
        url = extract_stream_from_html(html, film_url)
        if url:
            streams.append(Stream(name="Xem phim", url=url))
            log.info(f"✅ [lẻ] {title[:40]}")
    else:
        # Phim bộ
        for ep_name, ep_url in episodes:
            page.goto(ep_url, wait_until="networkidle", timeout=TIMEOUT)
            page.wait_for_timeout(1000)
            ep_html = page.content()
            url = extract_stream_from_html(ep_html, ep_url)
            if url:
                streams.append(Stream(name=ep_name, url=url))
        if streams:
            log.info(f"✅ [bộ] {title[:40]} — {len(streams)} tập")
    
    if not streams:
        log.warning(f"⚠️  No stream for {title[:40]}")
        return None
    
    return MovieItem(title=title, image=image or "https://yanhh3d.bz/favicon.ico", description=desc[:200], streams=streams)

def main():
    log.info("═" * 60)
    log.info("  🎬 YanHH3D Scraper — Playwright Edition")
    log.info("═" * 60)
    
    items = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
        )
        page = context.new_page()
        
        # Crawl listing
        all_slugs = []
        for p_num in range(1, MAX_PAGES + 1):
            slugs = get_movie_slugs_playwright(page, p_num)
            if not slugs:
                break
            all_slugs.extend(slugs)
            time.sleep(DELAY)
        
        if not all_slugs:
            log.error("❌ No movies found!")
            browser.close()
            return
        
        log.info(f"\n🎯 Total: {len(all_slugs)} movies — crawling details...\n")
        
        # Crawl each movie
        for i, slug in enumerate(all_slugs, 1):
            log.info(f"[{i:3d}/{len(all_slugs)}] {slug}")
            film = scrape_film_playwright(page, slug)
            if film:
                items.append(film)
            time.sleep(DELAY)
        
        browser.close()
    
    # Export JSON
    output = {
        "name": "YanHH3D — Hoạt Hình 3D/4K Thuyết Minh",
        "items": [
            {
                "title": f.title,
                "image": f.image,
                "description": f.description,
                "streams": [{"name": s.name, "url": s.url} for s in f.streams],
            }
            for f in items
        ],
    }
    
    Path(OUTPUT_FILE).write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    
    log.info(f"✨ Done: {len(output['items'])} movies → {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
