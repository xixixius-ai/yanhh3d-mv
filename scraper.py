"""
scraper.py — Crawl yanhh3d.bz → MonPlayer JSON
Output: monplayer.json với cấu trúc:
{
  "name": "...",
  "items": [
    {
      "title": "...",
      "image": "...",
      "description": "...",
      "streams": [{"name": "Tập 1", "url": "https://...m3u8"}, ...]
    }
  ]
}
"""

import json
import re
import time
import logging
import os
import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Tuple
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = os.getenv("YANHH_BASE_URL", "https://yanhh3d.bz")
LIST_URL = f"{BASE_URL}/moi-cap-nhat"
OUTPUT_FILE = "monplayer.json"
MAX_PAGES = int(os.getenv("MAX_PAGES", "3"))
DELAY = float(os.getenv("SCRAPER_DELAY", "2.0"))
TIMEOUT = int(os.getenv("SCRAPER_TIMEOUT", "25"))
MAX_EP_PER_FILM = int(os.getenv("MAX_EP_PER_FILM", "500"))
DEBUG_MODE = os.getenv("DEBUG_SCRAPER", "false").lower() == "true"

# Headers giả lập browser thật để tránh bị chặn
HEADERS = {
    "User-Agent": os.getenv(
        "USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": BASE_URL,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}

# Thêm cookie Cloudflare nếu có (lấy từ browser devtools)
if os.getenv("CF_CLEARANCE"):
    HEADERS["Cookie"] = f"cf_clearance={os.getenv('CF_CLEARANCE')}"

# Logging config
logging.basicConfig(
    level=logging.DEBUG if DEBUG_MODE else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ── Data Models ──────────────────────────────────────────────────────────────
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

# ── HTTP Session ─────────────────────────────────────────────────────────────
session = requests.Session()
session.headers.update(HEADERS)

def fetch(url: str, retries: int = 3) -> Optional[BeautifulSoup]:
    """Fetch URL với retry logic + debug log"""
    for attempt in range(retries):
        try:
            log.debug(f"GET {url} (attempt {attempt + 1}/{retries})")
            resp = session.get(url, timeout=TIMEOUT, allow_redirects=True)
            
            # Debug: in HTML nếu cần
            if DEBUG_MODE and resp.status_code != 200:
                log.debug(f"  Status: {resp.status_code}\nPreview: {resp.text[:400]}")
            
            resp.raise_for_status()
            
            # Check Cloudflare challenge
            if "cf-browser-verification" in resp.text or "Checking your browser" in resp.text:
                log.warning(f"⚠️  Cloudflare challenge at {url}")
                return None
            
            return BeautifulSoup(resp.text, "html.parser")
            
        except requests.RequestException as e:
            wait = DELAY * (attempt + 1)
            log.warning(f"[{attempt+1}/{retries}] {url} → {e} | retry in {wait}s")
            time.sleep(wait)
        except Exception as e:
            log.error(f"Unexpected error at {url}: {type(e).__name__}: {e}")
            break
    
    log.error(f"❌ Failed after {retries} attempts: {url}")
    return None

# ── Step 1: Get movie slugs from listing page ─────────────────────────────────
SKIP_PATHS = {
    "dang-nhap", "dang-ky", "lien-he", "the-loai", "moi-cap-nhat",
    "lich-phim", "tim-kiem", "3d", "2d", "4k", "tag", "actor",
    "country", "year", "danh-sach", "phim-le", "phim-bo",
}

def get_movie_slugs(page: int = 1) -> List[str]:
    """Crawl listing page, trả về list slug phim hợp lệ"""
    url = LIST_URL if page == 1 else f"{LIST_URL}?page={page}"
    soup = fetch(url)
    if not soup:
        return []
    
    slugs = []
    seen = set()
    
    # Tìm tất cả link <a href="/slug">
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        
        # Bỏ link ngoài, anchor, JS, file tĩnh
        if not href.startswith("/") or href.startswith("//"):
            continue
        if any(c in href for c in ["#", "?", "javascript:", ".css", ".js", ".png", ".jpg"]):
            continue
        
        # Chuẩn hóa: bỏ trailing slash
        slug = href.rstrip("/")
        if slug in seen:
            continue
        
        # Chỉ chấp nhận pattern: /ten-phim (1 segment), không phải /phim/tap-1
        parts = slug.strip("/").split("/")
        if len(parts) != 1:
            continue
        if parts[0] in SKIP_PATHS:
            continue
        if "tap-" in slug.lower() or "sever" in slug.lower() or "server" in slug.lower():
            continue
        
        seen.add(slug)
        slugs.append(slug)
    
    log.info(f"📄 Page {page}: found {len(slugs)} movie slugs")
    return slugs

# ── Step 2: Get episode URLs from movie page ──────────────────────────────────
def get_episode_urls(slug: str) -> List[Tuple[str, str]]:
    """Tìm danh sách tập từ trang phim chính, trả về [(name, url), ...]"""
    soup = fetch(BASE_URL + slug)
    if not soup:
        return []
    
    episodes = []
    seen = set()
    
    # Tìm link có chứa /tap-N
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/tap-" not in href.lower():
            continue
        
        # Chuẩn hóa URL
        full_url = href if href.startswith("http") else BASE_URL + href.rstrip("/")
        
        # Bỏ server2/server3 để tránh trùng (chỉ crawl server chính)
        if any(x in full_url.lower() for x in ["/sever", "/server", "?server="]):
            continue
        
        if full_url in seen:
            continue
        seen.add(full_url)
        
        # Lấy label tập
        label = a.get_text(strip=True) or a.get("title", "")
        if not label:
            m = re.search(r"/tap-(\d+)", href, re.I)
            label = f"Tập {m.group(1)}" if m else "?"
        elif not re.search(r"t[ạa]p\s*\d+", label, re.I):
            # Nếu label không có "Tập N", thêm vào
            m = re.search(r"(\d+)", label)
            if m:
                label = f"Tập {m.group(1)}"
        
        episodes.append((label, full_url))
    
    # Sort theo số tập
    def ep_key(item: Tuple[str, str]) -> int:
        m = re.search(r"(\d+)", item[0])
        return int(m.group(1)) if m else 9999
    
    episodes.sort(key=ep_key)
    return episodes[:MAX_EP_PER_FILM]

# ── Step 3: Extract stream URL (CORE FUNCTION - IMPROVED) ─────────────────────
def extract_stream_url(soup: BeautifulSoup, page_url: str = "") -> str:
    """
    Extract stream URL với priority:
    1. Direct .m3u8 / .mp4 URLs
    2. JS config patterns (file/src/source/url)
    3. Base64 encoded URLs
    4. Iframe with direct stream src
    5. Data attributes (data-src, data-url)
    Returns empty string if not found.
    """
    html = str(soup)
    
    if DEBUG_MODE:
        log.debug(f"🔍 Parsing {len(html)} chars for stream in {page_url}")
    
    # ── 1. Direct stream URLs (.m3u8 / .mp4) ───────────────────────────────
    # Regex bắt URL có query params phức tạp, encoded chars
    direct_pattern = r'(https?://[^\s\'"<>\\]+?\.(m3u8|mp4)(?:[?&][^\s\'"<>#]+)?)'
    for match in re.finditer(direct_pattern, html, re.I):
        url = match.group(1).strip().replace('\\', '')
        if url and url.startswith('http'):
            log.info(f"✅ Found direct stream: {url[:100]}...")
            return url
    
    # ── 2. JS config patterns (jwplayer, videojs, plyr, custom) ────────────
    js_patterns = [
        # file: "url", src: 'url', source: "url", url: "url"
        r'["\']?(?:file|src|source|url|data-url|data-src|data-file)["\']?\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)(?:[?&][^"\']+)?)["\']',
        # sources: [{file: "..."}]
        r'sources\s*:\s*\[\s*\{[^}]*?(?:file|src)\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)[^"\']*)["\']',
        # player setup: setup({file: "..."})
        r'(?:player|video|jwplayer|videojs|plyr)\s*[.=]?\s*(?:setup|load|src)\s*\([^)]*?(?:file|src)\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)[^"\']*)["\']',
        # var xxx = "url.m3u8"
        r'(?:var|let|const)\s+\w+\s*=\s*["\']([^"\']+?\.(?:m3u8|mp4)(?:[?&][^"\']+)?)["\']',
    ]
    
    for pattern in js_patterns:
        for match in re.finditer(pattern, html, re.I | re.S):
            url = match.group(1).strip()
            if not url:
                continue
            # Resolve relative URL
            if not url.startswith('http'):
                url = urljoin(page_url, url)
            if '.m3u8' in url.lower() or '.mp4' in url.lower():
                log.info(f"✅ Found JS config stream: {url[:100]}...")
                return url
    
    # ── 3. Base64 encoded stream URLs ──────────────────────────────────────
    b64_pattern = r'["\']?(?:file|src|source|url)["\']?\s*[:=]\s*["\']([A-Za-z0-9+/=]{50,})["\']'
    for match in re.finditer(b64_pattern, html):
        try:
            encoded = match.group(1)
            decoded = base64.b64decode(encoded).decode('utf-8', errors='ignore')
            if '.m3u8' in decoded or '.mp4' in decoded:
                log.info(f"✅ Found base64 stream: {decoded[:100]}...")
                return decoded
        except:
            continue
    
    # ── 4. Iframe with direct stream src ───────────────────────────────────
    for iframe in soup.find_all("iframe", src=True):
        src = iframe["src"].strip()
        if not src or src in ("#", "about:blank", ""):
            continue
        if any(x in src.lower() for x in ["youtube", "google", "facebook"]):
            continue
        if '.m3u8' in src or '.mp4' in src:
            log.info(f"✅ Found iframe stream: {src[:100]}...")
            return src
    
    # ── 5. Data attributes ─────────────────────────────────────────────────
    for attr in ["data-src", "data-url", "data-file", "data-source", "data-video"]:
        for tag in soup.find_all(attrs={attr: True}):
            val = tag[attr].strip()
            if val and ('.m3u8' in val or '.mp4' in val):
                log.info(f"✅ Found {attr} stream: {val[:100]}...")
                return val
    
    # ── ❌ Not found: log debug snippet ────────────────────────────────────
    log.warning(f"⚠️  No valid stream found in {page_url}")
    
    if DEBUG_MODE:
        # Print HTML snippet around player-related keywords
        for kw in ["player", "video", "source", "file", "iframe", "embed", "jwplayer"]:
            idx = html.lower().find(kw)
            if idx > 0:
                start = max(0, idx - 300)
                end = min(len(html), idx + 400)
                snippet = html[start:end].replace("\n", " ")[:600]
                log.debug(f"📋 HTML snippet around '{kw}': {snippet}")
                break
    
    return ""

# ── Step 4: Scrape single movie ─────────────────────────────────────────────
def scrape_film(slug: str) -> Optional[MovieItem]:
    """Crawl chi tiết 1 phim: metadata + streams"""
    film_url = BASE_URL + slug
    soup = fetch(film_url)
    if not soup:
        return None
    
    # ── Title ───────────────────────────────────────────────────────────
    title = ""
    for sel in ["h1.film-title", "h1.title", ".movie-title h1", "h1", "title"]:
        tag = soup.select_one(sel)
        if tag:
            title = tag.get_text(strip=True)
            # Clean title: remove suffix like " - YanHH3D"
            title = re.sub(r"\s*[-–|]\s*yanhh?3d.*$", "", title, flags=re.I).strip()
            if title:
                break
    if not title:
        title = slug.strip("/").replace("-", " ").title()
    
    # ── Thumbnail ────────────────────────────────────────────────────────
    image = ""
    for sel in [
        "meta[property='og:image']",
        "meta[name='twitter:image']",
        ".film-poster img", ".poster img", ".movie-poster img",
        "img.thumbnail", "img.poster", "img[src*='poster']",
        "img[data-src]", "img[data-original]",
    ]:
        tag = soup.select_one(sel)
        if tag:
            image = tag.get("content") or tag.get("src") or tag.get("data-src") or tag.get("data-original") or ""
            if image:
                if image.startswith("//"):
                    image = "https:" + image
                elif image.startswith("/"):
                    image = BASE_URL + image
                break
    
    # ── Description ──────────────────────────────────────────────────────
    desc = ""
    for sel in [
        "meta[name='description']",
        "meta[property='og:description']",
        ".film-content p", ".description", ".summary", ".content-text",
    ]:
        tag = soup.select_one(sel)
        if tag:
            desc = (tag.get("content") or tag.get_text(strip=True) or "")[:300]
            if desc:
                break
    
    # ── Episodes / Streams ───────────────────────────────────────────────
    episodes = get_episode_urls(slug)
    streams = []
    
    if not episodes:
        # Phim lẻ: extract từ trang chính
        url = extract_stream_url(soup, page_url=film_url)
        if not url:
            log.warning(f"⚠️  [lẻ] No stream for '{title[:50]}' — skipping")
            return None
        streams.append(Stream(name="Xem phim", url=url))
        log.info(f"✅ [lẻ]  '{title[:55]}' → {url[:80]}...")
    else:
        # Phim bộ: crawl từng tập
        log.info(f"📦 [bộ] '{title[:50]}' — {len(episodes)} episodes found")
        for ep_name, ep_url in episodes:
            ep_soup = fetch(ep_url)
            if ep_soup:
                url = extract_stream_url(ep_soup, page_url=ep_url)
                if url:
                    streams.append(Stream(name=ep_name, url=url))
                else:
                    log.warning(f"  ⚠️  Skip {ep_name}: no stream")
            time.sleep(DELAY * 0.5)  # nhẹ hơn cho phim bộ
        
        if not streams:
            log.warning(f"⚠️  [bộ] No valid streams for '{title[:50]}' — skipping")
            return None
        log.info(f"✅ [bộ]  '{title[:50]}' — {len(streams)}/{len(episodes)} episodes with stream")
    
    return MovieItem(title=title, image=image, description=desc, streams=streams)

# ── Main ────────────────────────────────────────────────────────────────────
def main():
    log.info("═" * 60)
    log.info("  🎬 YanHH3D → MonPlayer JSON Scraper")
    log.info(f"  Base: {BASE_URL} | Pages: {MAX_PAGES} | Debug: {DEBUG_MODE}")
    log.info("═" * 60)
    
    # ── Step A: Crawl listing pages ─────────────────────────────────────
    all_slugs = []
    for page in range(1, MAX_PAGES + 1):
        log.info(f"📑 Crawling listing page {page}/{MAX_PAGES}...")
        slugs = get_movie_slugs(page)
        if not slugs:
            log.info(f"⚠️  No more movies at page {page} (or blocked).")
            break
        all_slugs.extend(slugs)
        log.info(f"   Total slugs so far: {len(all_slugs)}")
        time.sleep(DELAY)
    
    if not all_slugs:
        log.error("❌ No movie slugs found! Check selectors or anti-bot.")
        return
    
    log.info(f"\n🎯 Total: {len(all_slugs)} movies — crawling details...\n")
    
    # ── Step B: Crawl each movie ────────────────────────────────────────
    items = []
    for i, slug in enumerate(all_slugs, 1):
        log.info(f"[{i:3d}/{len(all_slugs)}] {slug}")
        film = scrape_film(slug)
        if film and film.streams:
            items.append(film)
        time.sleep(DELAY)
    
    # ── Step C: Build & export JSON ─────────────────────────────────────
    output = {
        "name": "YanHH3D — Hoạt Hình 3D/4K Thuyết Minh",
        "items": [
            {
                "title": f.title,
                "image": f.image if f.image else "https://yanhh3d.bz/favicon.ico",
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
    
    # Filter out items with no valid streams
    output["items"] = [item for item in output["items"] if item["streams"]]
    
    # Validate before write
    total_streams = sum(len(item["streams"]) for item in output["items"])
    log.info(f"\n📊 Output: {len(output['items'])} movies, {total_streams} streams")
    
    if not output["items"]:
        log.warning("⚠️  WARNING: Output has NO movies with streams!")
        log.warning("👉 Check extract_stream_url debug logs to fix regex")
    
    # Write file
    Path(OUTPUT_FILE).write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    
    log.info(f"✨ Exported: {OUTPUT_FILE}")
    log.info("═" * 60)

if __name__ == "__main__":
    main()
