"""
bot.py — Bot de Telegram con Gemini.
Corre en Render como servicio web independiente.
Recibe mensajes de Telegram via webhook y responde usando Gemini
con el contexto de tareas y horario del usuario.

Dependencias: fastapi, uvicorn, requests
"""

import os
import json
import requests
from datetime import date, timedelta
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn

import db

app = FastAPI()

# ─────────────────────────── CREDENCIALES ──────────────────────────────────

def get_env(clave: str) -> str:
    val = os.environ.get(clave)
    if not val:
        raise RuntimeError(f"Falta variable de entorno: {clave}")
    return val


# ─────────────────────────── TELEGRAM ──────────────────────────────────────

def enviar_mensaje(chat_id: str, texto: str) -> None:
    token = get_env("TELEGRAM_TOKEN")
    url   = f"https://api.telegram.org/bot{token}/sendMessage"
    # Telegram tiene límite de 4096 caracteres por mensaje
    if len(texto) > 4000:
        texto = texto[:4000] + "\n\n_(mensaje recortado)_"
    requests.post(url, json={
        "chat_id":    chat_id,
        "text":       texto,
        "parse_mode": "HTML",
    }, timeout=10)


def registrar_webhook(url_publica: str) -> dict:
    """Registra el webhook de Telegram apuntando a esta app."""
    token    = get_env("TELEGRAM_TOKEN")
    endpoint = f"https://api.telegram.org/bot{token}/setWebhook"
    webhook  = f"{url_publica.rstrip('/')}/webhook"
    resp     = requests.post(endpoint, json={"url": webhook}, timeout=10)
    return resp.json()


# ─────────────────────────── CONTEXTO PARA GEMINI ──────────────────────────

