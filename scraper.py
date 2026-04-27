#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, re, json, logging
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

CONFIG = {
    "base_url": "https://yanhh3d.bz",
    "output_file": os.getenv("OUTPUT_PATH", "yanhh3d.json"),
    "items_per_page": 50,
    "timeout": 20
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

def get_session():
    s = requests.Session()
    # Tăng số lần thử lại nếu web phản hồi chậm
    retries = Retry(total=5, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://yanhh3d.bz/"
    })
    return s

def slugify(text):
    if not text: return "unknown"
    text = text.lower()
    char_map = {'a': '[àáạảãâầấậẩẫăằắặẳẵ]', 'e': '[èéẹẻẽêềếệểễ]', 'i': '[ìíịỉĩ]', 'o': '[òóọỏõôồốộổỗơờớợởỡ]', 'u': '[ùúụủũưừứựửữ]', 'y': '[ỳýỵỷỹ]', 'd': '[đ]'}
    for char, pattern in char_map.items(): text = re.sub(pattern, char, text)
    return re.sub(r'[^a-z0-9]+', '-', text).strip('-')

class YanHH3DScraper:
    def __init__(self):
        self.session = get_session()
        self.base = CONFIG["base_url"].rstrip("/")

    def get_movies(self, path):
        url = urljoin(self.base, path)
        logger.info(f"🔍 Đang truy cập: {url}")
        try:
            r = self.session.get(url, timeout=CONFIG["timeout"])
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            logger.error(f"❌ Không thể truy cập {url}: {e}")
            return []

        items = []
        # Selector chính xác cho yanhh3d: .flw-item là các card phim
        cards = soup.select(".flw-item, .film-item, .movie-item, .item")
        
        for card in cards[:CONFIG["items_per_page"]]:
            # 1. Tìm link và tiêu đề (Ưu tiên thẻ a bên trong .film-poster hoặc .film-name)
            a_tag = card.select_one(".film-poster a, .film-name a, a[href*='/phim/']")
            if not a_tag: continue
            
            title = a_tag.get("title") or a_tag.get_text(strip=True)
            href = a_tag.get("href")
            
            # 2. Tìm ảnh (Xử lý Lazy Load bằng cách check nhiều attribute)
            img_tag = card.select_one("img")
            img_url = ""
            if img_tag:
                img_url = img_tag.get("data-src") or img_tag.get("src") or img_tag.get("data-original")
            
            # 3. Tìm nhãn trạng thái (Số tập)
            label_tag = card.select_one(".fd-infor .fdi-item, .tick-sub, .tick-eps, .label")
            label = label_tag.get_text(strip=True) if label_tag else "HD"

            if title and href:
                items.append({
                    "id": slugify(title),
                    "name": title,
                    "image": urljoin(self.base, img_url) if img_url else "",
                    "description": f"Trạng thái: {label}",
                    "label": label,
                    "remote_data": {"url": urljoin(self.base, href)}
                })
        
        logger.info(f"✅ Tìm thấy {len(items)} phim tại {path}")
        return items

    def run(self):
        # Khởi tạo data
        data = {
            "id": "yanhh3d",
            "name": "YanHH3D Hoạt Hình",
            "categories": []
        }

        # Định nghĩa các mục cần lấy
        sections = [
            ("🔥 Mới cập nhật", "/"),
            ("🎭 Hoạt Hình 3D", "/the-loai/hoat-hinh-3d"),
            ("🌟 Hoàn thành", "/trang-thai/hoan-thanh")
        ]

        for name, path in sections:
            movies = self.get_movies(path)
            data["categories"].append({
                "name": name,
                "channels": movies
            })

        # Ghi file
        with open(CONFIG["output_file"], "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"🚀 Hoàn tất! File {CONFIG['output_file']} đã sẵn sàng.")

if __name__ == "__main__":
    YanHH3DScraper().run()
