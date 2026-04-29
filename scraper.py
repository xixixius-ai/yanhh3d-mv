#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scraper cho yanhh3d.bz - Hoạt hình Trung Quốc thuyết minh
Chỉ chỉnh sửa cần thiết, giữ nguyên tinh thần code gốc
"""

import re
import sys
import json
import time
import requests
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from typing import Optional, List, Dict

# Cấu hình mặc định - chỉ chỉnh khi cần
DEFAULT_CONFIG = {
    "base_url": "https://yanhh3d.bz",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "timeout": 30,
    "retry_attempts": 3,
    "retry_delay": 2,
    "preferred_quality": "4K",  # 4K, 1080, HD, 4K-, 1080-
}


class YanHH3DScraper:
    """Scraper chính cho yanhh3d.bz"""
    
    def __init__(self, config: Optional[Dict] = None):
        """Khởi tạo scraper với cấu hình tùy chọn"""
        self.config = {**DEFAULT_CONFIG, **(config or {})}
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": self.config["user_agent"],
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": self.config["base_url"],
        })
    
    def _make_request(self, url: str, retries: int = 0) -> Optional[requests.Response]:
        """Gửi request với retry logic - giữ nguyên logic gốc"""
        try:
            response = self.session.get(url, timeout=self.config["timeout"])
            response.raise_for_status()
            return response
        except requests.RequestException as e:
            if retries < self.config["retry_attempts"]:
                time.sleep(self.config["retry_delay"] * (retries + 1))
                return self._make_request(url, retries + 1)
            print(f"[ERROR] Request failed: {url} - {e}", file=sys.stderr)
            return None
    
    def _parse_html(self, html: str) -> BeautifulSoup:
        """Parse HTML content - chỉ dùng BeautifulSoup"""
        return BeautifulSoup(html, "lxml")  # Hoặc "html.parser" nếu không có lxml
    
    def extract_episode_info(self, url: str) -> Optional[Dict]:
        """
        Trích xuất thông tin episode từ URL
        Returns: dict chứa info episode hoặc None nếu lỗi
        """
        response = self._make_request(url)
        if not response:
            return None
        
        soup = self._parse_html(response.text)
        
        # Trích xuất metadata từ og tags - giữ nguyên cách lấy dữ liệu gốc
        info = {
            "url": url,
            "title": soup.find("meta", property="og:title"),
            "description": soup.find("meta", property="og:description"),
            "image": soup.find("meta", property="og:image"),
            "published_time": soup.find("meta", property="article:published_time"),
            "modified_time": soup.find("meta", property="article:modified_time"),
        }
        
        # Clean values
        for key in info:
            if info[key] and hasattr(info[key], "get"):
                info[key] = info[key].get("content", "")
        
        # Trích xuất streaming servers - phần quan trọng nhất
        servers = self._extract_servers(soup)
        info["servers"] = servers
        
        # Trích xuất danh sách episode liên quan
        episodes = self._extract_episode_list(soup)
        info["episodes"] = episodes
        
        # Trích xuất thông tin phim
        film_info = self._extract_film_detail(soup)
        info["film"] = film_info
        
        return info
    
    def _extract_servers(self, soup: BeautifulSoup) -> List[Dict]:
        """
        Trích xuất danh sách server/streaming links
        Giữ nguyên cấu trúc data-src từ HTML gốc
        """
        servers = []
        server_list = soup.find("div", id="list_sv")
        if not server_list:
            return servers
        
        for btn in server_list.find_all("a", class_="btn3dsv"):
            server_info = {
                "name": btn.get("name", ""),
                "label": btn.get_text(strip=True),
                "data_src": btn.get("data-src", ""),
                "is_active": "active" in btn.get("class", []),
                "url": btn.get("href", ""),
            }
            
            # Resolve relative URLs
            if server_info["data_src"] and not server_info["data_src"].startswith(("http://", "https://")):
                server_info["data_src"] = urljoin(self.config["base_url"], server_info["data_src"])
            
            servers.append(server_info)
        
        return servers
    
    def _extract_episode_list(self, soup: BeautifulSoup) -> List[Dict]:
        """Trích xuất danh sách episode từ trang"""
        episodes = []
        
        # Tìm container chứa episode list
        ep_container = soup.find("div", class_="ep-range")
        if not ep_container:
            return episodes
        
        for item in ep_container.find_all("a", class_="ssl-item"):
            ep_info = {
                "title": item.get("title", ""),
                "url": item.get("href", ""),
                "order": None,
                "ep_name": None,
            }
            
            # Extract order number
            order_el = item.find("div", class_="ssli-order")
            if order_el:
                ep_info["order"] = order_el.get_text(strip=True)
            
            # Extract episode name
            name_el = item.find("div", class_="ep-name")
            if name_el:
                ep_info["ep_name"] = name_el.get_text(strip=True)
            
            # Resolve URL
            if ep_info["url"] and not ep_info["url"].startswith(("http://", "https://")):
                ep_info["url"] = urljoin(self.config["base_url"], ep_info["url"])
            
            episodes.append(ep_info)
        
        return episodes
    
    def _extract_film_detail(self, soup: BeautifulSoup) -> Dict:
        """Trích xuất thông tin chi tiết phim"""
        detail = {}
        
        # Film name
        film_name = soup.find("h2", class_="film-name")
        if film_name:
            link = film_name.find("a")
            detail["name"] = link.get_text(strip=True) if link else film_name.get_text(strip=True)
            detail["film_url"] = link.get("href", "") if link else ""
        
        # Info items
        for item in soup.find_all("div", class_="item"):
            head = item.find("span", class_="item-head")
            name = item.find("span", class_="name")
            if head and name:
                key = head.get_text(strip=True).rstrip(":").lower()
                value = name.get_text(strip=True)
                detail[key] = value
        
        # Genres
        genres = []
        for genre in soup.find_all("a", class_="genre"):
            genres.append({
                "name": genre.get_text(strip=True),
                "url": genre.get("href", ""),
            })
        if genres:
            detail["genres"] = genres
        
        return detail
    
    def get_stream_url(self, server_info: Dict, quality: Optional[str] = None) -> Optional[str]:
        """
        Lấy URL streaming thực tế từ server info
        quality: ưu tiên chất lượng nếu có nhiều server
        """
        if not server_info or not server_info.get("data_src"):
            return None
        
        data_src = server_info["data_src"]
        
        # Nếu là m3u8 trực tiếp, trả về ngay
        if data_src.endswith(".m3u8"):
            return data_src
        
        # Nếu là endpoint cần fetch thêm
        if "/play-" in data_src or data_src.startswith("https://yanhh3d.bz/play"):
            response = self._make_request(data_src)
            if response:
                # Try to extract m3u8 from response
                content = response.text
                m3u8_match = re.search(r'(https?://[^\s\'"]+\.m3u8[^\s\'"]*)', content)
                if m3u8_match:
                    return m3u8_match.group(1)
                # Try JSON response
                try:
                    json_data = response.json()
                    if isinstance(json_data, dict):
                        for key in ["url", "file", "source", "src", "link"]:
                            if key in json_data and isinstance(json_data[key], str):
                                if json_data[key].endswith(".m3u8"):
                                    return json_data[key]
                except json.JSONDecodeError:
                    pass
        
        return data_src  # Fallback trả về data_src gốc
    
    def get_best_server(self, servers: List[Dict], preferred_quality: str = None) -> Optional[Dict]:
        """Chọn server tốt nhất dựa trên quality preference"""
        if not servers:
            return None
        
        quality = preferred_quality or self.config["preferred_quality"]
        
        # Ưu tiên server active trước
        for server in servers:
            if server.get("is_active"):
                return server
        
        # Sau đó tìm theo quality preference
        quality_order = [quality, "4K", "1080", "HD", "4K-", "1080-", "Link10"]
        for q in quality_order:
            for server in servers:
                if q.lower() in server.get("label", "").lower():
                    return server
        
        # Fallback: trả về server đầu tiên
        return servers[0] if servers else None
    
    def scrape_episode(self, url: str, quality: Optional[str] = None) -> Optional[Dict]:
        """
        Hàm chính: scrape toàn bộ thông tin episode + lấy stream URL tốt nhất
        """
        info = self.extract_episode_info(url)
        if not info:
            return None
        
        # Chọn server tốt nhất
        best_server = self.get_best_server(info.get("servers", []), quality)
        if best_server:
            stream_url = self.get_stream_url(best_server, quality)
            if stream_url:
                info["stream_url"] = stream_url
                info["selected_server"] = best_server
        
        return info
    
    def scrape_series(self, series_url: str, start_ep: int = 1, end_ep: Optional[int] = None) -> List[Dict]:
        """
        Scrapes toàn bộ series hoặc range episodes
        series_url: URL trang chính của series (không phải episode)
        """
        results = []
        
        # Fetch trang chính để lấy episode list
        response = self._make_request(series_url)
        if not response:
            return results
        
        soup = self._parse_html(response.text)
        episodes = self._extract_episode_list(soup)
        
        # Filter episodes theo range
        for ep in episodes:
            order = ep.get("order", "")
            # Try extract number from order like "138", "139 TL", "51-55"
            ep_num_match = re.match(r'^(\d+)', str(order))
            if ep_num_match:
                ep_num = int(ep_num_match.group(1))
                if ep_num < start_ep:
                    continue
                if end_ep and ep_num > end_ep:
                    continue
            
            # Scrape từng episode
            if ep.get("url"):
                ep_info = self.scrape_episode(ep["url"])
                if ep_info:
                    results.append(ep_info)
                time.sleep(0.5)  # Rate limiting nhẹ
        
        return results


def main():
    """Entry point - giữ nguyên structure để dễ extend"""
    import argparse
    
    parser = argparse.ArgumentParser(description="YanHH3D Scraper")
    parser.add_argument("url", help="URL episode hoặc series cần scrape")
    parser.add_argument("-q", "--quality", choices=["4K", "1080", "HD", "4K-", "1080-"],
                       help="Chất lượng video ưu tiên")
    parser.add_argument("-o", "--output", help="File output JSON (optional)")
    parser.add_argument("-s", "--series", action="store_true", 
                       help="Scrape toàn bộ series thay vì 1 episode")
    parser.add_argument("--start", type=int, default=1, help="Episode bắt đầu (cho series)")
    parser.add_argument("--end", type=int, help="Episode kết thúc (cho series)")
    
    args = parser.parse_args()
    
    scraper = YanHH3DScraper()
    
    if args.series:
        results = scraper.scrape_series(
            args.url, 
            start_ep=args.start, 
            end_ep=args.end
        )
        output = {"series": args.url, "episodes": results, "count": len(results)}
    else:
        result = scraper.scrape_episode(args.url, quality=args.quality)
        output = result if result else {"error": "Failed to scrape"}
    
    # Output
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"[OK] Saved to {args.output}")
    else:
        print(json.dumps(output, ensure_ascii=False, indent=2))
    
    return 0 if output and "error" not in output else 1


if __name__ == "__main__":
    sys.exit(main())
