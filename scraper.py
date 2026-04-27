"""
scraper.py — Crawl yanhh3d.bz → MonPlayer JSON (FIXED v2)
- Fix: Tìm slug phim đúng cấu trúc site thực tế
- Fix: Bắt stream từ fbcdn.cloud / Facebook CDN
- Output: monplayer.json chuẩn MonPlayer format
"""

import json
import re
import time
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = os.getenv("YANHH_BASE_URL", "https://yanhh3d.bz")
LIST_URL = f"{BASE_URL}/moi-cap-nhat"
OUTPUT_FILE = "monplayer.json"
MAX_PAGES = int(os.getenv("MAX_PAGES", "3"))
DELAY = float(os.getenv("SCRAPER_DELAY", "2.0"))
TIMEOUT = int(os.getenv("SCRAPER_TIMEOUT", "25"))
MAX_EP_PER_FILM = int(os.getenv("MAX_EP_PER_FILM", "100"))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0 Safari/537.36",
    "Referer": BASE_URL,
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

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

session = requests.Session()
session.headers.update(HEADERS)

def fetch(url: str, retries: int = 3) -> Optional[BeautifulSoup]:
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
            r.raise_for_status()
            # Check Cloudflare
            if "cf-browser-verification" in r.text:
                log.warning(f"⚠️  Cloudflare at {url}")
                return None
            return BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            log.warning(f"[{attempt+1}/{retries}] {url} → {e}")
            time.sleep(DELAY * (attempt + 1))
    return None

# ── Step 1: Get movie slugs (FIXED selectors) ─────────────────────────────────
def get_movie_slugs(page: int = 1) -> List[str]:
    url = LIST_URL if page == 1 else f"{LIST_URL}?page={page}"
    soup = fetch(url)
    if not soup:
        return []
    
    slugs = []
    seen = set()
    
    # Strategy: Tìm link trong khu vực nội dung phim
    # Thử nhiều selector phổ biến cho film cards
    candidates = []
    
    # 1. Tìm trong các container thường chứa phim
    for container_sel in [".film-list", ".movies", ".video-list", ".list-film", "[data-list]", "main", ".content"]:
        container = soup.select_one(container_sel)
        if container:
            candidates.extend(container.find_all("a", href=True))
    
    # 2. Nếu không có container, tìm tất cả link trong body
    if not candidates:
        candidates = soup.find_all("a", href=True)
    
    for a in candidates:
        href = a.get("href", "").strip()
        if not href.startswith("/") or href.startswith("//"):
            continue
        if any(x in href.lower() for x in ["#", "?", "javascript", ".css", ".js", ".png", ".jpg", "vendor", "livewire", "cdn"]):
            continue
        
        slug = href.rstrip("/")
        parts = slug.strip("/").split("/")
        
        # Chỉ chấp nhận: /ten-phim (1 segment, không phải /phim/tap-1)
        if len(parts) != 1:
            continue
        
        # Lọc từ khóa không phải phim
        skip_words = {"moi-cap-nhat", "the-loai", "dang-nhap", "dang-ky", "lien-he", 
                     "tim-kiem", "lich-phim", "tag", "actor", "country", "year", 
                     "phim-le", "phim-bo", "danh-sach", "home", ""}
        if parts[0] in skip_words:
            continue
        
        # Kiểm tra text link có vẻ là tên phim (có chữ, không chỉ số)
        text = a.get_text(strip=True)
        if not text or len(text) < 3:
            continue
        
        if slug in seen:
            continue
        seen.add(slug)
        slugs.append(slug)
        
        # Giới hạn 30 phim/page để tránh quá tải
        if len(slugs) >= 30:
            break
    
    log.info(f"📄 Page {page}: found {len(slugs)} movies")
    return slugs

# ── Step 2: Get episode URLs ──────────────────────────────────────────────────
def get_episode_urls(slug: str) -> List[Tuple[str, str]]:
    soup = fetch(BASE_URL + slug)
    if not soup:
        return []
    
    episodes = []
    seen = set()
    
    for a in soup.find_all("a", href=True):
        href = a.get("href", "")
        if "/tap-" not in href.lower():
            continue
        
        full_url = href if href.startswith("http") else BASE_URL + href.rstrip("/")
        if any(x in full_url.lower() for x in ["/sever", "/server", "?server="]):
            continue
        if full_url in seen:
            continue
        seen.add(full_url)
        
        label = a.get_text(strip=True) or a.get("title", "")
        if not label:
            m = re.search(r"/tap-(\d+)", href, re.I)
            label = f"Tập {m.group(1)}" if m else "?"
        elif not re.search(r"t[ạa]p\s*\d+", label, re.I):
            m = re.search(r"(\d+)", label)
            if m:
                label = f"Tập {m.group(1)}"
        
        episodes.append((label, full_url))
    
    # Sort by episode number
    def ep_key(item):
        m = re.search(r"(\d+)", item[0])
        return int(m.group(1)) if m else 9999
    episodes.sort(key=ep_key)
    
    return episodes[:MAX_EP_PER_FILM]

