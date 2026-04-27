"""
scraper.py — Crawl yanhh3d.bz → output MonPlayer JSON
Hỗ trợ cả phim lẻ (1 link) và phim bộ (nhiều tập).
"""

import json
import re
import time
import logging
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional

import requests
from bs4 import BeautifulSoup

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────
BASE_URL = "https://yanhh3d.bz"
OUTPUT_FILE = "monplayer.json"
MAX_PAGES = 10          # số trang danh sách phim tối đa
DELAY = 1.5             # giây giữa mỗi request (tránh bị ban)
TIMEOUT = 15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": BASE_URL,
    "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Data models
# ──────────────────────────────────────────────
@dataclass
class Stream:
    name: str
    url: str


@dataclass
class MovieItem:
    title: str
    image: str
    description: str = ""
    streams: list[Stream] = field(default_factory=list)


@dataclass
class MonPlayerSource:
    name: str = "YanHH3D — yanhh3d.bz"
    items: list[MovieItem] = field(default_factory=list)


# ──────────────────────────────────────────────
# HTTP helper
# ──────────────────────────────────────────────
session = requests.Session()
session.headers.update(HEADERS)


def get(url: str, retries: int = 3) -> Optional[BeautifulSoup]:
    for attempt in range(retries):
        try:
            r = session.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            return BeautifulSoup(r.text, "html.parser")
        except requests.RequestException as e:
            log.warning(f"[attempt {attempt+1}] GET {url} → {e}")
            time.sleep(DELAY * (attempt + 1))
    log.error(f"Bỏ qua URL sau {retries} lần thử: {url}")
    return None


# ──────────────────────────────────────────────
# Step 1: Lấy danh sách link phim từ trang listing
# ──────────────────────────────────────────────
def get_movie_links(page: int = 1) -> list[str]:
    """Crawl trang danh sách, trả về list URL chi tiết phim."""
    # Thử các pattern URL phổ biến của site phim VN
    candidates = [
        f"{BASE_URL}/phim-moi/page/{page}",
        f"{BASE_URL}/page/{page}",
        f"{BASE_URL}/?page={page}",
    ]

    for url in candidates:
        soup = get(url)
        if soup is None:
            continue

        links: list[str] = []

        # Pattern 1: thẻ <a> trong grid/list phim
        for a in soup.select("div.film-item a, div.movie-item a, article a, .item a"):
            href = a.get("href", "")
            if href and href.startswith("/") and len(href) > 2:
                links.append(BASE_URL + href)
            elif href.startswith(BASE_URL):
                links.append(href)

        # Deduplicate, giữ thứ tự
        seen: set[str] = set()
        unique: list[str] = []
        for lnk in links:
            if lnk not in seen and lnk != BASE_URL + "/":
                seen.add(lnk)
                unique.append(lnk)

        if unique:
            log.info(f"  Trang {page}: tìm thấy {len(unique)} phim ({url})")
            return unique

    log.warning(f"Không tìm thấy phim ở trang {page}")
    return []


# ──────────────────────────────────────────────
# Step 2: Extract m3u8 / mp4 từ trang phim
# ──────────────────────────────────────────────
def extract_streams(soup: BeautifulSoup, page_url: str) -> list[Stream]:
    streams: list[Stream] = []
    html = str(soup)

    # --- Tìm trực tiếp trong HTML/JS ---
    # Pattern m3u8
    m3u8_urls = re.findall(
        r'https?://[^\s\'"<>]+\.m3u8(?:\?[^\s\'"<>]*)?', html
    )
    # Pattern mp4
    mp4_urls = re.findall(
        r'https?://[^\s\'"<>]+\.mp4(?:\?[^\s\'"<>]*)?', html
    )

    seen: set[str] = set()

    for i, url in enumerate(m3u8_urls):
        if url not in seen:
            seen.add(url)
            streams.append(Stream(name=f"Server {i+1} (HLS)", url=url))

    for i, url in enumerate(mp4_urls):
        if url not in seen:
            seen.add(url)
            streams.append(Stream(name=f"MP4 {i+1}", url=url))

    # --- Tìm iframe embed (player ngoài) ---
    if not streams:
        for iframe in soup.find_all("iframe"):
            src = iframe.get("src", "") or iframe.get("data-src", "")
            if src and ("player" in src or "embed" in src or "watch" in src):
                streams.append(Stream(name="Embed Player", url=src))

    # --- Tìm trong thẻ <source> ---
    for source in soup.find_all("source"):
        src = source.get("src", "")
        if src and src not in seen:
            seen.add(src)
            label = source.get("label", source.get("size", "Video"))
            streams.append(Stream(name=str(label), url=src))

    return streams


