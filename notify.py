"""
notify.py — Notificaciones por Telegram.
Funciones invocadas por distintos GitHub Actions workflows.

Uso:
    python notify.py matutino       → resumen del día (cada mañana)
    python notify.py deadlines      → aviso 24h antes de deadlines (cada noche)
    python notify.py partido        → aviso si hay partido hoy (cada mañana)
    python notify.py semanal        → resumen de la semana siguiente (domingos)
"""

import os
import sys
import json
import urllib.request
import urllib.parse
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
import db


# ─────────────────────────── TELEGRAM ──────────────────────────────────────

def enviar_telegram(token: str, chat_id: str, mensaje: str) -> bool:
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


# ─────────────────────────── HELPERS ───────────────────────────────────────

def _barra_carga(n: int, maximo: int = 10) -> str:
    """Genera una barra visual tipo ████░░░░"""
    llenos  = min(round((n / maximo) * 10), 10)
    vacios  = 10 - llenos
    return "█" * llenos + "░" * vacios

def _nivel_carga(n_deadlines: int, n_tareas: int) -> str:
    total = n_deadlines * 2 + n_tareas  # deadlines pesan más
    if total >= 8:  return "🔴 MUY ALTA"
    if total >= 5:  return "🟠 ALTA"
    if total >= 3:  return "🟡 MEDIA"
    if total >= 1:  return "🟢 BAJA"
    return "⚪ LIBRE"


# ─────────────────────── 1. RESUMEN MATUTINO ───────────────────────────────