# ── Step 3: Extract stream URL (FIXED: accept fbcdn.cloud) ────────────────────
def extract_stream_url(soup: BeautifulSoup, page_url: str = "") -> str:
    html = str(soup)
    
    # 1. Direct .m3u8 / .mp4 (bao gồm Facebook CDN: fbcdn.cloud)
    # Pattern mở rộng: chấp nhận domain có dấu gạch ngang, subdomain
    direct_pattern = r'(https?://[^\s\'"<>\\]+?\.(m3u8|mp4)(?:[?&][^\s\'"<>#]+)?)'
    for match in re.finditer(direct_pattern, html, re.I):
        url = match.group(1).strip().replace('\\', '')
        if url and url.startswith('http'):
            # Filter quảng cáo, nhưng giữ fbcdn
            if any(x in url.lower() for x in ["googlesyndication", "doubleclick", "ads."]):
                continue
            log.info(f"✅ Found stream: {url[:100]}...")
            return url
    
    # 2. JS config: src="...", file: "...", source: "..."
    js_patterns = [
        r'(?:src|file|source|url)\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)(?:[?&][^"\']+)?)["\']',
        r'sources\s*:\s*\[\s*\{[^}]*?(?:file|src)\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)[^"\']*)["\']',
    ]
    for pattern in js_patterns:
        for match in re.finditer(pattern, html, re.I | re.S):
            url = match.group(1).strip()
            if not url:
                continue
            if not url.startswith('http'):
                url = urljoin(page_url, url)
            if '.m3u8' in url.lower() or '.mp4' in url.lower():
                if any(x in url.lower() for x in ["googlesyndication", "doubleclick"]):
                    continue
                log.info(f"✅ Found JS stream: {url[:100]}...")
                return url
    
    # 3. Iframe với src chứa stream
    for iframe in soup.find_all("iframe", src=True):
        src = iframe["src"].strip()
        if src and '.m3u8' in src or '.mp4' in src:
            return src
    
    log.warning(f"⚠️  No stream found in {page_url}")
    return ""

# ── Step 4: Scrape single movie ───────────────────────────────────────────────
def scrape_film(slug: str) -> Optional[MovieItem]:
    film_url = BASE_URL + slug
    soup = fetch(film_url)
    if not soup:
        return None
    
    # Title
    title = ""
    for sel in ["h1.film-title", "h1.title", ".movie-title h1", "h1", "title"]:
        tag = soup.select_one(sel)
        if tag:
            title = tag.get_text(strip=True)
            title = re.sub(r"\s*[-–|]\s*yanhh?3d.*$", "", title, flags=re.I).strip()
            if title:
                break
    if not title:
        title = slug.strip("/").replace("-", " ").title()
    
    # Image
    image = ""
    for sel in ["meta[property='og:image']", ".film-poster img", ".poster img", "img[src*='poster']"]:
        tag = soup.select_one(sel)
        if tag:
            image = tag.get("content") or tag.get("src") or tag.get("data-src") or ""
            if image:
                if image.startswith("//"):
                    image = "https:" + image
                elif image.startswith("/"):
                    image = BASE_URL + image
                break
    
    # Description
    desc = ""
    for sel in ["meta[name='description']", "meta[property='og:description']", ".description"]:
        tag = soup.select_one(sel)
        if tag:
            desc = (tag.get("content") or tag.get_text(strip=True) or "")[:300]
            if desc:
                break
    
    # Episodes / Streams
    episodes = get_episode_urls(slug)
    streams = []
    
    if not episodes:
        # Phim lẻ
        url = extract_stream_url(soup, page_url=film_url)
        if not url:
            log.warning(f"⚠️  [lẻ] No stream for '{title[:40]}'")
            return None
        streams.append(Stream(name="Xem phim", url=url))
        log.info(f"✅ [lẻ] '{title[:40]}'")
    else:
        # Phim bộ
        for ep_name, ep_url in episodes:
            ep_soup = fetch(ep_url)
            if ep_soup:
                url = extract_stream_url(ep_soup, page_url=ep_url)
                if url:
                    streams.append(Stream(name=ep_name, url=url))
            time.sleep(0.5)
        if not streams:
            log.warning(f"⚠️  [bộ] No streams for '{title[:40]}'")
            return None
        log.info(f"✅ [bộ] '{title[:40]}' — {len(streams)} tập")
    
    return MovieItem(title=title, image=image, description=desc, streams=streams)

# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    log.info("═" * 60)
    log.info("  🎬 YanHH3D Scraper — FIXED v2")
    log.info("═" * 60)
    
    all_slugs = []
    for page in range(1, MAX_PAGES + 1):
        log.info(f"📑 Crawling page {page}...")
        slugs = get_movie_slugs(page)
        if not slugs:
            log.info(f"⚠️  No more movies at page {page}")
            break
        all_slugs.extend(slugs)
        time.sleep(DELAY)
    
    if not all_slugs:
        log.error("❌ No movies found! Check listing selectors.")
        return
    
    log.info(f"\n🎯 Total: {len(all_slugs)} movies — crawling...\n")
    
    items = []
    for i, slug in enumerate(all_slugs, 1):
        log.info(f"[{i:3d}/{len(all_slugs)}] {slug}")
        film = scrape_film(slug)
        if film and film.streams:
            items.append(film)
        time.sleep(DELAY)
    
    # Build output
    output = {
        "name": "YanHH3D — Hoạt Hình 3D/4K Thuyết Minh",
        "items": [
            {
                "title": f.title,
                "image": f.image or "https://yanhh3d.bz/favicon.ico",
                "description": f.description[:200] if f.description else "",
                "streams": [
                    {"name": s.name, "url": s.url}
                    for s in f.streams
                    if s.url and ('.m3u8' in s.url.lower() or '.mp4' in s.url.lower())
                ],
            }
            for f in items
        ],
    }
    
    # Filter empty
    output["items"] = [item for item in output["items"] if item["streams"]]
    
    # Write
    Path(OUTPUT_FILE).write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    
    log.info(f"✨ Exported: {len(output['items'])} movies, {sum(len(i['streams']) for i in output['items'])} streams")

if __name__ == "__main__":
    main()
