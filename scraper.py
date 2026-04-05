"""
scraper.py — Scraping de horario Loyola y partidos Sevilla FC.
Devuelve listas de dicts normalizados. No toca Streamlit ni DB directamente.
"""

import re
import time
from datetime import datetime, timedelta, date
from typing import Optional

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

try:
    from webdriver_manager.chrome import ChromeDriverManager
    _HAS_WDM = True
except ImportError:
    _HAS_WDM = False


# ─────────────────────── DRIVER ────────────────────────────────────────────

def crear_driver() -> Optional[webdriver.Chrome]:
    """Devuelve un driver Chrome headless o None si no se puede iniciar."""
    opts = webdriver.ChromeOptions()
    for arg in ("--headless", "--no-sandbox", "--disable-dev-shm-usage",
                "--disable-gpu", "--window-size=1920,1080"):
        opts.add_argument(arg)

    # Intentar driver del sistema (Linux/Cloud)
    candidatos_driver = [
        "/usr/bin/chromedriver",
        "/usr/lib/chromium-browser/chromedriver",
        "/snap/bin/chromium.chromedriver",
    ]
    candidatos_bin = [
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
    ]

    service = None
    for p in candidatos_driver:
        import os
        if os.path.exists(p):
            service = Service(p)
            for b in candidatos_bin:
                if os.path.exists(b):
                    opts.binary_location = b
                    break
            break

    if service is None and _HAS_WDM:
        try:
            service = Service(ChromeDriverManager().install())
        except Exception:
            pass

    if service is None:
        return None

    try:
        return webdriver.Chrome(service=service, options=opts)
    except Exception:
        return None


# ─────────────────────── LOYOLA ────────────────────────────────────────────

def scrape_loyola(semanas: int = 12) -> list[dict]:
    """
    Scrapea el horario de Loyola.
    Devuelve lista de:
        { asignatura, aula, fecha (YYYY-MM-DD), hora (HH:MM - HH:MM) }
    """
    driver = crear_driver()
    if driver is None:
        raise RuntimeError("No se pudo iniciar Chrome.")

    url = (
        "https://portales.uloyola.es/LoyolaHorario/horario.xhtml"
        "?curso=2025%2F26&tipo=M&titu=2169&campus=2&ncurso=1&grupo=A"
    )
    clases = []

    try:
        driver.get(url)
        wait = WebDriverWait(driver, 15)
        wait.until(EC.presence_of_element_located((By.CLASS_NAME, "fc-view-harness")))

        for _ in range(semanas):
            try:
                try:
                    WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.CLASS_NAME, "fc-event"))
                    )
                except Exception:
                    pass

                time.sleep(1.5)

                # Mapa columna → fecha
                headers = driver.find_elements(By.CLASS_NAME, "fc-col-header-cell")
                col_map = []
                for h in headers:
                    d = h.get_attribute("data-date")
                    if d:
                        r = h.rect
                        col_map.append({"date": d, "x0": r["x"], "x1": r["x"] + r["width"]})

                for ev in driver.find_elements(By.CLASS_NAME, "fc-event"):
                    try:
                        rect = ev.rect
                        cx = rect["x"] + rect["width"] / 2
                        fecha = next((c["date"] for c in col_map if c["x0"] <= cx <= c["x1"]), None)
                        if not fecha:
                            continue

                        try:
                            hora_txt    = ev.find_element(By.CLASS_NAME, "fc-event-time").text
                            content_txt = ev.find_element(By.CLASS_NAME, "fc-event-title").text
                        except Exception:
                            lines = ev.text.split("\n")
                            hora_txt    = lines[0] if lines else ""
                            content_txt = lines[1] if len(lines) > 1 else ""

                        parts = content_txt.split("/")
                        asig = parts[0].strip()
                        aula = parts[1].replace("Aula:", "").strip() if len(parts) > 1 else "?"

                        # Ajuste UTC → Europe/Madrid
                        # La web devuelve horas en UTC. España es UTC+2 en verano, UTC+1 en invierno.
                        try:
                            import pytz
                            from datetime import datetime as dt_
                            zona = pytz.timezone("Europe/Madrid")
                            tramos = hora_txt.split("-")
                            ajustados = []
                            for t in tramos:
                                utc_dt = datetime.strptime(t.strip(), "%H:%M").replace(
                                    tzinfo=pytz.utc, year=2000, month=6, day=1
                                )
                                local_dt = utc_dt.astimezone(zona)
                                ajustados.append(local_dt.strftime("%H:%M"))
                            hora_txt = f"{ajustados[0]} - {ajustados[1]}"
                        except Exception:
                            pass

                        clases.append({"asignatura": asig, "aula": aula,
                                    "fecha": fecha, "hora": hora_txt})
                    except Exception:
                        continue

                # Siguiente semana
                btn = driver.find_element(By.CLASS_NAME, "fc-next-button")
                btn.click()
                time.sleep(1.0)

            except Exception:
                break
    finally:
        driver.quit()

    return clases