def construir_resumen_matutino() -> str:
    hoy     = date.today()
    hoy_str = hoy.isoformat()
    dia_idx = hoy.weekday()

    dia_nombre   = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"][dia_idx]
    fecha_bonita = hoy.strftime("%d/%m/%Y")

    lineas = [f"☀️ <b>Buenos días — {dia_nombre} {fecha_bonita}</b>", ""]

    # Clases de hoy
    clases_all, _ = db.cache_leer("loyola")
    clases_hoy    = [c for c in clases_all if c.get("fecha") == hoy_str]
    if clases_hoy:
        lineas.append("🎓 <b>Clases de hoy:</b>")
        for c in sorted(clases_hoy, key=lambda x: x.get("hora", "")):
            lineas.append(f"  • {c['hora']} — {c['asignatura']} ({c.get('aula','?')})")
    else:
        lineas.append("🎓 Sin clases hoy")
    lineas.append("")

    # Partido hoy
    futbol_all, _ = db.cache_leer("sevilla")
    partido_hoy   = [f for f in futbol_all if f.get("fecha") == hoy_str]
    if partido_hoy:
        p     = partido_hoy[0]
        hora_p = p.get("hora") or "hora por confirmar"
        lineas.append(f"⚽ <b>Partido hoy:</b> {p['titulo']} — {hora_p} ({p.get('aula','')})")
        lineas.append("")

    # Eventos del horario dinámico
    horario = db.horario_listar()
    eventos = [
        ev for ev in horario
        if (ev["es_rutina"] and dia_idx in ev["dias_semana"])
        or (not ev["es_rutina"] and ev.get("fecha") == hoy_str)
    ]
    if eventos:
        lineas.append("📅 <b>Eventos de hoy:</b>")
        for ev in sorted(eventos, key=lambda x: x["hora_inicio"]):
            lineas.append(f"  • {ev['hora_inicio']} - {ev['hora_fin']} — {ev['titulo']}")
        lineas.append("")

    # Tareas del día
    tareas     = db.tareas_listar()
    tareas_hoy = [
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

    # Deadlines próximos 3 días
    limite    = (hoy + timedelta(days=3)).isoformat()
    deadlines = [
        t for t in tareas
        if t["estado"] != "Completada"
        and t.get("fecha_fin")
        and hoy_str <= t["fecha_fin"] <= limite
    ]
    if deadlines:
        lineas.append("⏰ <b>Deadlines próximos:</b>")
        for t in sorted(deadlines, key=lambda x: x["fecha_fin"]):
            dias = (date.fromisoformat(t["fecha_fin"]) - hoy).days
            when = "HOY" if dias == 0 else "mañana" if dias == 1 else f"en {dias}d"
            lineas.append(f"  • {t['titulo']} — vence {when}")
        lineas.append("")

    # Atrasadas
    atrasadas = [
        t for t in tareas
        if t["estado"] != "Completada"
        and (t.get("fecha_fin") or t.get("fecha", "9999")) < hoy_str
    ]
    if atrasadas:
        lineas.append(f"🚨 <b>{len(atrasadas)} tarea(s) atrasada(s):</b>")
        for t in atrasadas:
            ref = t.get("fecha_fin") or t.get("fecha", "?")
            lineas.append(f"  • {t['titulo']} (venció el {ref})")
        lineas.append("")

    pendientes_total = len([t for t in tareas if t["estado"] != "Completada"])
    lineas.append(f"📊 Total pendientes: <b>{pendientes_total}</b>")
    lineas.append("")
    lineas.append("¡Buena jornada! 💪")

    return "\n".join(lineas)


# ─────────────────────── 2. AVISO DEADLINES 24H ────────────────────────────

def construir_aviso_deadlines() -> str | None:
    """
    Devuelve un mensaje si mañana hay deadlines o tareas programadas.
    Devuelve None si no hay nada → el workflow no manda nada.
    """
    hoy      = date.today()
    manana   = hoy + timedelta(days=1)
    manana_s = manana.isoformat()

    tareas = db.tareas_listar()

    # Deadlines que vencen mañana
    deadlines_manana = [
        t for t in tareas
        if t["estado"] != "Completada"
        and t.get("fecha_fin") == manana_s
    ]

    # Tareas programadas para mañana (fecha fija, no deadline)
    tareas_manana = [
        t for t in tareas
        if t["estado"] != "Completada"
        and t.get("fecha") == manana_s
        and not t.get("fecha_fin")
    ]

    # Si no hay nada mañana, no mandamos nada
    if not deadlines_manana and not tareas_manana:
        return None

    dia_nombre   = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"][manana.weekday()]
    fecha_bonita = manana.strftime("%d/%m/%Y")

    lineas = [
        f"⏰ <b>Recordatorio — mañana {dia_nombre} {fecha_bonita}</b>",
        "",
    ]

    if deadlines_manana:
        lineas.append("🔴 <b>Deadlines que vencen mañana:</b>")
        for t in deadlines_manana:
            prio = t.get("prioridad", "Normal")
            lineas.append(f"  • [{prio}] {t['titulo']} — {t['tipo']}")
        lineas.append("")

    if tareas_manana:
        lineas.append("📝 <b>Tareas programadas para mañana:</b>")
        for t in tareas_manana:
            hora_t = f" a las {t['hora']}" if not t["dia_completo"] and t.get("hora") else ""
            lineas.append(f"  • [{t['prioridad']}] {t['titulo']}{hora_t}")
        lineas.append("")

    lineas.append("¡Prepáralo esta noche! 📚")

    return "\n".join(lineas)


# ─────────────────────── 3. AVISO PARTIDO ──────────────────────────────────

def construir_aviso_partido() -> str | None:
    """
    Devuelve un mensaje si hoy hay partido del Sevilla.
    Devuelve None si no hay partido → el workflow no manda nada.
    """
    hoy     = date.today()
    hoy_str = hoy.isoformat()

    futbol_all, _ = db.cache_leer("sevilla")
    partido_hoy   = [f for f in futbol_all if f.get("fecha") == hoy_str]

    if not partido_hoy:
        return None

    p      = partido_hoy[0]
    hora_p = p.get("hora")
    lugar  = p.get("aula", "?")

    # Calcular horas que quedan si hay hora definida
    horas_quedan = ""
    if hora_p:
        try:
            ahora        = datetime.now()
            partido_dt   = datetime.strptime(f"{hoy_str} {hora_p}", "%Y-%m-%d %H:%M")
            delta        = partido_dt - ahora
            horas        = int(delta.total_seconds() // 3600)
            minutos      = int((delta.total_seconds() % 3600) // 60)
            if delta.total_seconds() > 0:
                horas_quedan = f"\n⏱ Faltan <b>{horas}h {minutos}min</b>"
        except Exception:
            pass

    icono_lugar = "🏠" if lugar == "Casa" else "✈️"

    lineas = [
        f"⚽ <b>¡HOY HAY PARTIDO!</b>",
        "",
        f"<b>{p['titulo']}</b>",
        f"🕐 {hora_p or 'Hora por confirmar'}  {icono_lugar} {lugar}",
        horas_quedan,
        "",
        "¡Aupa el Sevilla! 🔴⚪",
    ]

    return "\n".join(l for l in lineas if l is not None)


# ─────────────────────── 4. RESUMEN SEMANAL ────────────────────────────────

def construir_resumen_semanal() -> str:
    hoy        = date.today()
    # Próximo lunes
    dias_hasta_lunes = (7 - hoy.weekday()) % 7 or 7
    lunes      = hoy + timedelta(days=dias_hasta_lunes)
    domingo    = lunes + timedelta(days=6)
    lunes_s    = lunes.isoformat()
    domingo_s  = domingo.isoformat()

    fecha_ini  = lunes.strftime("%d/%m")
    fecha_fin  = domingo.strftime("%d/%m")

    lineas = [
        f"📅 <b>Semana del {fecha_ini} al {fecha_fin}</b>",
        "",
    ]

    tareas = db.tareas_listar()

    # Deadlines de la semana
    deadlines_semana = sorted([
        t for t in tareas
        if t["estado"] != "Completada"
        and t.get("fecha_fin")
        and lunes_s <= t["fecha_fin"] <= domingo_s
    ], key=lambda x: x["fecha_fin"])

    # Tareas fijas de la semana
    tareas_semana = [
        t for t in tareas
        if t["estado"] != "Completada"
        and t.get("fecha")
        and lunes_s <= t["fecha"] <= domingo_s
        and not t.get("fecha_fin")
    ]

    # Clases de la semana
    clases_all, _ = db.cache_leer("loyola")
    clases_semana = [c for c in clases_all if lunes_s <= c.get("fecha","") <= domingo_s]

    # Partidos de la semana
    futbol_all, _ = db.cache_leer("sevilla")
    partidos_semana = [f for f in futbol_all if lunes_s <= f.get("fecha","") <= domingo_s]

    # Carga visual
    n_dl    = len(deadlines_semana)
    n_t     = len(tareas_semana)
    nivel   = _nivel_carga(n_dl, n_t)
    barra   = _barra_carga(n_dl * 2 + n_t)

    lineas.append(f"📊 <b>Carga semanal:</b> {nivel}")
    lineas.append(f"<code>{barra}</code>")
    lineas.append("")

    # Resumen numérico
    lineas.append(f"🎓 Clases: <b>{len(clases_semana)}</b>")
    lineas.append(f"📝 Tareas: <b>{n_t}</b>")
    lineas.append(f"⏰ Deadlines: <b>{n_dl}</b>")
    if partidos_semana:
        lineas.append(f"⚽ Partidos: <b>{len(partidos_semana)}</b>")
    lineas.append("")

    # Detalle deadlines
    if deadlines_semana:
        lineas.append("⏰ <b>Deadlines:</b>")
        dias_es = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"]
        for t in deadlines_semana:
            try:
                d     = date.fromisoformat(t["fecha_fin"])
                dia_n = dias_es[d.weekday()]
                fecha_fmt = f"{dia_n} {d.strftime('%d/%m')}"
            except Exception:
                fecha_fmt = t["fecha_fin"]
            prio_icon = "🔴" if t["prioridad"] == "Urgente" else "🟠" if t["prioridad"] == "Importante" else "🟡"
            lineas.append(f"  {prio_icon} {fecha_fmt} — {t['titulo']}")
        lineas.append("")

    # Detalle partidos
    if partidos_semana:
        lineas.append("⚽ <b>Partidos:</b>")
        for p in partidos_semana:
            try:
                d         = date.fromisoformat(p["fecha"])
                dias_es   = ["Lun","Mar","Mié","Jue","Vie","Sáb","Dom"]
                dia_n     = dias_es[d.weekday()]
                fecha_fmt = f"{dia_n} {d.strftime('%d/%m')}"
            except Exception:
                fecha_fmt = p["fecha"]
            hora_p = p.get("hora") or "TBD"
            lineas.append(f"  • {fecha_fmt} — {p['titulo']} {hora_p}")
        lineas.append("")

    lineas.append("¡Buena semana! 💪")
    return "\n".join(lineas)


# ─────────────────────────── MAIN ──────────────────────────────────────────

def main():
    token   = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("❌ Faltan TELEGRAM_TOKEN o TELEGRAM_CHAT_ID.")
        sys.exit(1)

    # Qué función ejecutar según el argumento
    modo = sys.argv[1] if len(sys.argv) > 1 else "matutino"
    print(f"Modo: {modo}")

    db.init_db()

    if modo == "matutino":
        mensaje = construir_resumen_matutino()

    elif modo == "deadlines":
        mensaje = construir_aviso_deadlines()
        if mensaje is None:
            print("✅ No hay deadlines ni tareas para mañana. No se manda nada.")
            sys.exit(0)

    elif modo == "partido":
        mensaje = construir_aviso_partido()
        if mensaje is None:
            print("✅ No hay partido hoy. No se manda nada.")
            sys.exit(0)

    elif modo == "semanal":
        mensaje = construir_resumen_semanal()

    else:
        print(f"❌ Modo desconocido: {modo}")
        sys.exit(1)

    print("── MENSAJE ──────────────────────────")
    print(mensaje)
    print("─────────────────────────────────────")

    ok = enviar_telegram(token, chat_id, mensaje)
    if ok:
        print("✅ Mensaje enviado correctamente.")
    else:
        print("❌ Error al enviar el mensaje.")
        sys.exit(1)


if __name__ == "__main__":
    main()
