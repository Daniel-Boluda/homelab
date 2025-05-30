import os
import logging
import time
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure the logging system to log to stdout
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Environment variables for secrets
USERNAME = os.getenv('USERNAME')
PASSWORD_HDO = os.getenv('PASSWORD_HDO')
PASSWORD_TORRENTEROS = os.getenv('PASSWORD_TORRENTEROS')
PASSWORD_TORRENTLAND = os.getenv('PASSWORD_TORRENTLAND')
PASSWORD_TORRENTLEECH = os.getenv('PASSWORD_TORRENTLEECH')

def login_hdo(page):
    try:
        page.goto("https://hd-olimpo.club/login")

        page.fill("#username", USERNAME)
        time.sleep(2)
        page.fill("#password", PASSWORD_HDO)
        time.sleep(2)

        page.click("#login-button")

        # Esperar que la URL cambie a la esperada
        page.wait_for_url("https://hd-olimpo.club/pages/1", timeout=10000)

        current_url = page.url
        if current_url == "https://hd-olimpo.club/pages/1":
            logging.info('Login successful. Redirected to %s', current_url)
        else:
            logging.error('Login failed. Current URL is %s', current_url)

    except Exception as e:
        logging.error(f'Login failed due to an exception: {e}')

def login_torrenteros(page):
    try:
        page.goto("https://torrenteros.org/login")

        page.fill("input[name='username']", USERNAME)
        time.sleep(2)
        page.fill("input[name='password']", PASSWORD_TORRENTEROS)
        time.sleep(2)

        page.click("//button[contains(@class,'auth-form__primary-button')]")

        page.wait_for_url("https://torrenteros.org/pages/1", timeout=10000)

        current_url = page.url
        if current_url == "https://torrenteros.org/pages/1":
            logging.info('Login successful. Redirected to %s', current_url)
        else:
            logging.error('Login failed. Current URL is %s', current_url)

    except Exception as e:
        logging.error(f'Login failed due to an exception: {e}')

def login_torrentland(page):
    try:
        page.goto("https://torrentland.li/login")

        page.fill("#username", USERNAME)
        time.sleep(2)
        page.fill("#password", PASSWORD_TORRENTLAND)
        time.sleep(2)

        page.click("#login-button")

        page.wait_for_url("https://torrentland.li/", timeout=10000)

        current_url = page.url
        if current_url == "https://torrentland.li/":
            logging.info('Login successful. Redirected to %s', current_url)
        else:
            logging.error('Login failed. Current URL is %s', current_url)

    except Exception as e:
        logging.error(f'Login failed due to an exception: {e}')

def login_torrentleech(page):
    try:
        page.goto("https://www.torrentleech.org/user/account/login/")

        page.fill("input[name='username']", USERNAME)
        time.sleep(2)
        page.fill("input[name='password']", PASSWORD_TORRENTLEECH)
        time.sleep(2)

        page.click("//button[contains(@type,'submit') and contains(@class, 'tl-btn') and contains(@class, 'btn-primary')]")

        page.wait_for_url("https://www.torrentleech.org/torrents/top/index/added/-1%20day/orderby/completed/order/desc", timeout=10000)

        current_url = page.url
        if current_url == "https://www.torrentleech.org/torrents/top/index/added/-1%20day/orderby/completed/order/desc":
            logging.info('Login successful. Redirected to %s', current_url)
        else:
            logging.error('Login failed. Current URL is %s', current_url)

    except Exception as e:
        logging.error(f'Login failed due to una excepción: {e}')

if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)  # headless=True para sin GUI, false para ver la ventana
        page = browser.new_page()

        login_hdo(page)
        time.sleep(2)

        login_torrenteros(page)
        time.sleep(2)

        login_torrentland(page)
        time.sleep(2)

        login_torrentleech(page)
        time.sleep(2)

        browser.close()