def construir_contexto() -> str:
    """
    Construye un texto con todas las tareas y horario actual
    para mandárselo a Gemini como contexto.
    """
    hoy     = date.today()
    hoy_str = hoy.isoformat()
    dia_idx = hoy.weekday()
    dia_nombre = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"][dia_idx]

    lineas = [
        f"Hoy es {dia_nombre} {hoy.strftime('%d de %B de %Y')} (fecha ISO: {hoy_str}).",
        f"La semana empieza el lunes. Día de la semana de hoy: {dia_idx} (0=Lunes, 6=Domingo).",
        "",
    ]

    # ── Tareas pendientes ──
    try:
        tareas = db.tareas_listar()
        pendientes = [t for t in tareas if t["estado"] != "Completada"]
        completadas = [t for t in tareas if t["estado"] == "Completada"]

        if pendientes:
            lineas.append("=== TAREAS PENDIENTES ===")
            for t in pendientes:
                ref  = t.get("fecha_fin") or t.get("fecha", "sin fecha")
                hora = f" a las {t['hora']}" if t.get("hora") and not t["dia_completo"] else ""
                tipo = "DEADLINE" if t.get("fecha_fin") else "fecha fija"
                try:
                    dias = (date.fromisoformat(ref) - hoy).days
                    estado_dias = f"({dias} días)" if dias >= 0 else f"(ATRASADA {abs(dias)} días)"
                except Exception:
                    estado_dias = ""
                lineas.append(
                    f"- ID:{t['id']} | {t['titulo']} | tipo:{t['tipo']} | "
                    f"prioridad:{t['prioridad']} | {tipo}:{ref}{hora} {estado_dias}"
                )
        else:
            lineas.append("=== TAREAS PENDIENTES: ninguna ===")

        if completadas:
            lineas.append(f"\n=== TAREAS COMPLETADAS RECIENTES ({len(completadas)}) ===")
            for t in completadas[-5:]:  # Solo las 5 últimas
                ref = t.get("fecha_fin") or t.get("fecha", "?")
                lineas.append(f"- {t['titulo']} | {t['tipo']} | fecha:{ref}")

    except Exception as e:
        lineas.append(f"Error leyendo tareas: {e}")

    lineas.append("")

    # ── Horario rutinas y eventos ──
    try:
        horario = db.horario_listar()
        if horario:
            lineas.append("=== HORARIO / RUTINAS Y EVENTOS MANUALES ===")
            dias_map = ["Lunes","Martes","Miércoles","Jueves","Viernes","Sábado","Domingo"]
            for ev in horario:
                if ev["es_rutina"]:
                    dias_str = ", ".join(dias_map[i] for i in ev["dias_semana"])
                    lineas.append(
                        f"- RUTINA: {ev['titulo']} | {ev['hora_inicio']}-{ev['hora_fin']} | "
                        f"días: {dias_str} | lugar: {ev.get('ubicacion','?')}"
                    )
                else:
                    lineas.append(
                        f"- EVENTO: {ev['titulo']} | {ev['hora_inicio']}-{ev['hora_fin']} | "
                        f"fecha: {ev.get('fecha','?')} | lugar: {ev.get('ubicacion','?')}"
                    )
    except Exception as e:
        lineas.append(f"Error leyendo horario: {e}")

    lineas.append("")

    # ── Cache Loyola (clases) ──
    try:
        clases, ts_l = db.cache_leer("loyola")
        if clases:
            # Todas las clases futuras (para que Gemini pueda contar por asignatura)
            futuras = sorted(
                [c for c in clases if c.get("fecha","") >= hoy_str],
                key=lambda x: x.get("fecha","")
            )
            lineas.append(f"=== CLASES UNIVERSIDAD ({len(futuras)} clases futuras) ===")
            # Resumen por asignatura primero
            from collections import Counter
            conteo = Counter(c["asignatura"] for c in futuras)
            lineas.append("-- Clases restantes por asignatura:")
            for asig, n in sorted(conteo.items()):
                lineas.append(f"  {asig}: {n} clases restantes")
            lineas.append("-- Detalle completo:")
            for c in futuras:
                lineas.append(
                    f"- {c['fecha']} | {c['hora']} | {c['asignatura']} | aula:{c.get('aula','?')}"
                )
        else:
            lineas.append("=== CLASES UNIVERSIDAD: cache vacío (usar botón Actualizar Loyola) ===")
    except Exception as e:
        lineas.append(f"Error leyendo clases: {e}")

    lineas.append("")

    # ── Cache Sevilla FC ──
    try:
        futbol, ts_f = db.cache_leer("sevilla")
        if futbol:
            lineas.append(f"=== PARTIDOS SEVILLA FC (próximos) ===")
            proximos = [f for f in futbol if f.get("fecha","") >= hoy_str]
            for p in proximos[:5]:
                hora_p = p.get("hora") or "hora TBD"
                lineas.append(
                    f"- {p['fecha']} | {hora_p} | {p['titulo']} | {p.get('aula','?')}"
                )
            if not proximos:
                lineas.append("(no hay partidos próximos en el cache)")
        else:
            lineas.append("=== PARTIDOS SEVILLA FC: cache vacío ===")
    except Exception as e:
        lineas.append(f"Error leyendo fútbol: {e}")

    return "\n".join(lineas)


# ─────────────────────────── GEMINI ────────────────────────────────────────

SYSTEM_PROMPT = """Eres el asistente personal de un estudiante universitario en Sevilla.
Tienes acceso a su agenda completa: tareas, deadlines, horario de clases, rutinas y partidos del Sevilla FC.

REGLAS:
- Responde SIEMPRE en español
- Sé conciso pero completo. Usa emojis para que sea visual y fácil de leer.
- Cuando te pregunten por un día concreto, busca en los datos TODAS las clases, tareas y eventos de ese día.
- Para calcular fechas usa la fecha de hoy que te doy en el contexto.
- Si no hay datos para algo, dilo claramente.
- Nunca inventes tareas o eventos que no estén en los datos.
- Formato de respuesta: usa saltos de línea y emojis para estructurar bien.
- Si la pregunta no tiene que ver con la agenda, responde que solo puedes ayudar con eso.

FORMATO SUGERIDO para consultas de día:
📅 <fecha bonita>

🎓 Clases:
  • HH:MM - Asignatura (Aula)

📝 Tareas:
  • Título [prioridad]

⏰ Deadlines:
  • Título (vence en X días)

Si no hay algo, omite esa sección."""


