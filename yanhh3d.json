#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, re, json, time, logging
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
    text = re.sub(r'[àáạảãâầấậẩẫăằắặẳẵ]', 'a', text.lower())
    text = re.sub(r'[èéẹẻẽêềếệểễ]', 'e', text)
    text = re.sub(r'[ìíịỉĩ]', 'i', text)
    text = re.sub(r'[òóọỏõôồốộổỗơờớợởỡ]', 'o', text)
    text = re.sub(r'[ùúụủũưừứựửữ]', 'u', text)
    text = re.sub(r'[ỳýỵỷỹ]', 'y', text)
    text = re.sub(r'[đ]', 'd', text)
    return re.sub(r'[^a-z0-9]+', '-', text.strip('-')) or 'movie'

def parse_label(text):
    m = re.search(r'(?:Tập\s*)?(\d+)(?:\s*/\s*(\d+))?', text or "")
    if m:
        return f"{m.group(1)}/{m.group(2)} [4K]" if m.group(2) else f"Tập {m.group(1)}"
    return "Full"

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
        # Selector phổ biến cho card phim trên các site hoạt hình VN
        cards = soup.select("div.film-item, div.movie-item, li.item, div.col-md-2, div.col-xs-4")
        if not cards:
            # Fallback: tìm tất cả link có href chứa /phim/ hoặc /detail/
            links = soup.select("a[href*='/phim/'], a[href*='/detail/'], a[href*='/xem/']")
            for a in links[:CONFIG["items_per_page"]]:
                title = a.get("title") or a.get_text(strip=True)
                if len(title) < 3: continue
                items.append({
                    "id": slugify(title),
                    "name": title,
                    "description": title,
                    "image": {"url": f"{self.base}/placeholder.jpg", "type": "cover", "width": 480, "height": 640},
                    "type": "series",
                    "display": "text-below",
                    "label": {"text": "Mới", "position": "top-left", "color": "#35ba8b", "text_color": "#ffffff"},
                    "remote_data": {"url": urljoin(self.base, a["href"])},
                    "enable_detail": True
                })
            return items

        for card in cards[:CONFIG["items_per_page"]]:
            a = card.find("a", href=True)
            img = card.find("img")
            if not a: continue
            
            title = a.get("title") or (a.find(["h3","span"]) and a.find(["h3","span
