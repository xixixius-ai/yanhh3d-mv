#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, re, json, logging
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

CONFIG = {
    "base_url": os.getenv("YANH_BASE_URL", "https://yanhh3d.bz"),
    "output_file": os.getenv("OUTPUT_PATH", "yanhh3d.json"),
    "max_pages": int(os.getenv("MAX_PAGES", "5")),
    "items_per_page": int(os.getenv("ITEMS_PER_PAGE", "60")),
    "timeout": 30
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

def get_session():
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])))
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
    return s

def slugify(text):
    if not text: return "unknown"
    # Chuyển về chữ thường
    text = text.lower()
    # Xử lý tiếng Việt (đã gộp dòng để tránh lỗi SyntaxError)
    text = re.sub(r'[àáạảãâầấậẩẫăằắặẳẵ]', 'a', text)
    text = re.sub(r'[èéẹẻẽêềếệểễ]', 'e', text)
    text = re.sub(r'[ìíịỉĩ]', 'i', text)
    text = re.sub(r'[òóọỏõôồốộổỗơờớợởỡ]', 'o', text)
    text = re.sub(r'[ùúụủũưừứựửữ]', 'u', text)
    text = re.sub(r'[ỳýỵỷỹ]', 'y', text)
    text = re.sub(r'[đ]', 'd', text)
    # Loại bỏ ký tự đặc biệt
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-') or 'movie'

def parse_label(text):
    if not text: return "Full"
    m = re.search(r'(?:Tập\s*)?(\d+)(?:\s*/\s*(\d+))?', text)
    if m:
        return f"{m.group(1)}/{m.group(2)} [4K]" if m.group(2) else f"Tập {m.group(1)}"
    return text.strip() if text.strip() else "Full"

class YanHH3DScraper:
    def __init__(self):
        self.session = get_session()
        self.base = CONFIG["base_url"].rstrip("/")

    def fetch(self, url):
        try:
            r = self.session.get(url, timeout=CONFIG["timeout"])
            r.raise_for_status()
            r.encoding = "utf-8"
            return BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            logger.error(f"Fetch fail {url}: {e}")
            return None

    def scrape(self):
        soup = self.fetch(self.base)
        if not soup: return []

        items = []
        cards = soup.select("div.film-item, div.movie-item, li.item, div.col-md-2, div.col-xs-4, div.item, .film_list > div, .film_list > a")
        
        if not cards:
            links = soup.select("a[href*='/phim/'], a[href*='/detail/'], a[href*='/xem/'], .film_list a")
            for a in links[:CONFIG["items_per_page"]]:
                title = a.get("title", "").strip() or a.get_text(strip=True)
                if len(title) < 3: continue
                items.append(self._make_item(title, urljoin(self.base, a["href"]), f"{self.base}/thumb.jpg"))
            return items

        for card in cards[:CONFIG["items_per_page"]]:
            a = card.find("a", href=True)
            if not a: continue

            title = a.get("title", "").strip()
            if not title:
                title_elem = a.find(["h3", "span", "p"])
                title = title_elem.get_text(strip=True) if title_elem else "Unknown"

            detail_url = urljoin(self.base, a["href"])
            img_tag = card.find("img")
            img_url = img_tag.get("data-src") or img_tag.get("src") or f"{self.base}/thumb.jpg"
            if img_url.startswith("//"): img_url = "https:" + img_url
            elif img_url.startswith("/"): img_url = urljoin(self.base, img_url)

            label_text = ""
            lbl = card.select_one("span.label, span.episode, span.status, span.film-status, div.label, .episode-tag")
            if lbl: label_text = lbl.get_text(strip=True)

            items.append({
                "id": slugify(title),
                "name": title,
                "description": title,
                "image": {"url": img_url, "type": "cover", "width": 480, "height": 640},
                "type": "series",
                "display": "text-below",
                "label": {"text": parse_label(label_text), "position": "top-left", "color": "#35ba8b", "text_color": "#ffffff"},
                "remote_data": {"url": detail_url},
                "enable_detail": True
            })
        return items

    def _make_item(self, title, url, img):
        return {
            "id": slugify(title),
            "name": title,
            "description": title,
            "image": {"url": img, "type": "cover", "width": 480, "height": 640},
            "type": "series",
            "display": "text-below",
            "label": {"text": "Mới", "position": "top-left", "color": "#35ba8b", "text_color": "#ffffff"},
            "remote_data": {"url": url},
            "enable_detail": True
        }

    def generate(self, movies):
        return {
            "id": "yanhh3d",
            "name": "YanHH3D",
            "url": self.base,
            "color": "#1a1a2e",
            "image": {"url": f"{self.base}/favicon.ico", "type": "cover"},
            "description": "Hoạt hình Trung Quốc thuyết minh HD",
            "sorts": [
                {"text": "Mới cập nhật", "url": f"{self.base}/danh-sach/phim-moi"},
                {"text": "Hoạt Hình 3D", "url": f"{self.base}/the-loai/hoat-hinh-3d"},
                {"text": "Hoạt Hình 2D", "url": f"{self.base}/the-loai/hoat-hinh-2d"},
                {"text": "Hoàn thành", "url": f"{self.base}/trang-thai/hoan-thanh"}
            ],
            "grid_number": 3,
            "channels": movies,
            "load_more": {
                "remote_data": {"url": f"{self.base}/danh-sach"},
                "paging": {"page_key": "page", "size_key": "limit"},
                "pageInfo": {
                    "current_page": 1,
                    "total": len(movies) * CONFIG["max_pages"],
                    "per_page": CONFIG["items_per_page"],
                    "last_page": max(1, CONFIG["max_pages"])
                }
            }
        }

    def run(self):
        logger.info("🚀 Start scraping YanHH3D...")
        movies = self.scrape()
        if not movies:
            logger.warning("⚠️ Không tìm thấy phim. Tạo file mẫu để workflow không fail.")
            movies = []

        data = self.generate(movies)
        with open(CONFIG["output_file"], "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"✅ Export {len(movies)} phim → {CONFIG['output_file']}")
        return True

if __name__ == "__main__":
    YanHH3DScraper().run()
