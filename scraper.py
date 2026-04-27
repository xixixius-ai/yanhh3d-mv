"""
scraper.py — YanHH3D → MonPlayer JSON
Selector: .film-poster-ahref
Output: monplayer.json chuẩn MonPlayer format
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

# ── Config ───────────────────────────────────────────────────────────────────
BASE_URL = os.getenv("YANHH_BASE_URL", "https://yanhh3d.bz")
LIST_URL = f"{BASE_URL}/moi-cap-nhat"
OUTPUT_FILE = "monplayer.json"
MAX_PAGES = int(os.getenv("MAX_PAGES", "2"))
MAX_EP_PER_FILM = int(os.getenv("MAX_EP_PER_FILM", "50"))
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

# ── Helper: Extract stream URL ───────────────────────────────────────────────
def extract_stream_url(html: str, page_url: str) -> Optional[str]:
    for m in re.finditer(r'(https?://[^\s\'"<>\\|{}]+?\.(m3u8|mp4)(?:[?&][^\s\'"<>#|{}]+)?)', html, re.I):
        url = m.group(1).strip().replace('\\', '').replace('"', '').replace("'", "")
        if url and url.startswith('http') and 'ads' not in url.lower() and 'google' not in url.lower():
            return url
    return None

# ── Step 1: Get slugs với selector .film-poster-ahref ────────────────────────
def get_movie_slugs(page: Page, page_num: int = 1) -> List[str]:
    url = LIST_URL if page_num == 1 else f"{LIST_URL}?page={page_num}"
    log.info(f"📄 Loading {url}")
    
    page.goto(url, wait_until="networkidle", timeout=TIMEOUT)
    page.wait_for_timeout(3000)
    
    slugs = []
    seen = set()
    
    for a in page.query_selector_all(".film-poster-ahref[href]"):
        href = a.get_attribute("href") or ""
        title = a.get_attribute("title") or ""
        
        if not href.startswith("http") and not href.startswith("/"):
            continue
        
        slug = href.replace(BASE_URL, "").strip("/")
        if "/" in slug or not slug:
            continue
            
        skip = {
            "moi-cap-nhat", "the-loai", "dang-nhap", "dang-ky", "lien-he",
            "tim-kiem", "lich-phim", "tag", "actor", "country", "year",
            "phim-le", "phim-bo", "danh-sach", "home", "login", "register",
            "hoat-hinh-3d", "hoat-hinh-2d", "hoat-hinh-4k", "hoan-thanh", 
            "dang-chieu", "vendor", "livewire", "cdn",
        }
        if slug in skip or slug in seen:
            continue
        if len(title) < 3 or len(title) > 100:
            continue
        
        seen.add(slug)
        slugs.append(f"/{slug}")
        log.info(f"  ✅ /{slug:40s} → '{title[:50]}'")
        
        if len(slugs) >= 25:
            break
    
    log.info(f"🎯 Page {page_num}: {len(slugs)} movies found")
    return slugs

# ── Step 2: Scrape film ──────────────────────────────────────────────────────
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
    title = re.sub(r"\s*[-–|]\s*yanhh?3d.*$", "", title, flags=re.I).strip()
    if not title:
        title = slug.strip("/").replace("-", " ").title()
    
    # Image
    image = ""
    try:
        image = page.locator("meta[property='og:image']").first.get_attribute("content") or ""
    except: pass
    if not image:
        try: image = page.locator("img.film-poster-img").first.get_attribute("data-src") or ""
        except: pass
    if image and image.startswith("/"): image = BASE_URL + image
    
    # Episodes
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
    log.info(f"  🔍 Found {len(episodes)} episodes")
    
    # Streams
    streams = []
    if not episodes:
        url = extract_stream_url(page.content(), url)
        if url:
            streams.append(Stream("Xem phim", url))
            log.info(f"  ✅ [lẻ] Found stream")
    else:
        for ep_name, ep_url in episodes[:min(3, MAX_EP_PER_FILM)]:
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
    log.info("  🎬 YanHH3D Scraper — Ready to run")
    log.info(f"  Pages: {MAX_PAGES} | Max episodes: {MAX_EP_PER_FILM}")
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
        
        if not all_slugs:
            log.error("❌ No slugs found!")
            browser.close()
            return
        
        for i, slug in enumerate(all_slugs, 1):
            log.info(f"[{i}/{len(all_slugs)}] {slug}")
            item = scrape_film(page, slug)
            if item:
                items.append(item)
                log.info(f"  🎉 Added: {item.title} — {len(item.streams)} streams")
                if len(items) >= 5:
                    break
            time.sleep(DELAY)
        browser.close()
        
    # Output JSON
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
    
    log.info(f"✨ Exported: {len(items)} movies, {sum(len(i['streams']) for i in output['items'])} streams → {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
