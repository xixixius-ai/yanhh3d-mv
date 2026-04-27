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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL         = "https://yanhh3d.bz"
LIST_URL         = f"{BASE_URL}/moi-cap-nhat"
OUTPUT_FILE      = "monplayer.json"
MAX_PAGES        = 5      # số trang listing
DELAY            = 1.5    # giây giữa request
TIMEOUT          = 15
MAX_EP_PER_FILM  = 999    # tập tối đa / phim

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": BASE_URL,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}

logging.basicConfig(
    level=logging.INFO,
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
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
            r.raise_for_status()
            return BeautifulSoup(r.text, "html.parser")
        except requests.RequestException as e:
            wait = DELAY * (attempt + 1)
            log.warning(f"[{attempt+1}/{retries}] {url} → {e} | retry {wait}s")
            time.sleep(wait)
    log.error(f"Bỏ qua: {url}")
    return None

# ── Step 1: Listing ───────────────────────────────────────────────────────────
SKIP_PATHS = {
    "dang-nhap", "dang-ky", "lien-he", "the-loai",
    "moi-cap-nhat", "lich-phim", "tim-kiem",
    "3d", "2d", "4k",
}

def get_movie_slugs(page=1):
    url = LIST_URL if page == 1 else f"{LIST_URL}?page={page}"
    soup = get(url)
    if not soup:
        return []

    slugs = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href.startswith("/"):
            continue
        parts = href.strip("/").split("/")
        if not parts or not parts[0]:
            continue
        if parts[0] in SKIP_PATHS:
            continue
        if "tap-" in href or "sever" in href:
            continue
        if any(c in href for c in ["#", "?", "javascript:"]):
            continue
        if href not in seen:
            seen.add(href)
            slugs.append(href)

    log.info(f"  Trang {page}: {len(slugs)} phim")
    return slugs

# ── Step 2: Episode list ──────────────────────────────────────────────────────
def get_episode_urls(slug):
    soup = get(BASE_URL + slug)
    if not soup:
        return []

    episodes = []
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/tap-" not in href:
            continue
        full = href if href.startswith("http") else BASE_URL + href
        # canonical: bỏ /sever2/ để tránh trùng
        canonical = re.sub(r"https?://[^/]+/sever\d+/", BASE_URL + "/", full)
        if canonical in seen:
            continue
        seen.add(canonical)
        label = a.get_text(strip=True)
        if not label:
            m = re.search(r"/tap-(.+?)(?:/|$)", href)
            label = m.group(1) if m else "?"
        episodes.append((f"Tập {label}", full))

    def ep_num(item):
        m = re.search(r"/tap-(\d+)", item[1])
        return int(m.group(1)) if m else 0

    episodes.sort(key=ep_num)
    return episodes[:MAX_EP_PER_FILM]

# ── Step 3: Extract stream URL ────────────────────────────────────────────────
def extract_stream_url(soup):
    html = str(soup)

    # m3u8
    for url in re.findall(r'https?://[^\s\'"\\<>]+\.m3u8(?:\?[^\s\'"\\<>]*)?', html):
        return url

    # mp4
    for url in re.findall(r'https?://[^\s\'"\\<>]+\.mp4(?:\?[^\s\'"\\<>]*)?', html):
        return url

    # JS file/source/src key
    for url in re.findall(
        r'(?:file|source|src)\s*[=:]\s*["\']([^"\']+\.(?:m3u8|mp4)[^"\']*)["\']', html
    ):
        return url

    # iframe
    for iframe in soup.find_all("iframe", src=True):
        src = iframe["src"]
        if src and src not in ("#", "about:blank", ""):
            return src

    return ""

# ── Step 4: Scrape 1 phim ─────────────────────────────────────────────────────
def scrape_film(slug):
    film_url = BASE_URL + slug
    soup = get(film_url)
    if not soup:
        return None

    # Title
    title = ""
    for sel in ["h1", ".film-title", ".title-film", "title"]:
        tag = soup.select_one(sel)
        if tag:
            title = tag.get_text(strip=True)
            title = re.sub(r"\s*[-–|]\s*yanhh3d.*$", "", title, flags=re.I).strip()
            if title:
                break
    if not title:
        title = slug.strip("/").replace("-", " ").title()

    # Thumbnail
    image = ""
    for sel in [
        "meta[property='og:image']",
        ".film-poster img", ".poster img",
        "img.thumb", "img.poster",
    ]:
        tag = soup.select_one(sel)
        if tag:
            image = tag.get("content") or tag.get("src") or ""
            if image:
                if image.startswith("/"):
                    image = BASE_URL + image
                break

    # Description
    desc = ""
    for sel in [
        "meta[name='description']",
        "meta[property='og:description']",
        ".film-content p", ".description",
    ]:
        tag = soup.select_one(sel)
        if tag:
            desc = (tag.get("content") or tag.get_text(strip=True) or "")[:300]
            if desc:
                break

    # Episodes
    episodes = get_episode_urls(slug)
    streams = []

    if not episodes:
        # Phim lẻ
        url = extract_stream_url(soup) or film_url
        streams.append(Stream(name="Xem phim", url=url))
        log.info(f"  ✓ [lẻ]  {title[:55]}")
    else:
        # Phim bộ
        for ep_name, ep_url in episodes:
            ep_soup = get(ep_url)
            time.sleep(DELAY)
            if ep_soup:
                url = extract_stream_url(ep_soup) or ep_url
                streams.append(Stream(name=ep_name, url=url))
        log.info(f"  ✓ [bộ]  {title[:50]} — {len(streams)} tập")

    return MovieItem(title=title, image=image, description=desc, streams=streams)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("═" * 55)
    log.info("  YanHH3D → MonPlayer JSON Scraper")
    log.info("═" * 55)

    all_slugs = []
    for page in range(1, MAX_PAGES + 1):
        slugs = get_movie_slugs(page)
        if not slugs:
            log.info(f"Hết phim ở trang {page}.")
            break
        all_slugs.extend(slugs)
        time.sleep(DELAY)

    log.info(f"\nTổng: {len(all_slugs)} phim — crawl chi tiết...\n")

    items = []
    for i, slug in enumerate(all_slugs, 1):
        log.info(f"[{i}/{len(all_slugs)}] {slug}")
        film = scrape_film(slug)
        if film and film.streams:
            items.append(film)
        time.sleep(DELAY)

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

    log.info("\n" + "═" * 55)
    log.info(f"  Xong! {len(items)} phim → {OUTPUT_FILE}")
    log.info("═" * 55)


if __name__ == "__main__":
    main()
