"""
scraper.py — Crawl yanhh3d.bz → MonPlayer JSON
Cấu trúc URL thực:
  - Listing : https://yanhh3d.bz/moi-cap-nhat  (có ?page=N)
  - Phim    : https://yanhh3d.bz/{slug}
  - Tập     : https://yanhh3d.bz/{slug}/tap-{N}
              hoặc   https://yanhh3d.bz/sever2/{slug}/tap-{N}
"""

import json
import re
import time
import logging
import os
import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL         = os.getenv("YANHH_BASE_URL", "https://yanhh3d.bz")
LIST_URL         = f"{BASE_URL}/moi-cap-nhat"
OUTPUT_FILE      = "monplayer.json"
MAX_PAGES        = int(os.getenv("MAX_PAGES", "5"))
DELAY            = float(os.getenv("SCRAPER_DELAY", "1.5"))
TIMEOUT          = int(os.getenv("SCRAPER_TIMEOUT", "20"))
MAX_EP_PER_FILM  = int(os.getenv("MAX_EP_PER_FILM", "999"))
DEBUG_MODE       = os.getenv("DEBUG_SCRAPER", "false").lower() == "true"

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

# Thêm cookie nếu có (cho Cloudflare bypass)
if os.getenv("CF_CLEARANCE"):
    HEADERS["Cookie"] = f"cf_clearance={os.getenv('CF_CLEARANCE')}"

