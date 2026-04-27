"""
scraper.py — YanHH3D → MonPlayer JSON (MINIMAL RELIABLE)
- Selector: .film-poster-ahref (đã xác nhận hoạt động)
- Crawl 20 phim mới nhất, tất cả tập
- Output chuẩn MonPlayer
"""

import json
import re
import time
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright, Page

# ── Config ──────────────────────────────────────────────────────────────────
BASE_URL = "https://yanhh3d.bz"
LIST_URL = f"{BASE_URL}/moi-cap-nhat"
OUTPUT_FILE = "monplayer.json"
MAX_MOVIES = 20
MAX_EP_PER_FILM = 999
DELAY = 1.5
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
    poster: str
    banner: str
    description: str
    streams: List[Stream] = field(default_factory=list)

# ── Helper: Extract stream URL ───────────────────────────────────────────────
def extract_stream_url(html: str) -> Optional[str]:
    for m in re.finditer(r'(https?://[^\s\'"<>]+?\.(m3u8|mp4)[^\s\'"<>]*)', html, re.I):
        url = m.group(1).strip()
        if url and 'ads' not in url.lower():
            return url
    return None

# ── Step 1: Get latest slugs ─────────────────────────────────────────────────
def get_latest_slugs(page: Page) -> List[str]:
    page.goto(LIST_URL, wait_until="networkidle", timeout=TIMEOUT)
    page.wait_for_timeout(2000)
    
    slugs = []
    seen = set()
    skip = {"moi-cap-nhat", "the-loai", "dang-nhap", "dang-ky", "tim-kiem", 
            "lich-phim", "tag", "actor", "home", "login", "vendor", "cdn"}
    
    # ✅ Selector đã xác nhận hoạt động từ screenshot
    for a in page.query_selector_all(".film-poster-ahref[href]"):
        if len(slugs) >= MAX_MOVIES:
            break
        href = a.get_attribute("href") or ""
        title = a.get_attribute("title") or ""
        
        if not href.startswith("/"):
            continue
        slug = href.strip("/")
        if "/" in slug or slug in skip or slug in seen:
            continue
        if len(title) < 3:
            continue
            
        seen.add(slug)
        slugs.append(f"/{slug}")
    
    log.info(f"✅ Found {len(slugs)} movies")
    return slugs

# ── Step 2: Scrape film ──────────────────────────────────────────────────────
def scrape_film(page: Page, slug: str) -> Optional[MovieItem]:
    url = BASE_URL + slug
    
    try:
        page.goto(url, wait_until="networkidle", timeout=TIMEOUT)
        page.wait_for_timeout(1500)
    except:
        return None
    
    # Title
    title = ""
    for sel in ["h1.film-title", "h1.title", "h1"]:
        el = page.locator(sel).first
        if el.count() > 0:
            title = el.text_content(timeout=5000) or ""
            break
    title = re.sub(r"\s*[-–|]\s*yanhh?3d.*$", "", title, flags=re.I).strip()
    if not title:
        title = slug.strip("/").replace("-", " ").title()
    
    # Poster
    poster = ""
    try:
        poster = page.locator("meta[property='og:image']").first.get_attribute("content") or ""
    except:
        pass
    if not poster:
        try:
            poster = page.locator("img.film-poster-img").first.get_attribute("data-src") or ""
            if not poster:
                poster = page.locator("img.film-poster-img").first.get_attribute("src") or ""
        except:
            pass
    if poster and poster.startswith("/"):
        poster = BASE_URL + poster
    if poster and poster.startswith("//"):
        poster = "https:" + poster
    
    banner = poster  # Fallback
    
    # Description
    desc = ""
    try:
        desc = page.locator("meta[name='description']").first.get_attribute("content") or ""
        if not desc:
            desc = page.locator(".film-content, .description").first.text_content(timeout=3000) or ""
    except:
        pass
    desc = desc.strip()[:300]
    
    # Episodes
    episodes = []
    seen_eps = set()
    for a in page.query_selector_all("a[href*='/tap-']"):
        href = a.get_attribute("href") or ""
        if "/sever" in href.lower() or "/server" in href.lower():
            continue
        full = href if href.startswith("http") else BASE_URL + href.rstrip("/")
        if full in seen_eps:
            continue
        seen_eps.add(full)
        
        label = a.text_content().strip() or f"Tập {href.split('/tap-')[-1].split('/')[0]}"
        if not re.search(r"t[ạa]p\s*\d+", label, re.I):
            m = re.search(r"(\d+)", label)
            if m:
                label = f"Tập {m.group(1)}"
        episodes.append((label, full))
    
    episodes.sort(key=lambda x: int(re.search(r"(\d+)", x[0]).group(1)) if re.search(r"(\d+)", x[0]) else 999)
    
    # Streams
    streams = []
    if not episodes:
        stream_url = extract_stream_url(page.content())
        if stream_url:
            streams.append(Stream("Xem phim", stream_url))
    else:
        for ep_name, ep_url in episodes[:MAX_EP_PER_FILM]:
            try:
                page.goto(ep_url, wait_until="networkidle", timeout=TIMEOUT)
                page.wait_for_timeout(500)
                stream_url = extract_stream_url(page.content())
                if stream_url:
                    streams.append(Stream(ep_name, stream_url))
            except:
                pass
            time.sleep(0.2)
    
    if not streams:
        return None
    
    return MovieItem(title, poster, banner, desc, streams)

# ── Main ────────────────────────────────────────────────────────────────────
def main():
    log.info("🚀 YanHH3D Scraper — Starting")
    
    items = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080}
        )
        page = context.new_page()
        
        slugs = get_latest_slugs(page)
        for slug in slugs:
            item = scrape_film(page, slug)
            if item:
                items.append(item)
            time.sleep(DELAY)
        browser.close()
    
    # Output JSON
    output = {
        "name": "YanHH3D — Hoạt Hình 3D/4K Thuyết Minh",
        "items": [
            {
                "title": m.title,
                "poster": m.poster,
                "banner": m.banner,
                "image": m.poster,
                "description": m.description,
                "streams": [{"name": s.name, "url": s.url} for s in m.streams]
            }
            for m in items
        ]
    }
    
    Path(OUTPUT_FILE).write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    
    log.info(f"✅ Done: {len(items)} movies → {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
