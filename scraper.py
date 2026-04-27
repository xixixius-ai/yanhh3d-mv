"""
scraper.py — YanHH3D → MonPlayer JSON (FULL Playwright Version)
- Render JS bằng Chromium headless để bắt link Livewire dynamic
- Extract stream .m3u8/.mp4 từ fbcdn.cloud và các CDN khác
- Output chuẩn MonPlayer format + timestamp để force git commit
- Chạy được trên GitHub Actions và local
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

from playwright.sync_api import sync_playwright, Page

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL = "https://yanhh3d.bz"
LIST_URL = f"{BASE_URL}/moi-cap-nhat"
OUTPUT_FILE = "monplayer.json"
MAX_PAGES = 3
DELAY = 2.0
MAX_EP_PER_FILM = 100
TIMEOUT = 30000  # 30 seconds

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
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

# ── Helper: Extract stream URL from rendered HTML ────────────────────────────
def extract_stream_url(html: str, page_url: str) -> Optional[str]:
    """
    Extract .m3u8/.mp4 URL from HTML with priority:
    1. Direct URLs in HTML/JS
    2. JS config patterns (file/src/source)
    3. Iframe src
    Accepts fbcdn.cloud, Facebook CDN, and other streaming CDNs
    """
    # 1. Direct .m3u8 / .mp4 URLs (including query params)
    pattern = r'(https?://[^\s\'"<>\\]+?\.(m3u8|mp4)(?:[?&][^\s\'"<>#]+)?)'
    for match in re.finditer(pattern, html, re.I):
        url = match.group(1).strip().replace('\\', '')
        if url and url.startswith('http'):
            # Skip ads, keep streaming CDNs
            if any(x in url.lower() for x in ["googlesyndication", "doubleclick", "ads.", "googlevideo"]):
                continue
            log.info(f"✅ Found direct stream: {url[:100]}...")
            return url
    
    # 2. JS config: file: "url", src='url', source: "url", etc.
    js_patterns = [
        r'(?:file|src|source|url|data-url|data-src)\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)(?:[?&][^"\']+)?)["\']',
        r'sources\s*:\s*\[\s*\{[^}]*?(?:file|src)\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)[^"\']*)["\']',
        r'playerInstance\.setup\s*\([^)]*?(?:file|src)\s*[:=]\s*["\']([^"\']+?\.(?:m3u8|mp4)[^"\']*)["\']',
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
    
    # 3. Iframe with direct stream src
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for iframe in soup.find_all("iframe", src=True):
        src = iframe.get("src", "").strip()
        if src and ('.m3u8' in src or '.mp4' in src):
            if not any(x in src.lower() for x in ["youtube", "google", "facebook"]):
                log.info(f"✅ Found iframe stream: {src[:100]}...")
                return src
    
    log.warning(f"⚠️  No valid stream found in {page_url}")
    return None

# ── Step 1: Get movie slugs from listing (Playwright) ─────────────────────────
def get_movie_slugs(page: Page, page_num: int = 1) -> List[str]:
    """Crawl listing page với JS rendering (Livewire)"""
    url = LIST_URL if page_num == 1 else f"{LIST_URL}?page={page_num}"
    log.info(f"📄 Loading listing page {page_num}: {url}")
    
    try:
        page.goto(url, wait_until="networkidle", timeout=TIMEOUT)
        page.wait_for_timeout(2000)  # Đợi Livewire render content
    except Exception as e:
        log.error(f"❌ Failed to load {url}: {e}")
        return []
    
    slugs = []
    seen = set()
    
    # Tìm tất cả link <a href="/slug">
    for a in page.query_selector_all("a[href]"):
        href = a.get_attribute("href") or ""
        
        # Filter: chỉ lấy link nội bộ, dạng /ten-phim
        if not href.startswith("/") or href.startswith("//"):
            continue
        if any(x in href.lower() for x in [
            "#", "?", "javascript", ".css", ".js", ".png", ".jpg", ".gif",
            "vendor", "livewire", "cdn", "tap-", "sever", "server",
            "dang-nhap", "dang-ky", "lien-he", "tim-kiem"
        ]):
            continue
        
        slug = href.rstrip("/")
        parts = slug.strip("/").split("/")
        
        # Chỉ chấp nhận pattern: /ten-phim (1 segment duy nhất)
        if len(parts) != 1 or not parts[0]:
            continue
        
        # Skip reserved paths
        skip = {"moi-cap-nhat", "the-loai", "lich-phim", "tag", "actor", 
                "country", "year", "phim-le", "phim-bo", "danh-sach", "home", ""}
        if parts[0] in skip:
            continue
        
        # Kiểm tra text link có vẻ là tên phim (có chữ, độ dài hợp lý)
        text = a.text_content().strip()
        if len(text) < 3 or len(text) > 100:
            continue
        
        if slug not in seen:
            seen.add(slug)
            slugs.append(slug)
            if len(slugs) >= 30:  # Giới hạn để tránh quá tải
                break
    
    log.info(f"✅ Page {page_num}: found {len(slugs)} movie slugs")
    return slugs

# ── Step 2: Get episode URLs from movie page ──────────────────────────────────
def get_episode_urls(page: Page, slug: str) -> List[Tuple[str, str]]:
    """Tìm danh sách tập từ trang phim, trả về [(name, url), ...]"""
    film_url = BASE_URL + slug
    log.info(f"🎬 Loading movie page: {slug}")
    
    try:
        page.goto(film_url, wait_until="networkidle", timeout=TIMEOUT)
        page.wait_for_timeout(1500)
    except Exception as e:
        log.error(f"❌ Failed to load {film_url}: {e}")
        return []
    
    episodes = []
    seen = set()
    
    for a in page.query_selector_all("a[href]"):
        href = a.get_attribute("href") or ""
        if "/tap-" not in href.lower():
            continue
        
        # Chuẩn hóa URL
        full_url = href if href.startswith("http") else BASE_URL + href.rstrip("/")
        
        # Bỏ server2/server3 để tránh trùng
        if any(x in full_url.lower() for x in ["/sever", "/server", "?server="]):
            continue
        
        if full_url in seen:
            continue
        seen.add(full_url)
        
        # Lấy label tập
        label = a.text_content().strip() or a.get_attribute("title") or ""
        if not label:
            m = re.search(r"/tap-(\d+)", href, re.I)
            label = f"Tập {m.group(1)}" if m else "?"
        elif not re.search(r"t[ạa]p\s*\d+", label, re.I):
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

# ── Step 3: Scrape single movie with metadata + streams ───────────────────────
def scrape_film(page: Page, slug: str) -> Optional[MovieItem]:
    """Crawl chi tiết 1 phim: title, image, desc, streams"""
    film_url = BASE_URL + slug
    
    try:
        page.goto(film_url, wait_until="networkidle", timeout=TIMEOUT)
        page.wait_for_timeout(1500)
    except Exception as e:
        log.error(f"❌ Failed to scrape {slug}: {e}")
        return None
    
    html = page.content()
    
    # ── Title ───────────────────────────────────────────────────────────
    title = ""
    for sel in ["h1.film-title", "h1.title", ".movie-title h1", "h1"]:
        try:
            el = page.locator(sel).first
            if el.count() > 0:
                title = el.text_content(timeout=5000) or ""
                if title:
                    break
        except:
            pass
    if not title:
        try:
            title = page.title() or slug.replace("-", " ").title()
        except:
            title = slug.replace("-", " ").title()
    title = re.sub(r"\s*[-–|]\s*yanhh?3d.*$", "", title, flags=re.I).strip()
    
    # ── Thumbnail ────────────────────────────────────────────────────────
    image = ""
    try:
        og_img = page.locator("meta[property='og:image']").first
        if og_img.count() > 0:
            image = og_img.get_attribute("content") or ""
    except:
        pass
    if not image:
        try:
            poster = page.locator(".film-poster img, .poster img, img[src*='poster']").first
            if poster.count() > 0:
                image = poster.get_attribute("src") or poster.get_attribute("data-src") or ""
        except:
            pass
    if image:
        if image.startswith("//"):
            image = "https:" + image
        elif image.startswith("/"):
            image = BASE_URL + image
    
    # ── Description ──────────────────────────────────────────────────────
    desc = ""
    try:
        desc_meta = page.locator("meta[name='description']").first
        if desc_meta.count() > 0:
            desc = desc_meta.get_attribute("content") or ""
    except:
        pass
    desc = desc[:300] if desc else ""
    
    # ── Episodes / Streams ───────────────────────────────────────────────
    episodes = get_episode_urls(page, slug)
    streams = []
    
    if not episodes:
        # Phim lẻ: extract từ trang chính
        url = extract_stream_url(html, film_url)
        if url:
            streams.append(Stream(name="Xem phim", url=url))
            log.info(f"✅ [lẻ] '{title[:40]}' → {url[:70]}...")
        else:
            log.warning(f"⚠️  [lẻ] No stream for '{title[:40]}'")
            return None
    else:
        # Phim bộ: crawl từng tập
        log.info(f"📦 [bộ] '{title[:40]}' — {len(episodes)} episodes found")
        for ep_name, ep_url in episodes:
            try:
                page.goto(ep_url, wait_until="networkidle", timeout=TIMEOUT)
                page.wait_for_timeout(1000)
                ep_html = page.content()
                url = extract_stream_url(ep_html, ep_url)
                if url:
                    streams.append(Stream(name=ep_name, url=url))
                else:
                    log.warning(f"  ⚠️  Skip {ep_name}: no stream")
            except Exception as e:
                log.warning(f"  ⚠️  Error crawling {ep_name}: {e}")
            time.sleep(0.3)  # nhẹ hơn cho phim bộ
        
        if not streams:
            log.warning(f"⚠️  [bộ] No valid streams for '{title[:40]}'")
            return None
        log.info(f"✅ [bộ] '{title[:40]}' — {len(streams)}/{len(episodes)} episodes with stream")
    
    return MovieItem(
        title=title,
        image=image or "https://yanhh3d.bz/favicon.ico",
        description=desc,
        streams=streams
    )

# ── Main ────────────────────────────────────────────────────────────────────
def main():
    log.info("═" * 60)
    log.info("  🎬 YanHH3D Scraper — FULL Playwright Version")
    log.info(f"  Base: {BASE_URL} | Pages: {MAX_PAGES}")
    log.info("═" * 60)
    
    items = []
    
    with sync_playwright() as p:
        # Launch browser
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            bypass_csp=True,  # Bypass một số CSP nếu site chặn
        )
        page = context.new_page()
        
        # ── Step A: Crawl listing pages ─────────────────────────────────
        all_slugs = []
        for p_num in range(1, MAX_PAGES + 1):
            slugs = get_movie_slugs(page, p_num)
            if not slugs:
                log.info(f"⚠️  No more movies at page {p_num}")
                break
            all_slugs.extend(slugs)
            time.sleep(DELAY)
        
        if not all_slugs:
            log.error("❌ No movie slugs found! Site may be blocked or structure changed.")
            browser.close()
            return
        
        log.info(f"\n🎯 Total: {len(all_slugs)} movies — crawling details...\n")
        
        # ── Step B: Crawl each movie ────────────────────────────────────
        for i, slug in enumerate(all_slugs, 1):
            log.info(f"[{i:3d}/{len(all_slugs)}] {slug}")
            film = scrape_film(page, slug)
            if film:
                items.append(film)
            time.sleep(DELAY)
        
        browser.close()
    
    # ── Step C: Build output JSON ───────────────────────────────────────
    output = {
        "name": "YanHH3D — Hoạt Hình 3D/4K Thuyết Minh",
        "generated_at": datetime.now(timezone.utc).isoformat(),  # ← Force change
        "items": [
            {
                "title": f.title,
                "image": f.image,
                "description": f.description,
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
    
    # Stats
    total_streams = sum(len(item["streams"]) for item in output["items"])
    log.info(f"\n📊 Output: {len(output['items'])} movies, {total_streams} streams")
    
    if not output["items"]:
        log.warning("⚠️  WARNING: Output has NO movies! Check logs above.")
    
    # ── Write file ──────────────────────────────────────────────────────
    Path(OUTPUT_FILE).write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    
    log.info(f"✨ Exported: {OUTPUT_FILE}")
    log.info("═" * 60)

if __name__ == "__main__":
    main()
