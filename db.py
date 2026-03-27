"""
db.py — Capa de persistencia con Turso via HTTP API.
No requiere libsql-experimental ni Rust. Solo 'requests'.

Fallback automático a SQLite local si no hay credenciales Turso
(útil para desarrollo sin internet o sin secrets configurados).
"""

import json
import os
import sqlite3
import requests
from datetime import date, datetime
from pathlib import Path
from contextlib import contextmanager


# ─────────────────────────── CREDENCIALES ──────────────────────────────────

def _get_turso_credentials() -> tuple[str | None, str | None]:
    """Lee URL y token de Turso desde Streamlit secrets o variables de entorno."""
    # 1. Streamlit secrets (app en local o Streamlit Cloud)
    try:
        import streamlit as st
        url   = st.secrets["turso"]["url"]
        token = st.secrets["turso"]["token"]
        return url, token
    except Exception:
        pass

    # 2. Variables de entorno (GitHub Actions)
    url   = os.environ.get("TURSO_URL")
    token = os.environ.get("TURSO_TOKEN")
    if url and token:
        return url, token

    return None, None


# ─────────────────────────── TURSO HTTP ────────────────────────────────────

def _turso_execute(statements: list[dict]) -> list[list[dict]]:
    """
    Ejecuta una o varias sentencias SQL contra Turso via HTTP.
    
    statements: lista de { "q": "SQL...", "params": [...] }
    Devuelve: lista de resultados, uno por sentencia.
    Cada resultado es una lista de dicts (filas).
    """
    url, token = _get_turso_credentials()
    if not url or not token:
        raise RuntimeError("No hay credenciales Turso configuradas.")

    # La API de Turso espera la URL con /v2/pipeline
    endpoint = url.replace("libsql://", "https://") + "/v2/pipeline"

    payload = {
        "requests": [
            {"type": "execute", "stmt": {"sql": s["q"], "args": [
                {"type": "text", "value": str(v)} if v is not None else {"type": "null"}
                for v in s.get("params", [])
            ]}}
            for s in statements
        ] + [{"type": "close"}]
    }

    resp = requests.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type":  "application/json",
        },
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    resultados = []
    for item in data.get("results", []):
        if item.get("type") == "ok":
            rs = item.get("response", {}).get("result", {})
            cols = [c["name"] for c in rs.get("cols", [])]
            rows = []
            for row in rs.get("rows", []):
                fila = {}
                for col, val in zip(cols, row):
                    # val es {"type": "text"/"integer"/"null", "value": ...}
                    if val["type"] == "null":
                        fila[col] = None
                    elif val["type"] == "integer":
                        fila[col] = int(val["value"])
                    else:
                        fila[col] = val["value"]
                rows.append(fila)
            resultados.append(rows)
        elif item.get("type") == "error":
            raise RuntimeError(f"Turso error: {item.get('error')}")

    return resultados


def _usar_turso() -> bool:
    url, token = _get_turso_credentials()
    return bool(url and token)


# ─────────────────────────── SQLITE LOCAL (fallback) ───────────────────────

