#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, re, json, logging
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# Cấu hình hệ thống
CONFIG = {
    "base_url": os.getenv("YANH_BASE_URL", "https://yanhh3d.bz"),
    "output_file": os.getenv("OUTPUT_PATH", "yanhh3d.json"),
    "items_per_page": 60,
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
    text = text.lower()
    # Xử lý tiếng Việt chuẩn để làm ID
    char_map = {
        'a': '[àáạảãâầấậẩẫăằắặẳẵ]', 'e': '[èéẹẻẽêềếệểễ]', 'i': '[ìíịỉĩ]',
        'o': '[òóọỏõôồốộổỗơờớợởỡ]', 'u': '[ùúụủũưừứựửữ]', 'y': '[ỳýỵỷỹ]', 'd': '[đ]'
    }
    for char, pattern in char_map.items():
        text = re.sub(pattern, char, text)
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-') or 'movie'

def parse_label(text):
    if not text: return "HD"
    # Lấy số tập (Ví dụ: "Tập 12/24" -> "12/24")
    m = re.search(r'(\d+/\d+|\d+)', text)
    return m.group(1) if m else text.strip()

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

    def get_movies(self, path):
        url = urljoin(self.base, path)
        soup = self.fetch(url)
        if not soup: return []

        items = []
        # Selector linh hoạt cho nhiều giao diện
        cards = soup.select("div.film-item, div.movie-item, .list-films .item, .film_list > div")
        
        for card in cards[:CONFIG["items_per_page"]]:
            a = card.find("a", href=True)
            if not a: continue

            title = a.get("title") or a.find(["h3", "h2", "span"]).get_text(strip=True) if a.find(["h3", "h2", "span"]) else "No Name"
            detail_url = urljoin(self.base, a["href"])
            
            img_tag = card.find("img")
            img_url = ""
            if img_tag:
                img_url = img_tag.get("data-src") or img_tag.get("src") or img_tag.get("data-original")
            
            # Fix URL ảnh
            if img_url and img_url.startswith("//"): img_url = "https:" + img_url
            elif img_url and img_url.startswith("/"): img_url = urljoin(self.base, img_url)

            # Lấy trạng thái tập phim
            lbl = card.select_one(".label, .episode, .status, .film-status")
            status = parse_label(lbl.get_text(strip=True)) if lbl else "Full"

            items.append({
                "id": slugify(title),
                "name": title,
                "image": img_url or f"{self.base}/thumb.jpg",
                "description": f"Trạng thái: {status}",
                "type": "series",
                "label": status,
                "remote_data": {"url": detail_url}
            })
        return items

    def run(self):
        logger.info("🚀 Đang cào dữ liệu YanHH3D...")
        
        # Cấu trúc cho App Media Player
        data = {
            "id": "yanhh3d",
            "name": "YanHH3D - Hoạt Hình",
            "description": "Kho hoạt hình 3D Trung Quốc thuyết minh chất lượng cao.",
            "image": "https://yanhh3d.bz/favicon.ico",
            "categories": [
                {
                    "name": "🔥 Mới cập nhật",
                    "channels": self.get_movies("/danh-sach/phim-moi")
                },
                {
                    "name": "🎭 Hoạt Hình 3D",
                    "channels": self.get_movies("/the-loai/hoat-hinh-3d")
                },
                {
                    "name": "🌟 Phim Bộ Hoàn Thành",
                    "channels": self.get_movies("/trang-thai/hoan-thanh")
                }
            ]
        }

        # Lưu file
        with open(CONFIG["output_file"], "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        total = sum(len(cat["channels"]) for cat in data["categories"])
        logger.info(f"✅ Đã xuất {total} phim vào file {CONFIG['output_file']}")

if __name__ == "__main__":
    YanHH3DScraper().run()
