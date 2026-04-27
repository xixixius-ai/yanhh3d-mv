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
    text = re.sub(r'[àáạảãâầấậẩẫăằắặẳẵ]', 'a', text.lower())
    text = re.sub(r'[èéẹẻẽêề