@contextmanager
def _local_conn():
    conn = sqlite3.connect(Path("autogestor.db"), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _local_fetchall(conn, sql: str, params: list = []) -> list[dict]:
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────── API PÚBLICA ───────────────────────────────────
# Todas las funciones de abajo funcionan igual independientemente de si
# estamos en Turso o SQLite local. app.py y notify.py no necesitan saber nada.

def _exec(sql: str, params: list = []) -> list[dict]:
    """Ejecuta una sentencia y devuelve filas (para SELECT)."""
    if _usar_turso():
        results = _turso_execute([{"q": sql, "params": params}])
        return results[0] if results else []
    else:
        with _local_conn() as conn:
            return _local_fetchall(conn, sql, params)


def _exec_write(sql: str, params: list = []) -> None:
    """Ejecuta una sentencia de escritura (INSERT/UPDATE/DELETE)."""
    if _usar_turso():
        _turso_execute([{"q": sql, "params": params}])
    else:
        with _local_conn() as conn:
            conn.execute(sql, params)


def _exec_many(statements: list[tuple[str, list]]) -> None:
    """Ejecuta varias sentencias de escritura en una sola llamada HTTP."""
    if _usar_turso():
        _turso_execute([{"q": sql, "params": p} for sql, p in statements])
    else:
        with _local_conn() as conn:
            for sql, p in statements:
                conn.execute(sql, p)


# ─────────────────────────── INICIALIZACIÓN ────────────────────────────────

def init_db():
    """Crea las tablas si no existen. Se llama al arrancar la app."""
    tablas = [
        ("""
        CREATE TABLE IF NOT EXISTS tareas (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo       TEXT    NOT NULL,
            tipo         TEXT    NOT NULL DEFAULT 'Otro',
            prioridad    TEXT    NOT NULL DEFAULT 'Normal',
            estado       TEXT    NOT NULL DEFAULT 'Pendiente',
            fecha        TEXT,
            fecha_fin    TEXT,
            hora         TEXT,
            dia_completo INTEGER NOT NULL DEFAULT 1,
            creado_en    TEXT    NOT NULL DEFAULT (date('now'))
        )""", []),
        ("""
        CREATE TABLE IF NOT EXISTS horario (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            titulo      TEXT    NOT NULL,
            ubicacion   TEXT,
            es_rutina   INTEGER NOT NULL DEFAULT 0,
            dias_semana TEXT    DEFAULT '[]',
            fecha       TEXT,
            hora_inicio TEXT    NOT NULL,
            hora_fin    TEXT    NOT NULL
        )""", []),
        ("""
        CREATE TABLE IF NOT EXISTS horario_cache (
            fuente      TEXT PRIMARY KEY,
            datos       TEXT NOT NULL,
            actualizado TEXT NOT NULL
        )""", []),
    ]
    _exec_many(tablas)


# ─────────────────────────── TAREAS ────────────────────────────────────────

def tareas_listar() -> list[dict]:
    return _exec(
        "SELECT * FROM tareas ORDER BY COALESCE(fecha_fin, fecha), hora"
    )


def tareas_crear(titulo: str, tipo: str, prioridad: str,
                 fecha: str | None, fecha_fin: str | None,
                 hora: str | None, dia_completo: bool) -> None:
    _exec_write(
        """INSERT INTO tareas (titulo, tipo, prioridad, fecha, fecha_fin, hora, dia_completo)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [titulo, tipo, prioridad, fecha, fecha_fin, hora, int(dia_completo)]
    )


def tareas_actualizar(id: int, **campos) -> None:
    if not campos:
        return
    if "dia_completo" in campos:
        campos["dia_completo"] = int(campos["dia_completo"])
    sets    = ", ".join(f"{k} = ?" for k in campos)
    valores = list(campos.values()) + [id]
    _exec_write(f"UPDATE tareas SET {sets} WHERE id = ?", valores)


def tareas_borrar(id: int) -> None:
    _exec_write("DELETE FROM tareas WHERE id = ?", [id])


def tareas_limpiar_viejas() -> int:
    hoy = date.today().isoformat()
    _exec_write(
        "DELETE FROM tareas WHERE estado = 'Completada' AND COALESCE(fecha_fin, fecha) < ?",
        [hoy]
    )
    return 0


# ─────────────────────────── HORARIO ───────────────────────────────────────

def horario_listar() -> list[dict]:
    rows = _exec("SELECT * FROM horario ORDER BY hora_inicio")
    for r in rows:
        r["dias_semana"] = json.loads(r.get("dias_semana") or "[]")
        r["es_rutina"]   = bool(r["es_rutina"])
    return rows


def horario_crear(titulo: str, ubicacion: str, es_rutina: bool,
                  dias_semana: list[int], fecha: str | None,
                  hora_inicio: str, hora_fin: str) -> None:
    _exec_write(
        """INSERT INTO horario (titulo, ubicacion, es_rutina, dias_semana, fecha, hora_inicio, hora_fin)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [titulo, ubicacion, int(es_rutina), json.dumps(dias_semana), fecha, hora_inicio, hora_fin]
    )


def horario_actualizar(id: int, **campos) -> None:
    if not campos:
        return
    if "es_rutina" in campos:
        campos["es_rutina"] = int(campos["es_rutina"])
    if "dias_semana" in campos:
        campos["dias_semana"] = json.dumps(campos["dias_semana"])
    sets    = ", ".join(f"{k} = ?" for k in campos)
    valores = list(campos.values()) + [id]
    _exec_write(f"UPDATE horario SET {sets} WHERE id = ?", valores)


def horario_borrar(id: int) -> None:
    _exec_write("DELETE FROM horario WHERE id = ?", [id])


# ─────────────────────────── CACHE SCRAPING ────────────────────────────────

def cache_leer(fuente: str) -> tuple[list, datetime | None]:
    rows = _exec(
        "SELECT datos, actualizado FROM horario_cache WHERE fuente = ?", [fuente]
    )
    if not rows:
        return [], None
    row = rows[0]
    ts  = datetime.fromisoformat(row["actualizado"])
    return json.loads(row["datos"]), ts


def cache_guardar(fuente: str, datos: list) -> None:
    now = datetime.now().isoformat()
    _exec_write(
        """INSERT INTO horario_cache (fuente, datos, actualizado) VALUES (?, ?, ?)
           ON CONFLICT(fuente) DO UPDATE
           SET datos = excluded.datos, actualizado = excluded.actualizado""",
        [fuente, json.dumps(datos, ensure_ascii=False), now]
    )