# ─────────────────────── SEVILLA FC ────────────────────────────────────────

def _format_team(name: str) -> str:
    partes = name.strip().split()
    res = []
    for p in partes:
        if p.lower() in ("fc", "cf", "sfc", "cd"):
            res.append(p.upper())
        elif p.lower() in ("de", "del", "la", "el"):
            res.append(p.lower())
        else:
            res.append(p.capitalize())
    return " ".join(res)


def scrape_sevilla() -> list[dict]:
    """
    Scrapea próximos partidos del Sevilla FC.
    Devuelve lista de:
        { titulo, aula (Casa/Fuera), fecha (YYYY-MM-DD), hora (HH:MM | None), dia_completo }
    """
    driver = crear_driver()
    if driver is None:
        raise RuntimeError("No se pudo iniciar Chrome.")

    url = "https://www.laliga.com/clubes/sevilla-fc/proximos-partidos"
    partidos = []

    try:
        driver.get(url)
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.TAG_NAME, "tr"))
        )

        # Aceptar cookies si aparecen
        try:
            for btn in driver.find_elements(By.TAG_NAME, "button"):
                if any(w in btn.text.lower() for w in ("aceptar", "accept", "consentir")):
                    btn.click()
                    time.sleep(1)
                    break
        except Exception:
            pass

        for fila in driver.find_elements(By.TAG_NAME, "tr"):
            if "more-info" in (fila.get_attribute("class") or ""):
                continue
            texto = fila.text
            if not texto:
                continue

            # Fecha DD.MM.YYYY
            m_fecha = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", texto)
            if not m_fecha:
                continue
            fecha_iso = f"{m_fecha.group(3)}-{m_fecha.group(2)}-{m_fecha.group(1)}"

            # Hora HH:MM
            m_hora = re.search(r"(\d{2}:\d{2})", texto)
            hora_txt = m_hora.group(1) if m_hora else None
            dia_completo = hora_txt is None

            # Equipos via "VS"
            lineas = [l.strip() for l in texto.split("\n") if l.strip()]
            idx_vs = next((i for i, l in enumerate(lineas) if l.upper() == "VS"), -1)

            local = visitante = "Desconocido"
            if idx_vs > 0 and idx_vs + 1 < len(lineas):
                local     = _format_team(lineas[idx_vs - 1])
                visitante = _format_team(lineas[idx_vs + 1])

            ubicacion = "Casa" if "sevilla" in local.lower() else "Fuera"

            partidos.append({
                "titulo":      f"{local} vs {visitante}",
                "aula":        ubicacion,
                "fecha":       fecha_iso,
                "hora":        hora_txt,
                "dia_completo": dia_completo,
            })

    except Exception as e:
        raise RuntimeError(f"Error scraping Sevilla: {e}") from e
    finally:
        driver.quit()

    return partidos
