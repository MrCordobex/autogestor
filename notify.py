"""
notify.py — Script de notificación matutina por Telegram.
Lo ejecuta GitHub Actions cada mañana automáticamente.
No necesita Streamlit — lee directamente de Turso y manda el mensaje.
"""

import os
import sys
import json
import urllib.request
import urllib.parse
from datetime import date, datetime, timedelta

# Añadir el directorio actual al path para importar db.py
sys.path.insert(0, os.path.dirname(__file__))
import db


# ─────────────────────────── TELEGRAM ──────────────────────────────────────

def enviar_telegram(token: str, chat_id: str, mensaje: str) -> bool:
    """Envía un mensaje por Telegram. Devuelve True si fue bien."""
    url  = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id":    chat_id,
        "text":       mensaje,
        "parse_mode": "HTML",
    }).encode()

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("ok", False)
    except Exception as e:
        print(f"Error enviando Telegram: {e}")
        return False


# ─────────────────────────── LÓGICA DEL RESUMEN ────────────────────────────

def construir_resumen() -> str:
    hoy      = date.today()
    hoy_str  = hoy.isoformat()
    dia_idx  = hoy.weekday()  # 0=Lunes

    dia_nombre = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"][dia_idx]
    fecha_bonita = hoy.strftime("%d/%m/%Y")

    lineas = [
        f"☀️ <b>Buenos días — {dia_nombre} {fecha_bonita}</b>",
        "",
    ]

    # ── Clases de hoy (cache Loyola) ───────────────────────────────────────
    clases_hoy, _ = db.cache_leer("loyola")
    clases_dia    = [c for c in clases_hoy if c.get("fecha") == hoy_str]

    if clases_dia:
        lineas.append("🎓 <b>Clases de hoy:</b>")
        for c in clases_dia:
            lineas.append(f"  • {c['hora']} — {c['asignatura']} ({c.get('aula','?')})")
    else:
        lineas.append("🎓 Sin clases hoy")

    lineas.append("")

    # ── Partido del Sevilla hoy ────────────────────────────────────────────
    futbol_hoy, _ = db.cache_leer("sevilla")
    partido_hoy   = [f for f in futbol_hoy if f.get("fecha") == hoy_str]

    if partido_hoy:
        p = partido_hoy[0]
        hora_p = p.get("hora") or "hora por confirmar"
        lineas.append(f"⚽ <b>Partido hoy:</b> {p['titulo']} — {hora_p} ({p.get('aula','')})")
        lineas.append("")

    # ── Horario dinámico (rutinas y eventos) ───────────────────────────────
    horario  = db.horario_listar()
    eventos  = [
        ev for ev in horario
        if (ev["es_rutina"] and dia_idx in ev["dias_semana"])
        or (not ev["es_rutina"] and ev.get("fecha") == hoy_str)
    ]

    if eventos:
        lineas.append("📅 <b>Eventos de hoy:</b>")
        for ev in sorted(eventos, key=lambda x: x["hora_inicio"]):
            lineas.append(f"  • {ev['hora_inicio']} - {ev['hora_fin']} — {ev['titulo']}")
        lineas.append("")

    # ── Tareas del día ─────────────────────────────────────────────────────
    tareas      = db.tareas_listar()
    tareas_hoy  = [
        t for t in tareas
        if t["estado"] != "Completada"
        and t.get("fecha") == hoy_str
        and not t.get("fecha_fin")
    ]

    if tareas_hoy:
        lineas.append("📝 <b>Tareas de hoy:</b>")
        for t in tareas_hoy:
            hora_t = f" a las {t['hora']}" if not t["dia_completo"] and t.get("hora") else ""
            lineas.append(f"  • [{t['prioridad']}] {t['titulo']}{hora_t}")
        lineas.append("")

    # ── Deadlines próximos (hoy + próximos 3 días) ─────────────────────────
    limite = (hoy + timedelta(days=3)).isoformat()
    deadlines = [
        t for t in tareas
        if t["estado"] != "Completada"
        and t.get("fecha_fin")
        and hoy_str <= t["fecha_fin"] <= limite
    ]

    if deadlines:
        lineas.append("⏰ <b>Deadlines próximos:</b>")
        for t in sorted(deadlines, key=lambda x: x["fecha_fin"]):
            dias_restantes = (date.fromisoformat(t["fecha_fin"]) - hoy).days
            if dias_restantes == 0:
                when = "HOY"
            elif dias_restantes == 1:
                when = "mañana"
            else:
                when = f"en {dias_restantes} días"
            lineas.append(f"  • {t['titulo']} — vence {when} ({t['fecha_fin']})")
        lineas.append("")

    # ── Tareas atrasadas ───────────────────────────────────────────────────
    atrasadas = [
        t for t in tareas
        if t["estado"] != "Completada"
        and (t.get("fecha_fin") or t.get("fecha", "9999")) < hoy_str
    ]

    if atrasadas:
        lineas.append(f"🚨 <b>Tienes {len(atrasadas)} tarea(s) atrasada(s):</b>")
        for t in atrasadas:
            ref = t.get("fecha_fin") or t.get("fecha", "?")
            lineas.append(f"  • {t['titulo']} (venció el {ref})")
        lineas.append("")

    # ── Pie ────────────────────────────────────────────────────────────────
    pendientes_total = len([t for t in tareas if t["estado"] != "Completada"])
    lineas.append(f"📊 Total pendientes: <b>{pendientes_total}</b>")
    lineas.append("")
    lineas.append("¡Buena jornada! 💪")

    return "\n".join(lineas)


# ─────────────────────────── MAIN ──────────────────────────────────────────

def main():
    # Leer credenciales de variables de entorno (las pone GitHub Actions)
    token   = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("❌ Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en las variables de entorno.")
        sys.exit(1)

    print("Conectando a la base de datos...")
    db.init_db()

    print("Construyendo resumen...")
    mensaje = construir_resumen()
    print("── MENSAJE ──────────────────────────")
    print(mensaje)
    print("─────────────────────────────────────")

    print("Enviando por Telegram...")
    ok = enviar_telegram(token, chat_id, mensaje)

    if ok:
        print("✅ Mensaje enviado correctamente.")
    else:
        print("❌ Error al enviar el mensaje.")
        sys.exit(1)


if __name__ == "__main__":
    main()