logging.basicConfig(
    level=logging.DEBUG if DEBUG_MODE else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Models ────────────────────────────────────────────────────────────────────
@dataclass
class Stream:
    name: str
    url: str

@dataclass
class MovieItem:
    title: str
    image: str
    description: str = ""
    streams: list = field(default_factory=list)

# ── HTTP ──────────────────────────────────────────────────────────────────────
session = requests.Session()
session.headers.update(HEADERS)

def get(url, retries=3):
    """Fetch URL với retry + log debug khi cần"""
    for attempt in range(retries):
        try:
            log.debug(f"GET {url} (attempt {attempt+1})")
            r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
            
            if DEBUG_MODE and r.status_code != 200:
                log.debug(f"  [DEBUG] Status: {r.status_code}\nHTML preview: {r.text[:500]}")
            
            r.raise_for_status()
            
            # Check nếu HTML bị chặn (Cloudflare challenge)
            if "cf-browser-verification" in r.text or "Checking your browser" in r.text:
                log.warning(f"⚠ Cloudflare challenge detected at {url}")
                return None
                
            return BeautifulSoup(r.text, "html.parser")
        except requests.RequestException as e:
            wait = DELAY * (attempt + 1)
            log.warning(f"[{attempt+1}/{retries}] {url} → {e} | retry in {wait}s")
            time.sleep(wait)
        except Exception as e:
            log.error(f"Unexpected error at {url}: {e}")
            break
    log.error(f"❌ Bỏ qua sau {retries} lần thử: {url}")
    return None

# ── Step 1: Listing ───────────────────────────────────────────────────────────
SKIP_PATHS = {
    "dang-nhap", "dang-ky", "lien-he", "the-loai",
    "moi-cap-nhat", "lich-phim", "tim-kiem",
    "3d", "2d", "4k", "tag", "actor", "country",
}

def get_movie_slugs(page=1):
    """Crawl listing page, trả về list slug phim"""
    url = LIST_URL if page == 1 else f"{LIST_URL}?page={page}"
    soup = get(url)
    if not soup:
        return []

    slugs = []
    seen = set()

    # Pattern: tìm link phim trong khu vực nội dung chính
    # Thường nằm trong .film-item, .movie-item, hoặc card có href="/slug"
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        
        # Bỏ link ngoài, anchor, JS
        if not href.startswith("/") or href.startswith("//"):
            continue
        if any(c in href for c in ["#", "?", "javascript:", ".css", ".js"]):
            continue
            
        # Chuẩn hóa slug: bỏ trailing slash
        slug = href.rstrip("/")
        if slug in seen:
            continue
            
        # Lọc theo pattern slug hợp lệ: /ten-phim, /ten-phim-phan-2
        parts = slug.strip("/").split("/")
        if len(parts) != 1:  # chỉ chấp nhận /slug, không phải /slug/tap-1
            continue
        if parts[0] in SKIP_PATHS:
            continue
        if "tap-" in slug or "sever" in slug.lower():
            continue
            
        seen.add(slug)
        slugs.append(slug)

    log.info(f"📄 Trang {page}: tìm thấy {len(slugs)} phim")
    return slugs

# ── Step 2: Episode list ──────────────────────────────────────────────────────
def get_episode_urls(slug):
    """Tìm danh sách tập từ trang phim chính"""
    soup = get(BASE_URL + slug)
    if not soup:
        return []

    episodes = []
    seen = set()

    # Tìm button/link tập phim: thường có class .episode, .ep-item, hoặc text "Tập N"
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/tap-" not in href.lower():
            continue
            
        # Chuẩn hóa URL
        full_url = href if href.startswith("http") else BASE_URL + href.rstrip("/")
        
        # Bỏ server2 trùng lặp (chỉ crawl server chính)
        if "/sever" in full_url.lower() or "/server" in full_url.lower():
            continue
            
        if full_url in seen:
            continue
        seen.add(full_url)
        
        # Lấy label tập
        label = a.get_text(strip=True) or a.get("title", "")
        if not label:
            m = re.search(r"/tap-(\d+)", href, re.I)
            label = f"Tập {m.group(1)}" if m else "?"
        elif "tập" not in label.lower() and "tap" not in label.lower():
            label = f"Tập {label}"
            
        episodes.append((label, full_url))

    # Sort theo số tập
    def ep_key(item):
        m = re.search(r"(\d+)", item[0])
        return int(m.group(1)) if m else 9999
        
    episodes.sort(key=ep_key)
    return episodes[:MAX_EP_PER_FILM]

# ── Step 3: Extract stream URL (IMPROVED) ─────────────────────────────────────
def extract_stream_url(soup, page_url=""):
    """
    Extract stream URL với priority:
    1. Direct .m3u8/.mp4 URLs
    2. JS config patterns (file/source/src)
    3. Base64 encoded URLs
    4. Iframe embeds
    5. Data attributes
    """
    html = str(soup)
    if DEBUG_MODE:
        log.debug(f"🔍 Parsing {len(html)} chars for stream in {page_url}")

    # ── 1. Direct stream URLs (cải tiến regex) ─────────────────────────────
    # Bắt URL có query params phức tạp, encoded characters
    direct_pattern = r'(https?://[^\s\'"\\<>]+?\.(m3u8|mp4)(?:[?&][^\s\'"\\<>#]+)?)'
    for match in re.finditer(direct_pattern, html, re.I):
        url = match.group(1).strip()
        if url and not url.startswith("data:"):
            log.info(f"✅ Found direct stream: {url[:120]}...")
            return url

    # ── 2. JS config patterns (jwplayer, videojs, plyr, custom players) ───
    js_patterns = [
        # file: "url", src: 'url', source: "url"
        r'["\']?(?:file|src|source|url|data-url)["\']?\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)(?:[?&][^"\']+)?)["\']',
        # sources: [{file: "..."}]
        r'sources\s*:\s*\[\s*\{[^}]*?(?:file|src)\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)[^"\']*)["\']',
        # player setup patterns
        r'(?:player|video|jwplayer|videojs)\s*[.=]\s*(?:setup|load|src)\s*\([^)]*?(?:file|src)\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)[^"\']*)["\']',
    ]
    
    for pattern in js_patterns:
        for match in re.finditer(pattern, html, re.I | re.S):
            url = match.group(1).strip()
            if url and not url.startswith("http"):
                url = urljoin(page_url, url)
            if url and (".m3u8" in url.lower() or ".mp4" in url.lower()):
                log.info(f"✅ Found JS config stream: {url[:120]}...")
                return url

    # ── 3. Base64 encoded stream URLs ─────────────────────────────────────
    b64_pattern = r'["\']?(?:file|src|source|url)["\']?\s*[:=]\s*["\']([A-Za-z0-9+/=]{40,})["\']'
    for match in re.finditer(b64_pattern, html):
        try:
            encoded = match.group(1)
            # Thử decode base64
            decoded = base64.b64decode(encoded).decode('utf-8', errors='ignore')
            if '.m3u8' in decoded or '.mp4' in decoded:
                log.info(f"✅ Found base64 stream: {decoded[:120]}...")
                return decoded
        except:
            continue

    # ── 4. Iframe embeds (có thể cần crawl tiếp) ──────────────────────────
    for iframe in soup.find_all("iframe", src=True):
        src = iframe["src"].strip()
        if not src or src in ("#", "about:blank", ""):
            continue
        if "youtube" in src or "google" in src:
            continue
        # Nếu iframe trỏ đến .m3u8/.mp4 trực tiếp
        if ".m3u8" in src or ".mp4" in src:
            log.info(f"✅ Found iframe stream: {src[:120]}...")
            return src
        # Nếu iframe trỏ đến player page, có thể thử crawl tiếp (tuỳ chọn)
        if DEBUG_MODE:
            log.debug(f"🔗 Found iframe (may need recursive crawl): {src[:100]}")

    # ── 5. Data attributes (data-src, data-url, data-file) ────────────────
    for attr in ["data-src", "data-url", "data-file", "data-source"]:
        for tag in soup.find_all(attrs={attr: True}):
            val = tag[attr].strip()
            if val and (".m3u8" in val or ".mp4" in val):
                log.info(f"✅ Found {attr} stream: {val[:120]}...")
                return val

    # ── ❌ Không tìm thấy: LOG chi tiết để debug ─────────────────────────
    log.warning(f"⚠️  No valid stream found in {page_url}")
    
    if DEBUG_MODE:
        # In snippet HTML quanh các từ khoá player
        for kw in ["player", "video", "source", "file", "iframe", "embed"]:
            idx = html.lower().find(kw)
            if idx > 0:
                start = max(0, idx - 300)
                end = min(len(html), idx + 400)
                snippet = html[start:end].replace("\n", " ")[:600]
                log.debug(f"📋 HTML snippet around '{kw}': {snippet}")
                break
    
    return ""

# ── Step 4: Scrape 1 phim ─────────────────────────────────────────────────────
def scrape_film(slug):
    """Crawl chi tiết 1 phim: metadata + streams"""
    film_url = BASE_URL + slug
    soup = get(film_url)
    if not soup:
        return None

    # ── Title ───────────────────────────────────────────────────────────
    title = ""
    for sel in ["h1.film-title", "h1.title", ".movie-title h1", "title"]:
        tag = soup.select_one(sel)
        if tag:
            title = tag.get_text(strip=True)
            # Clean title: bỏ suffix như " - YanHH3D"
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
    ]:
        tag = soup.select_one(sel)
        if tag:
            image = tag.get("content") or tag.get("src") or tag.get("data-src") or ""
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
        ".film-content p", ".description", ".summary",
        "div.content-text p:first-child",
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
            log.warning(f"⚠️  [lẻ] No stream for {title[:50]} — skipping")
            return None
        streams.append(Stream(name="Xem phim", url=url))
        log.info(f"✅ [lẻ]  {title[:55]} → {url[:80]}...")
    else:
        # Phim bộ: crawl từng tập
        log.info(f"📦 [bộ] {title[:50]} — {len(episodes)} tập tìm thấy")
        for ep_name, ep_url in episodes:
            ep_soup = get(ep_url)
            if ep_soup:
                url = extract_stream_url(ep_soup, page_url=ep_url)
                if url:
                    streams.append(Stream(name=ep_name, url=url))
                else:
                    log.warning(f"  ⚠️  Skip {ep_name}: no stream")
            time.sleep(DELAY * 0.5)  # nhẹ hơn cho phim bộ
        
        if not streams:
            log.warning(f"⚠️  [bộ] No valid streams for {title[:50]} — skipping")
            return None
        log.info(f"✅ [bộ]  {title[:50]} — {len(streams)}/{len(episodes)} tập có stream")

    return MovieItem(title=title, image=image, description=desc, streams=streams)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("═" * 60)
    log.info("  🎬 YanHH3D → MonPlayer JSON Scraper")
    log.info(f"  Base: {BASE_URL} | Pages: {MAX_PAGES} | Debug: {DEBUG_MODE}")
    log.info("═" * 60)

    # ── Step A: Crawl listing ───────────────────────────────────────────
    all_slugs = []
    for page in range(1, MAX_PAGES + 1):
        log.info(f"📑 Crawling listing page {page}...")
        slugs = get_movie_slugs(page)
        if not slugs:
            log.info(f"⚠️  Hết phim hoặc lỗi ở trang {page}.")
            break
        all_slugs.extend(slugs)
        log.info(f"   Total slugs so far: {len(all_slugs)}")
        time.sleep(DELAY)

    if not all_slugs:
        log.error("❌ Không tìm thấy phim nào! Kiểm tra selector hoặc anti-bot.")
        return

    log.info(f"\n🎯 Tổng: {len(all_slugs)} phim — bắt đầu crawl chi tiết...\n")

    # ── Step B: Crawl từng phim ─────────────────────────────────────────
    items = []
    for i, slug in enumerate(all_slugs, 1):
        log.info(f"[{i:3d}/{len(all_slugs)}] {slug}")
        film = scrape_film(slug)
        if film and film.streams:
            items.append(film)
        time.sleep(DELAY)

    # ── Step C: Export JSON ─────────────────────────────────────────────
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

    # Validate output
    total_streams = sum(len(f["streams"]) for f in output["items"])
    log.info(f"\n📊 Output: {len(items)} phim, {total_streams} streams")

    Path(OUTPUT_FILE).write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Warning nếu không có phim nào có stream
    if len(items) == 0:
        log.warning("⚠️  WARNING: Output rỗng! Kiểm tra log debug để fix selector.")
    else:
        log.info(f"✨ Xong! {len(items)} phim → {OUTPUT_FILE}")
    
    log.info("═" * 60)

if __name__ == "__main__":
    main()
