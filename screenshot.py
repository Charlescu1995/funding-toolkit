"""
Utilidad de desarrollo: abre la app Streamlit local con Playwright y guarda
capturas de pantalla. Solo para enseñar el progreso — no forma parte de la
app en sí.
"""

import sys
import time

from playwright.sync_api import sync_playwright

url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8501"
out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/screenshot.png"
wait_selector = sys.argv[3] if len(sys.argv) > 3 else None
click_text = sys.argv[4] if len(sys.argv) > 4 else None

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto(url, wait_until="networkidle", timeout=30000)
    time.sleep(2)  # streamlit tarda un poco en pintar tras la carga inicial

    if click_text:
        page.get_by_role("tab", name=click_text).click()
        time.sleep(2)

    if wait_selector:
        page.wait_for_selector(wait_selector, timeout=15000)

    page.screenshot(path=out, full_page=True)
    browser.close()

print(f"Guardado: {out}")
