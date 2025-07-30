import os
import time
import logging
import requests
from datetime import datetime
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

# Configuración
URL = 'https://ascatedrais.xunta.gal/monatr/iniciarReserva'
STATE_FILE = '/tmp/day10_status.txt'

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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

def load_previous_status():
    if not os.path.exists(STATE_FILE):
        return None
    with open(STATE_FILE, 'r') as f:
        return f.read().strip()

def save_current_status(status):
    with open(STATE_FILE, 'w') as f:
        f.write(status)

def check_availability():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        logging.info("Iniciando comprobación de disponibilidad.")

        try:
            # Paso 1: Ir a la URL
            page.goto(URL, timeout=30000)

            # Esperar y hacer clic en el botón "Continuar"
            page.wait_for_selector("text=Iniciar reserva", timeout=15000)
            page.get_by_text("Iniciar reserva").click()
            logging.info("Botón 'Continuar' clicado.")

            # Paso 3: Esperar redirección y campo de plazas
            page.wait_for_selector("#numPlazas", timeout=15000)
            page.fill("#numPlazas", "2")
            logging.info("Escrito '2' en el campo numPlazas.")

            # Paso 4: Verificar si se muestra 'Agosto 2025'
            page.wait_for_selector(".monthTitle", timeout=10000)
            month_title = page.inner_text(".monthTitle").strip()
            logging.info(f"Mes visible en el calendario: {month_title}")
            
            # Esperar el contenido del día 10
            page.wait_for_selector("#dayList_10", timeout=15000)
            element = page.query_selector("#dayList_10")
            class_attr = element.get_attribute("class")
            logging.info(f"Clase actual de dayList_10: {class_attr}")

            # Comprobar si ha cambiado
            previous_status = load_previous_status()
            if previous_status != class_attr:
                save_current_status(class_attr)
                if "sinPlazas" not in class_attr:
                    notify_telegram("🟢 <b>¡Día 10 disponible!</b>\nRevisa la página de reservas: https://ascatedrais.xunta.gal/monatr/iniciarReserva")
                else:
                    notify_telegram("🔴 <b>El día 10 ha vuelto a estar sin plazas.</b>")
            else:
                logging.info("Sin cambios en la disponibilidad.")
        except Exception as e:
            logging.error(f"Error en la comprobación: {e}")
        finally:
            browser.close()

if __name__ == "__main__":
    while True:
        check_availability()
        time.sleep(300)  # Espera 5 minutos