def preguntar_gemini(pregunta: str, contexto: str) -> str:
    """Llama a la API de Gemini y devuelve la respuesta."""
    api_key = get_env("GEMINI_API_KEY")
    url     = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-2.5-flash:generateContent?key={api_key}"
    )

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": (
                    f"{SYSTEM_PROMPT}\n\n"
                    f"=== DATOS ACTUALES DE LA AGENDA ===\n{contexto}\n\n"
                    f"=== PREGUNTA DEL USUARIO ===\n{pregunta}"
                )}]
            }
        ],
        "generationConfig": {
            "temperature":     0.3,   # Respuestas más precisas y menos creativas
            "maxOutputTokens": 1024,
        }
    }

    try:
        resp = requests.post(url, json=payload, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except requests.exceptions.Timeout:
        return "⏳ Gemini tardó demasiado en responder. Inténtalo de nuevo."
    except Exception as e:
        return f"❌ Error llamando a Gemini: {e}"


# ─────────────────────────── SEGURIDAD ─────────────────────────────────────

def es_autorizado(chat_id: str) -> bool:
    """Solo responde a tu chat_id para evitar que otros usen el bot."""
    mi_chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    return str(chat_id) == str(mi_chat_id)


# ─────────────────────────── ENDPOINTS ─────────────────────────────────────

@app.get("/")
def health():
    """Health check — Render lo usa para saber si el servicio está vivo."""
    return {"status": "ok", "servicio": "AutoGestor Bot"}


@app.get("/setup")
def setup():
    """
    Registra el webhook de Telegram apuntando a esta app.
    Llámalo UNA VEZ después de desplegar en Render:
    https://tu-app.onrender.com/setup
    """
    url_publica = os.environ.get("RENDER_EXTERNAL_URL", "")
    if not url_publica:
        return {"error": "No se encontró RENDER_EXTERNAL_URL. ¿Estás en Render?"}
    resultado = registrar_webhook(url_publica)
    return resultado


@app.post("/webhook")
async def webhook(request: Request):
    """Recibe los mensajes de Telegram y responde."""
    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"ok": True})

    # Extraer mensaje
    message = data.get("message") or data.get("edited_message")
    if not message:
        return JSONResponse({"ok": True})

    chat_id = str(message.get("chat", {}).get("id", ""))
    texto   = message.get("text", "").strip()

    if not chat_id or not texto:
        return JSONResponse({"ok": True})

    # Verificar que eres tú
    if not es_autorizado(chat_id):
        enviar_mensaje(chat_id, "⛔ No tienes permiso para usar este bot.")
        return JSONResponse({"ok": True})

    # Comando /start
    if texto == "/start":
        enviar_mensaje(chat_id,
            "👋 ¡Hola! Soy tu asistente de agenda.\n\n"
            "Puedes preguntarme cosas como:\n"
            "• <i>¿Qué tengo el 17 de abril?</i>\n"
            "• <i>¿Tengo algo esta semana?</i>\n"
            "• <i>¿Cuándo es el próximo partido del Sevilla?</i>\n"
            "• <i>¿Qué deadlines tengo esta semana?</i>\n\n"
            "Pregúntame lo que quieras 📅"
        )
        return JSONResponse({"ok": True})

    # Para cualquier otro mensaje → Gemini
    try:
        # Indicador de que está procesando
        enviar_mensaje(chat_id, "⏳ Consultando tu agenda...")

        contexto  = construir_contexto()
        respuesta = preguntar_gemini(texto, contexto)
        enviar_mensaje(chat_id, respuesta)

    except Exception as e:
        enviar_mensaje(chat_id, f"❌ Error inesperado: {e}")

    return JSONResponse({"ok": True})