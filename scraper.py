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
    "items_per_page": 60,
    "timeout": 30
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

def get_session():
    s = requests.Session()
    s.mount("https://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1)))
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "vi-VN,vi;q=0.9"
    })
    return s

def slugify(text):
    if not text: return "unknown"
    text = text.lower()
    char_map = {
        'a': '[àáạảãâầấậẩẫăằắặẳẵ]', 'e': '[èéẹẻẽêềếệểễ]', 'i': '[ìíịỉĩ]',
        'o': '[òóọỏõôồốộổỗơờớợởỡ]', 'u': '[ùúụủũưừứựửữ]', 'y': '[ỳýỵỷỹ]', 'd': '[đ]'
    }
    for char, pattern in char_map.items():
        text = re.sub(pattern, char, text)
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-') or 'movie'

class YanHH3DScraper:
    def __init__(self):
        self.session = get_session()
        self.base = CONFIG["base_url"].rstrip("/")

    def fetch(self, url):
        try:
            r = self.session.get(url, timeout=CONFIG["timeout"])
            r.raise_for_status()
            return BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            logger.error(f"❌ Lỗi tải trang {url}: {e}")
            return None

    def get_movies(self, path):
        url = urljoin(self.base, path)
        soup = self.fetch(url)
        if not soup: return []

        items = []
        # Selector cập nhật cho yanhh3d: Thường là .flw-item hoặc .film-item
        cards = soup.select(".flw-item, .film-item, .list-films .item, .film_list > div")
        
        for card in cards[:CONFIG["items_per_page"]]:
            # Tìm link phim
            a = card.select_one("a[href*='/phim/']")
            if not a: continue

            title = a.get("title") or card.select_one(".film-name, .title, h3").get_text(strip=True)
            detail_url = urljoin(self.base, a["href"])
            
            # Tìm ảnh
            img_tag = card.find("img")
            img_url = ""
            if img_tag:
                img_url = img_tag.get("data-src") or img_tag.get("src") or img_tag.get("data-original")
            
            if img_url and img_url.startswith("//"): img_url = "https:" + img_url
            elif img_url and img_url.startswith("/"): img_url = urljoin(self.base, img_url)

            # Tìm nhãn (tập phim)
            lbl = card.select_one(".status, .ep-status, .tick-item, .label")
            status = lbl.get_text(strip=True) if lbl else "HD"

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
        logger.info("🚀 Bắt đầu cào dữ liệu...")
        
        # Luôn khởi tạo cấu trúc dữ liệu cơ bản
        data = {
            "id": "yanhh3d",
            "name": "YanHH3D - Hoạt Hình",
            "description": "Cập nhật: " + os.popen('date').read().strip(),
            "image": "https://yanhh3d.bz/favicon.ico",
            "categories": []
        }

        # Danh sách các mục cần cào
        targets = [
            ("🔥 Mới cập nhật", "/"),
            ("🎭 Hoạt Hình 3D", "/the-loai/hoat-hinh-3d"),
            ("🌟 Phim Bộ Hoàn Thành", "/trang-thai/hoan-thanh")
        ]

        for name, path in targets:
            logger.info(f"🔎 Đang lấy mục: {name}")
            movies = self.get_movies(path)
            data["categories"].append({
                "name": name,
                "channels": movies
            })

        # Ghi file JSON bất kể có phim hay không để tránh lỗi workflow
        with open(CONFIG["output_file"], "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        total = sum(len(cat["channels"]) for cat in data["categories"])
        logger.info(f"💾 Đã lưu {total} phim vào {CONFIG['output_file']}")

if __name__ == "__main__":
    YanHH3DScraper().run()
