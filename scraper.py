#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🎬 YanHH3D Scraper - Tạo file yanhh3d.json theo chuẩn OPhim
Author: Auto-generated
Date: 2026
"""

import os
import re
import json
import time
import logging
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ─────────────────────────────────────────────────────────────
# ⚙️ Cấu hình
# ─────────────────────────────────────────────────────────────
CONFIG = {
    "base_url": os.getenv("YANH_BASE_URL", "https://yanhh3d.bz"),
    "output_file": os.getenv("OUTPUT_PATH", "yanhh3d.json"),
    "max_pages": int(os.getenv("MAX_PAGES", "5")),
    "items_per_page": int(os.getenv("ITEMS_PER_PAGE", "60")),
    "request_timeout": 30,
    "retry_attempts": 3,
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────
# 🔧 Utility Functions
# ─────────────────────────────────────────────────────────────
def create_session():
    """Tạo requests session với retry & headers"""
    session = requests.Session()
    retry = Retry(
        total=CONFIG["retry_attempts"],
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": CONFIG["user_agent"],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
    })
    return session


def slugify(text: str) -> str:
    """Chuyển text thành URL-friendly slug"""
    text = text.lower().strip()
    text = re.sub(r'[àáạảãâầấậẩẫăằắặẳẵ]', 'a', text)
    text = re.sub(r'[èéẹẻẽêềếệểễ]', 'e', text)
    text = re.sub(r'[ìíịỉĩ]', 'i', text)
    text = re.sub(r'[òóọỏõôồốộổỗơờớợởỡ]', 'o', text)
    text = re.sub(r'[ùúụủũưừứựửữ]', 'u', text)
    text = re.sub(r'[ỳýỵỷỹ]', 'y', text)
    text = re.sub(r'[đ]', 'd', text)
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s-]+', '-', text)
    return text.strip('-')


def extract_episode_label(title: str, extra_text: str = "") -> str:
    """Tạo label tập phim từ metadata"""
    # Ưu tiên lấy từ extra_text (ví dụ: "Tập 12", "138/180")
    if extra_text:
        match = re.search(r'(?:Tập\s*)?(\d+)(?:/(\d+))?', extra_text, re.I)
        if match:
            current = match.group(1)
            total = match.group(2)
            if total:
                return f"{current}/{total} [4K]"
            return f"Tập {current}"
    
    # Fallback: parse từ title
    match = re.search(r'(?:Tập\s*)?(\d+)(?:\s*[/-]\s*(\d+))?', title, re.I)
    if match:
        return f"Tập {match.group(1)}"
    
    return "Full"


def get_image_dimensions(url: str) -> tuple[int, int]:
    """Trả về kích thước ảnh chuẩn OPhim (480x640)"""
    # YanHH3D có thể không cung cấp dimensions → dùng default
    return 480, 640


# ─────────────────────────────────────────────────────────────
# 🕷️ Scraper Core
# ─────────────────────────────────────────────────────────────
class YanHH3DScraper:
    def __init__(self):
        self.session = create_session()
        self.base_url = CONFIG["base_url"].rstrip('/')
        
    def fetch_page(self, url: str) -> BeautifulSoup | None:
        """Fetch và parse HTML page"""
        try:
            logger.info(f"📥 Fetching: {url}")
            resp = self.session.get(url, timeout=CONFIG["request_timeout"])
            resp.raise_for_status()
            # Kiểm tra encoding
            if 'charset' not in resp.headers.get('content-type', '').lower():
                resp.encoding = 'utf-8'
            return BeautifulSoup(resp.text, 'html.parser')
        except requests.RequestException as e:
            logger.error(f"❌ Failed to fetch {url}: {e}")
            return None
    
    def parse_movie_card(self, card) -> dict | None:
        """Parse một card phim từ HTML"""
        try:
            # Extract link & ID
            link_tag = card.find('a', href=True)
            if not link_tag:
                return None
            detail_url = urljoin(self.base_url, link_tag['href'])
            movie_id = slugify(link_tag.get('title', '') or link_tag.get_text(strip=True))
            
            # Extract image
            img_tag = card.find('img')
            if not img_tag:
                return None
            img_url = img_tag.get('data-src') or img_tag.get('src') or ''
            if img_url.startswith('//'):
                img_url = 'https:' + img_url
            elif img_url.startswith('/'):
                img_url = urljoin(self.base_url, img_url)
            
            # Extract title & description
            title = link_tag.get('title', '').strip()
            if not title:
                title = link_tag.find('h3') or link_tag.find('span', class_='title')
                title = title.get_text(strip=True) if title else 'Unknown'
            
            # Try to find original name/English description
            desc_tag = card.find('span', class_='original') or card.find('div', class_='desc')
            description = desc_tag.get_text(strip=True) if desc_tag else title
            
            # Extract episode/label info
            label_tag = card.find('span', class_='label') or card.find('span', class_='episode') or card.find('span', class_='status')
            label_text = label_tag.get_text(strip=True) if label_tag else ""
            
            # Build label object
            episode_label = extract_episode_label(title, label_text)
            is_completed = 'full' in label_text.lower() or 'hoàn thành' in label_text.lower() or 'full' in episode_label.lower()
            
            label_obj = {
                "text": episode_label,
                "position": "top-left",
                "color": "#35ba8b" if not is_completed else "#6c757d",
                "text_color": "#ffffff"
            }
            
            width, height = get_image_dimensions(img_url)
            
            return {
                "id": movie_id,
                "name": title,
                "description": description,
                "image": {
                    "url": img_url,
                    "type": "cover",
                    "width": width,
                    "height": height
                },
                "type": "playlist",
                "display": "text-below",
                "label": label_obj,
                "remote_data": {"url": detail_url},
                "enable_detail": True
            }
        except Exception as e:
            logger.warning(f"⚠️ Failed to parse card: {e}")
            return None
    
    def scrape_homepage(self) -> list[dict]:
        """Scrape danh sách phim từ trang chủ"""
        soup = self.fetch_page(self.base_url)
        if not soup:
            return []
        
        movies = []
        # Các selector phổ biến cho movie cards (tùy site, cần điều chỉnh)
        selectors = [
            ('div', {'class': 'film-item'}),
            ('div', {'class': 'movie-item'}),
            ('li', {'class': re.compile(r'item|film|movie')}),
            ('div', {'id': re.compile(r'list|grid|content')}),
        ]
        
        for tag_name, attrs in selectors:
            cards = soup.find_all(tag_name, attrs=attrs)
            if cards:
                logger.info(f"🎯 Found {len(cards)} cards with selector: {tag_name}{attrs}")
                for card in cards:
                    movie = self.parse_movie_card(card)
                    if movie and movie['image']['url']:
                        movies.append(movie)
                if movies:
                    break
        
        logger.info(f"✅ Parsed {len(movies)} movies from homepage")
        return movies[:CONFIG["items_per_page"]]
    
    def build_sorts_section(self) -> list[dict]:
        """Xây dựng phần sorts/filters"""
        base = self.base_url
        return [
            {
                "text": "Mới cập nhật",
                "type": "radio",
                "url": f"{base}/api/sort/newest"
            },
            {
                "text": "Loại phim",
                "type": "dropdown",
                "value": [
                    {"text": "Hoạt Hình 3D", "type": "radio", "url": f"{base}/api/type/3d"},
                    {"text": "Hoạt Hình 2D", "type": "radio", "url": f"{base}/api/type/2d"},
                    {"text": "Hoạt Hình 4K", "type": "radio", "url": f"{base}/api/type/4k"},
                    {"text": "Đã hoàn thành", "type": "radio", "url": f"{base}/api/type/completed"},
                    {"text": "Đang chiếu", "type": "radio", "url": f"{base}/api/type/ongoing"},
                    {"text": "Phim lẻ | OVA", "type": "radio", "url": f"{base}/api/type/ova"}
                ]
            },
            {
                "text": "Thể loại",
                "type": "dropdown",
                "value": [
                    {"text": "Huyền Huyễn", "url": f"{base}/api/genre/huyen-huyen"},
                    {"text": "Xuyên Không", "url": f"{base}/api/genre/xuyen-khong"},
                    {"text": "Trùng Sinh", "url": f"{base}/api/genre/trung-sinh"},
                    {"text": "Tiên Hiệp", "url": f"{base}/api/genre/tien-hiep"},
                    {"text": "Cổ Trang", "url": f"{base}/api/genre/co-trang"},
                    {"text": "Hài Hước", "url": f"{base}/api/genre/hai-huoc"},
                    {"text": "Kiếm Hiệp", "url": f"{base}/api/genre/kiem-hiep"},
                    {"text": "Hiện Đại", "url": f"{base}/api/genre/hien-dai"}
                ]
            }
        ]
    
    def generate_output(self, movies: list[dict]) -> dict:
        """Tạo JSON output theo chuẩn OPhim"""
        total_estimated = len(movies) * CONFIG["max_pages"]
        
        return {
            "id": "yanhh3d",
            "name": "YanHH3D - Hoạt Hình Trung Quốc",
            "url": self.base_url,
            "color": "#1a1a2e",
            "image": {
                "url": f"{self.base_url}/logo.png",
                "type": "cover"
            },
            "description": "Tuyển tập phim hoạt hình Trung Quốc mới nhất, thuyết minh chất lượng cao, cập nhật hàng ngày.",
            "share": {"url": self.base_url},
            "notice": {
                "closeable": True,
                "icon": f"{self.base_url}/notice-icon.png",
                "id": "notice-yanhh3d",
                "link": "https://facebook.com/yanhh3d.net",
                "text": "Cập nhật tên miền mới: YanHH3D.bz"
            },
            "sorts": self.build_sorts_section(),
            "grid_number": 3,
            "channels": movies,
            "load_more": {
                "remote_data": {
                    "url": f"{self.base_url}/api/movies",
                    "external": False
                },
                "paging": {
                    "page_key": "page",
                    "size_key": "limit"
                },
                "pageInfo": {
                    "current_page": 1,
                    "total": total_estimated,
                    "per_page": CONFIG["items_per_page"],
                    "last_page": CONFIG["max_pages"]
                }
            },
            "remote_data": {
                "url": f"{self.base_url}/api/movies",
                "external": False
            },
            "_meta": {
                "generated_at": datetime.utcnow().isoformat() + "Z",
                "source": CONFIG["base_url"],
                "scraper_version": "1.0.0"
            }
        }
    
    def run(self):
        """Main execution"""
        logger.info(f"🚀 Starting YanHH3D Scraper v1.0")
        logger.info(f"📍 Base URL: {self.base_url}")
        
        # Scrape movies
        movies = self.scrape_homepage()
        if not movies:
            logger.warning("⚠️ No movies found. Trying fallback selectors...")
            # Fallback: try generic link extraction
            soup = self.fetch_page(self.base_url)
            if soup:
                links = soup.find_all('a', href=re.compile(r'/phim|/detail|/movie'))
                for link in links[:CONFIG["items_per_page"]]:
                    title = link.get('title') or link.get_text(strip=True)
                    if title and len(title) > 3:
                        movies.append({
                            "id": slugify(title),
                            "name": title,
                            "description": title,
                            "image": {
                                "url": f"{self.base_url}/placeholder.jpg",
                                "type": "cover",
                                "width": 480,
                                "height": 640
                            },
                            "type": "playlist",
                            "display": "text-below",
                            "label": {"text": "New", "position": "top-left", "color": "#35ba8b", "text_color": "#ffffff"},
                            "remote_data": {"url": urljoin(self.base_url, link['href'])},
                            "enable_detail": True
                        })
        
        if not movies:
            logger.error("❌ Failed to scrape any movies. Please check selectors.")
            return False
        
        # Generate & save output
        output = self.generate_output(movies)
        
        os.makedirs(os.path.dirname(CONFIG["output_file"]) or ".", exist_ok=True)
        with open(CONFIG["output_file"], 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✅ Saved {len(movies)} movies to {CONFIG['output_file']}")
        return True


# ─────────────────────────────────────────────────────────────
# 🎬 Main Entry Point
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    scraper = YanHH3DScraper()
    success = scraper.run()
    exit(0 if success else 1)
