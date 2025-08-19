import os
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

BASE_URL = os.getenv('BASE_URL', 'http://localhost:8000')

def test_open_home_and_pages():
    opts = Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--no-sandbox')
    driver = webdriver.Chrome(options=opts)
    try:
        driver.set_page_load_timeout(20)
        driver.get(BASE_URL)
        time.sleep(1)
        driver.get(f"{BASE_URL}/page?id=home")
        time.sleep(1)
        assert "Welcome" in driver.page_source
        driver.get(f"{BASE_URL}/page?id=about")
        time.sleep(1)
        assert "About WebPageGenie" in driver.page_source
    finally:
        driver.quit()
