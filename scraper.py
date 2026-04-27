"""
scraper.py — YanHH3D → MonPlayer (Aggressive Capture Mode)
- Regex rộng để bắt MỌI .m3u8/.mp4 có thể
- Log chi tiết HTML snippet khi tìm thấy stream để debug
- Output chuẩn MonPlayer format
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
MAX_PAGES = 2  # Giảm để test nhanh
DELAY = 2.0
MAX_EP_PER_FILM = 50  # Giảm để test nhanh
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

# ── Helper: Extract stream URL (AGGRESSIVE MODE) ─────────────────────────────
def extract_stream_url(html: str, page_url: str) -> Optional[str]:
    """
    Bắt stream với regex RỘNG — ưu tiên tìm được link trước, filter sau
    """
    found_urls = []
    
    # 1. Direct .m3u8/.mp4 — pattern cực rộng, bắt cả URL có ký tự đặc biệt
    pattern = r'(https?://[^\s\'"<>\\|{}]+?\.(m3u8|mp4)(?:[?&][^\s\'"<>#|{}]+)?)'
    for m in re.finditer(pattern, html, re.I):
        url = m.group(1).strip().replace('\\', '').replace('"', '').replace("'", "")
        if url and url.startswith('http'):
            found_urls.append(url)
    
    # 2. JS config patterns — cũng dùng pattern rộng
    js_patterns = [
        r'["\']?(?:file|src|source|url|data-url|data-src|data-file)["\']?\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)[^"\']*)["\']',
        r'sources\s*:\s*\[\s*\{[^}]*?(?:file|src)\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)[^"\']*)["\']',
        r'var\s+\w+\s*=\s*["\']([^"\']+?\.(?:m3u8|mp4)[^"\']*)["\']',
    ]
    for pattern in js_patterns:
        for m in re.finditer(pattern, html, re.I | re.S):
            url = m.group(1).strip()
            if url and not url.startswith('http'):
                url = urljoin(page_url, url)
            if '.m3u8' in url.lower() or '.mp4' in url.lower():
                found_urls.append(url)
    
    # 3. Filter: chỉ giữ URL hợp lệ (loại ads, nhưng giữ fbcdn)
    valid_urls = []
    for url in found_urls:
        # Loại quảng cáo rõ ràng
        if any(x in url.lower() for x in [
            'googlesyndication', 'doubleclick', 'adservice', 
            'advertising', 'banner', 'popunder'
        ]):
            continue
        # Giữ Facebook CDN, streaming CDNs
        valid_urls.append(url)
    
    if valid_urls:
        # Log để debug: in URL đầu tiên + snippet HTML quanh nó
        first_url = valid_urls[0]
        log.info(f"✅ Found stream: {first_url[:120]}...")
        
        # In HTML snippet để biết context (chỉ khi debug)
        if logging.getLogger().level <= logging.DEBUG:
            idx = html.lower().find(first_url[:20].lower())
            if idx > 0:
                snippet = html[max(0, idx-200):idx+200].replace("\n", " ")[:400]
                log.debug(f"📋 HTML context: ...{snippet}...")
        
        return first_url
    
    return None

# ── Step 1: Get movie slugs (giữ nguyên logic cũ, chỉ log thêm) ──────────────
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
        if "/" in slug: continue
        if slug in SKIP or slug in seen: continue
        if "-" not in slug: continue
        text = a.text_content().strip()
        if len(text) < 3 or len(text) > 60: continue
        seen.add(slug)
        slugs.append(f"/{slug}")
        if len(slugs) >= 25: break
    
    log.info(f"✅ Page {page_num}: {len(slugs)} valid slugs")
    return slugs

# ── Step 2: Scrape film (với debug log chi tiết) ─────────────────────────────
def scrape_film(page: Page, slug: str) -> Optional[MovieItem]:
    url = BASE_URL + slug
    log.info(f"🎬 Scraping {slug}")
    
    try:
        page.goto(url, wait_until="networkidle", timeout=TIMEOUT)
        page.wait_for_timeout(1500)
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
        try: image = page.locator("img.poster, img[src*='poster']").first.get_attribute("src") or ""
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
    log.info(f"  🔍 Found {len(episodes)} episodes for {title[:40]}")
    
    # Streams: AGGRESSIVE MODE — bắt được là thêm
    streams = []
    
    if not episodes:
        # Phim lẻ
        log.info("  → Phim lẻ, extracting from main page...")
        html = page.content()
        url = extract_stream_url(html, url)
        if url:
            streams.append(Stream("Xem phim", url))
            log.info(f"  ✅ [lẻ] Found stream for {title[:40]}")
    else:
        # Phim bộ — crawl 3 tập đầu để test nhanh
        log.info(f"  → Phim bộ, testing first 3 episodes...")
        for ep_name, ep_url in episodes[:min(3, MAX_EP_PER_FILM)]:
            try:
                page.goto(ep_url, wait_until="networkidle", timeout=TIMEOUT)
                page.wait_for_timeout(800)
                html = page.content()
                url = extract_stream_url(html, ep_url)
                if url:
                    streams.append(Stream(ep_name, url))
                    log.info(f"  ✅ {ep_name}: {url[:80]}...")
            except Exception as e:
                log.warning(f"  ⚠️ Error crawling {ep_name}: {e}")
            time.sleep(0.5)
    
    if not streams:
        log.warning(f"⚠️ No streams for '{title[:40]}' — skipping")
        return None
        
    return MovieItem(title=title, image=image or f"{BASE_URL}/favicon.ico", description="", streams=streams)

# ── Main ────────────────────────────────────────────────────────────────────
def main():
    log.info("═" * 60)
    log.info("  🎬 YanHH3D Scraper — Aggressive Capture Mode")
    log.info(f"  Pages: {MAX_PAGES} | Max episodes/test: 3")
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
            
        log.info(f"\n🎯 Total slugs: {len(all_slugs)} — testing stream capture...\n")
        
        for i, slug in enumerate(all_slugs, 1):
            log.info(f"[{i}/{len(all_slugs)}] {slug}")
            item = scrape_film(page, slug)
            if item:
                items.append(item)
                log.info(f"  🎉 Added: {item.title} — {len(item.streams)} streams")
                # Test xong 1 phim có stream thì dừng để verify output
                if len(items) >= 1:
                    log.info("✅ Got 1 movie with streams — stopping for test")
                    break
            time.sleep(DELAY)
        browser.close()
        
    # Output JSON — minimal, chuẩn MonPlayer
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
    
    # Stats
    total_streams = sum(len(i["streams"]) for i in output["items"])
    log.info(f"\n✨ Exported: {len(items)} movies, {total_streams} streams → {OUTPUT_FILE}")
    
    # Preview first item
    if output["items"]:
        first = output["items"][0]
        log.info(f"📋 Preview: '{first['title']}' — {len(first['streams'])} streams")
        for s in first["streams"][:2]:
            log.info(f"   • {s['name']}: {s['url'][:100]}...")

if __name__ == "__main__":
    main()