# ──────────────────────────────────────────────
# Step 3: Lấy thông tin chi tiết 1 phim
# ──────────────────────────────────────────────
def scrape_movie(url: str) -> Optional[MovieItem]:
    soup = get(url)
    if soup is None:
        return None

    # --- Title ---
    title = ""
    for selector in ["h1.film-title", "h1.title", "h1", ".movie-title", "title"]:
        tag = soup.select_one(selector)
        if tag:
            title = tag.get_text(strip=True)
            # Bỏ suffix tên site
            title = re.sub(r"\s*[–|-]\s*yanhh3d.*$", "", title, flags=re.I)
            title = re.sub(r"\s*\|\s*yanhh3d.*$", "", title, flags=re.I)
            if title:
                break
    if not title:
        title = url.split("/")[-1].replace("-", " ").title()

    # --- Thumbnail ---
    image = ""
    for selector in [
        "meta[property='og:image']",
        ".film-poster img",
        ".movie-poster img",
        "img.poster",
        "img.thumbnail",
    ]:
        tag = soup.select_one(selector)
        if tag:
            image = tag.get("content") or tag.get("src") or ""
            if image:
                if image.startswith("/"):
                    image = BASE_URL + image
                break

    # --- Description ---
    desc = ""
    for selector in [
        "meta[name='description']",
        "meta[property='og:description']",
        ".film-content",
        ".description",
        ".overview",
    ]:
        tag = soup.select_one(selector)
        if tag:
            desc = tag.get("content") or tag.get_text(strip=True)
            if desc:
                desc = desc[:200]
                break

    # --- Streams ---
    # Phim bộ: tìm danh sách tập
    episodes = soup.select("ul.list-episode a, .episodes a, .ep-item a, a.episode-link")

    if episodes:
        # Phim bộ — crawl từng tập
        streams: list[Stream] = []
        for ep in episodes:
            ep_name = ep.get_text(strip=True) or "Tập"
            ep_url = ep.get("href", "")
            if ep_url:
                if ep_url.startswith("/"):
                    ep_url = BASE_URL + ep_url
                # Lấy stream từ trang tập
                ep_soup = get(ep_url)
                time.sleep(DELAY)
                if ep_soup:
                    ep_streams = extract_streams(ep_soup, ep_url)
                    if ep_streams:
                        streams.append(Stream(name=ep_name, url=ep_streams[0].url))
                    else:
                        streams.append(Stream(name=ep_name, url=ep_url))
    else:
        # Phim lẻ — lấy stream từ trang hiện tại
        streams = extract_streams(soup, url)
        if not streams:
            # Fallback: dùng URL trang phim
            streams = [Stream(name="Xem phim", url=url)]

    log.info(f"  ✓ {title[:60]} — {len(streams)} stream(s)")
    return MovieItem(title=title, image=image, description=desc, streams=streams)


# ──────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────
def main():
    log.info("═" * 50)
    log.info("  YanHH3D → MonPlayer JSON Scraper")
    log.info("═" * 50)

    source = MonPlayerSource()

    for page in range(1, MAX_PAGES + 1):
        log.info(f"\n[Trang {page}/{MAX_PAGES}]")
        movie_links = get_movie_links(page)

        if not movie_links:
            log.info("Hết phim, dừng lại.")
            break

        for link in movie_links:
            movie = scrape_movie(link)
            if movie and movie.streams:
                source.items.append(movie)
            time.sleep(DELAY)

    # Xuất JSON
    output = {
        "name": source.name,
        "items": [
            {
                "title": item.title,
                "image": item.image,
                "description": item.description,
                "streams": [asdict(s) for s in item.streams],
            }
            for item in source.items
        ],
    }

    out_path = Path(OUTPUT_FILE)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info("\n" + "═" * 50)
    log.info(f"  Xong! {len(source.items)} phim → {OUTPUT_FILE}")
    log.info("═" * 50)


if __name__ == "__main__":
    main()
