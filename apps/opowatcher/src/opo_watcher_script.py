import os
import time
import logging
import requests
import difflib
from datetime import datetime
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

# Cargar variables de entorno (.env)
load_dotenv()

# Configuración
URL = 'https://www.defensa.gob.es/portalservicios/procesos/personalestatutario/ofertas_empleo_publico/2024/'
BASE_URL = 'https://www.defensa.gob.es'
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_FILE = os.path.join(BASE_DIR, 'html', 'accordion4_content.html')
VERSIONS_DIR = os.path.join(BASE_DIR, 'html', 'versions')
DIFFS_DIR = os.path.join(BASE_DIR, 'html', 'diffs')

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
os.makedirs(VERSIONS_DIR, exist_ok=True)
os.makedirs(DIFFS_DIR, exist_ok=True)

def save_file(content, path):
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)

def save_version(content, diff_text=None):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_filename = f"accordion4_{timestamp}"
    html_path = os.path.join(VERSIONS_DIR, f"{base_filename}.html")
    save_file(content, html_path)
    logging.info(f"Versión guardada: {html_path}")

    if diff_text:
        diff_path = os.path.join(DIFFS_DIR, f"{base_filename}.diff.txt")
        save_file(diff_text, diff_path)
        logging.info(f"Diff guardado: {diff_path}")

def load_previous_lines():
    if not os.path.exists(HTML_FILE):
        return []
    with open(HTML_FILE, 'r', encoding='utf-8') as f:
        return f.read().splitlines()

def notify_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logging.warning("Faltan credenciales de Telegram.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        res = requests.post(url, data=data)
        if res.status_code == 200:
            logging.info("Notificación enviada.")
        else:
            logging.error(f"Telegram error: {res.status_code} - {res.text}")
    except Exception as e:
        logging.error(f"Telegram exception: {e}")

def extract_pdf_lines_from_page(page):
    try:
        page.click("a[href='#accordion4']", timeout=5000)
    except:
        pass
    page.wait_for_selector("#accordion4", state="visible", timeout=10000)
    return page.eval_on_selector_all(
        "#accordion4 .fileseccion p",
        "els => els.map(el => el.outerHTML.trim())"
    )

def generate_diff(old_lines, new_lines):
    diff = difflib.unified_diff(old_lines, new_lines, fromfile='anterior', tofile='actual', lineterm='')
    return '\n'.join(diff)

def extract_link_info(line):
    # Solo usando métodos de string para evitar dependencias externas
    href_start = line.find('href="') + len('href="')
    href_end = line.find('"', href_start)
    href = line[href_start:href_end] if href_start > -1 and href_end > -1 else ""

    # Texto entre > y </a>
    link_start = line.find('>', href_end) + 1
    link_end = line.find('</a>', link_start)
    text = line[link_start:link_end].strip() if link_start > -1 and link_end > -1 else ""

    if href and text:
        url = f"{BASE_URL}/{href.lstrip('./')}"
        return f"<a href=\"{url}\">{text}</a>"
    return None

def summarize_changes(diff_text):
    additions, removals = [], []

    for line in diff_text.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            info = extract_link_info(line[1:].strip())
            if info:
                additions.append(info)
        elif line.startswith("-") and not line.startswith("---"):
            info = extract_link_info(line[1:].strip())
            if info:
                removals.append(info)

    summary = ""
    if additions:
        summary += "➕ <b>Documentos añadidos:</b>\n" + "\n".join(additions) + "\n\n"
    if removals:
        summary += "➖ <b>Documentos eliminados:</b>\n" + "\n".join(removals)

    return summary.strip()

def monitor_page():
    previous_lines = load_previous_lines()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        logging.info("Iniciando monitorización. Ctrl+C para detener.")

        try:
            while True:
                page.goto(URL, timeout=30000)
                current_lines = extract_pdf_lines_from_page(page)

                if current_lines != previous_lines:
                    diff_text = generate_diff(previous_lines, current_lines)
                    summary = summarize_changes(diff_text)
                    if summary:
                        logging.info("Cambios detectados.")
                        notify_telegram(f"🔔 <b>¡Cambio detectado en la web del Ministerio de Defensa!</b>\n\n{summary}")
                    else:
                        logging.info("Cambio detectado sin diferencias útiles.")

                    joined_html = "\n".join(current_lines)
                    save_version(joined_html, diff_text)
                    save_file(joined_html, HTML_FILE)
                    previous_lines = current_lines
                else:
                    logging.info("Sin cambios detectados.")

                time.sleep(300)

        except KeyboardInterrupt:
            logging.info("Monitorización detenida por el usuario.")
        finally:
            browser.close()

if __name__ == "__main__":
    monitor_page()
