import logging
import time
from playwright.sync_api import sync_playwright

# Configurar logging a stdout
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def autoimport_sonarr(page):
    try:
        page.goto("https://sonarr.internal.dbcloud.org/activity/queue")
        page.wait_for_url("https://sonarr.internal.dbcloud.org/activity/queue", timeout=10000)
        page.wait_for_selector("table", timeout=10000)

        rows = page.query_selector_all("table tbody tr")
        row_count = len(rows)

        for i in range(1, row_count + 1):
            try:
                # Ver si existe el botón de importación (icono user)
                user_icon_svg = page.query_selector(
                    f"table tbody tr:nth-child({i}) td:last-child button svg[data-icon='user']"
                )

                if not user_icon_svg:
                    logging.info(f'Row {i} skipped: No import button present.')
                    continue

                # Subir al botón y hacer clic
                import_button_click = user_icon_svg.evaluate_handle("el => el.closest('button')")
                import_button_click.click()
                time.sleep(5)

                # Esperar modal
                modal_selector = "div[class*='Modal-modal']"
                page.wait_for_selector(modal_selector, timeout=5000)

                # Buscar botón Import
                import_button = page.query_selector(f"{modal_selector} button:has-text('Import')")

                if import_button and import_button.is_enabled():
                    import_button.click()
                    time.sleep(2)
                    logging.info(f'Row {i} processed via Import.')
                else:
                    # Click en Cancel si Import está deshabilitado
                    cancel_button = page.query_selector(f"{modal_selector} button:has-text('Cancel')")
                    if cancel_button:
                        cancel_button.click()
                        time.sleep(1)
                        logging.info(f'Row {i}: Import disabled, clicked Cancel.')

                        # Buscar botón remove (icono xmark)
                        remove_icon_svg = page.query_selector(
                            f"table tbody tr:nth-child({i}) td:last-child button svg[data-icon='xmark']"
                        )
                        if remove_icon_svg:
                            remove_button = remove_icon_svg.evaluate_handle("el => el.closest('button')")
                            remove_button.click()
                            time.sleep(2)

                            # Esperar modal de confirmación de eliminación
                            page.wait_for_selector(modal_selector, timeout=5000)
                            remove_confirm_button = page.query_selector(
                                "button.Button-button-paJ9a.Button-danger-vthZW.Button-medium-ZwfFe"
                            )
                            if remove_confirm_button:
                                remove_confirm_button.click()
                                time.sleep(2)
                                logging.info(f'Row {i}: Item removed from queue.')
                            else:
                                logging.warning(f'Row {i}: Remove confirmation button not found.')
                        else:
                            logging.warning(f'Row {i}: Remove icon/button not found.')
                    else:
                        logging.warning(f'Row {i}: Cancel button not found after Import disabled.')

            except Exception as row_error:
                logging.error(f"Error processing row {i}: {row_error}")
                continue

        logging.info('Auto-import process completed successfully')

    except Exception as e:
        logging.error(f'Autoimport failed due to an exception: {e}')


if __name__ == "__main__":
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        autoimport_sonarr(page)
        browser.close()
