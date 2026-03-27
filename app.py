"""
app.py — AutoGestor: app principal de Streamlit.

Arquitectura:
    db.py      → toda la persistencia (SQLite)
    scraper.py → scraping Loyola + Sevilla FC
    app.py     → UI únicamente (este archivo)
"""

import calendar
import json
from datetime import date, datetime, timedelta, time

import pytz
import streamlit as st

import db
import scraper

# ─────────────────────────── CONFIG ────────────────────────────────────────

st.set_page_config(page_title="AutoGestor", layout="wide",
                   initial_sidebar_state="expanded")

TIMEZONE = pytz.timezone("Europe/Madrid")

TIPOS_TAREA = ["Examen", "Entrega", "Estudio", "Clase", "Otro"]

COLORES_TIPO = {
    "Examen":   "#FF4B4B",
    "Entrega":  "#FFA500",
    "Estudio":  "#1E90FF",
    "Clase":  "#9370DB",
    "Otro":     "#808080",
    "Clase":    "#2E8B57",
    "Futbol":   "#FFFFFF",
}

DIAS_ABR  = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
MESES_ES  = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
             "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]

CACHE_HORAS = 12   # horas antes de considerar el cache caducado


# ─────────────────────────── HELPERS ───────────────────────────────────────

def ahora_madrid() -> datetime:
    return datetime.now(TIMEZONE)

def hoy_madrid() -> date:
    return ahora_madrid().date()

def fecha_str(d: date) -> str:
    return d.isoformat()

def notificar(tipo: str, texto: str):
    st.session_state["_notif"] = {"tipo": tipo, "texto": texto}

def mostrar_notificacion():
    n = st.session_state.pop("_notif", None)
    if n:
        (st.success if n["tipo"] == "exito" else st.error)(n["texto"])


# ─────────────────────── CACHE SCRAPING ────────────────────────────────────

def cargar_cache(fuente: str) -> list[dict]:
    """Lee cache de DB; si es viejo o inexistente devuelve []."""
    datos, ts = db.cache_leer(fuente)
    if ts and (ahora_madrid().replace(tzinfo=None) - ts).total_seconds() < CACHE_HORAS * 3600:
        return datos
    return []

def actualizar_scraping(fuente: str, force: bool = False):
    """Llama al scraper correspondiente y guarda en cache."""
    if not force:
        datos, ts = db.cache_leer(fuente)
        if ts and (ahora_madrid().replace(tzinfo=None) - ts).total_seconds() < CACHE_HORAS * 3600:
            return  # aún válido

    try:
        if fuente == "loyola":
            datos = scraper.scrape_loyola()
        else:
            datos = scraper.scrape_sevilla()
        db.cache_guardar(fuente, datos)
        notificar("exito", f"✅ {fuente.capitalize()} actualizado ({len(datos)} entradas)")
    except Exception as e:
        notificar("error", f"❌ Error actualizando {fuente}: {e}")


# ─────────────────────── ITEMS DEL DÍA ─────────────────────────────────────

def items_del_dia(dia: date, tareas: list, horario: list,
                  loyola: list, futbol: list) -> list[dict]:
    """
    Devuelve todos los eventos/tareas de un día como lista unificada de dicts:
        { tipo, titulo, hora_sort, hora, subtitulo, raw, icon }
    Ordenados por hora.
    """
    dia_s    = fecha_str(dia)
    dia_idx  = dia.weekday()   # 0=Lunes
    items    = []

    # 1. Clases Loyola
    for c in loyola:
        if c["fecha"] == dia_s:
            h = c["hora"].split("-")[0].strip()
            items.append({
                "tipo": "Clase", "titulo": c["asignatura"],
                "hora_sort": h, "hora": c["hora"],
                "subtitulo": f"📍 {c['aula']}", "icon": "🎓",
                "raw": {**c, "tipo": "Clase", "es_universidad": True},
            })

    # 2. Partidos Sevilla
    for f in futbol:
        if f["fecha"] == dia_s:
            h = f["hora"] or "00:00"
            items.append({
                "tipo": "Futbol", "titulo": f["titulo"],
                "hora_sort": h, "hora": f["hora"] or "TBD",
                "subtitulo": f['aula'], "icon": "⚽",
                "raw": {**f, "tipo": "Futbol"},
            })

    # 3. Horario dinámico (rutinas y eventos)
    for ev in horario:
        es_hoy = (ev["es_rutina"] and dia_idx in ev["dias_semana"]) \
              or (not ev["es_rutina"] and ev.get("fecha") == dia_s)
        if es_hoy:
            items.append({
                "tipo": "Evento", "titulo": ev["titulo"],
                "hora_sort": ev["hora_inicio"], "hora": f"{ev['hora_inicio']} - {ev['hora_fin']}",
                "subtitulo": ev.get("ubicacion", ""), "icon": "🔄" if ev["es_rutina"] else "📅",
                "raw": {**ev},
            })

    # 4. Tareas
    for t in tareas:
        if t["estado"] == "Completada":
            continue
        es_deadline = bool(t.get("fecha_fin"))
        if es_deadline and t["fecha_fin"] == dia_s:
            icon, tipo_v = "⏰", "Deadline"
        elif not es_deadline and t.get("fecha") == dia_s:
            icon, tipo_v = "📝", "Tarea"
        else:
            continue

        hora_s = t.get("hora") or ("00:00" if t["dia_completo"] else "23:59")
        items.append({
            "tipo": tipo_v, "titulo": t["titulo"],
            "hora_sort": hora_s, "hora": t.get("hora"),
            "subtitulo": t.get("prioridad", "Normal"),
            "icon": icon, "raw": {**t, "tipo": "tarea"},
        })

    items.sort(key=lambda x: x["hora_sort"].replace(":", "") if x["hora_sort"] else "9999")
    return items


# ─────────────────────────── DIALOG ────────────────────────────────────────

@st.dialog("Detalle")
def dialogo_detalle(item: dict):
    raw   = item["raw"]
    tipo  = raw.get("tipo", "")
    titulo = raw.get("titulo", raw.get("asignatura", "Sin título"))

    st.subheader(f"{item['icon']} {titulo}")
    st.caption(f"Tipo: {tipo}")
    st.divider()

    c1, c2 = st.columns(2)
    hora_display = item["hora"] or "Todo el día"
    c1.markdown(f"**🕒 Hora:** {hora_display}")
    if raw.get("aula"):      c1.markdown(f"**📍 Aula:** {raw['aula']}")
    if raw.get("ubicacion"): c1.markdown(f"**📍 Ubicación:** {raw['ubicacion']}")
    if raw.get("fecha"):     c2.markdown(f"**📅 Fecha:** {raw['fecha']}")
    if raw.get("fecha_fin"): c2.markdown(f"**⏰ Deadline:** {raw['fecha_fin']}")
    if raw.get("prioridad"): c2.markdown(f"**🚨 Prioridad:** {raw['prioridad']}")
    if raw.get("dias_semana"):
        ds = [DIAS_ABR[i] for i in raw["dias_semana"]]
        c2.markdown(f"**🔄 Días:** {', '.join(ds)}")

    st.divider()

    # Acciones según tipo
    if tipo == "tarea":
        if raw.get("estado") != "Completada":
            if st.button("✅ Marcar como Completada", use_container_width=True, type="primary"):
                db.tareas_actualizar(raw["id"], estado="Completada")
                notificar("exito", "Tarea completada ✅")
                st.rerun()
        else:
            st.success("Ya completada")

    elif tipo in ("Clase",) or raw.get("es_universidad"):
        st.info("Evento del horario universitario oficial.")

    elif raw.get("id"):
        if st.button("🗑️ Eliminar evento", type="primary", use_container_width=True):
            db.horario_borrar(raw["id"])
            notificar("exito", "Evento eliminado 🗑️")
            st.rerun()


# ─────────────────────────── VISTAS ────────────────────────────────────────

def vista_diaria(tareas, horario, loyola, futbol, fecha: date):
    hoy = hoy_madrid()

    # Banner tareas atrasadas
    atrasadas = [
        t for t in tareas
        if t["estado"] != "Completada"
        and (t.get("fecha_fin") or t.get("fecha", "9999")) < fecha_str(hoy)
    ]
    if atrasadas:
        st.error(f"🚨 {len(atrasadas)} tarea(s) atrasada(s)")
        with st.expander("Ver atrasadas"):
            for t in atrasadas:
                ref = t.get("fecha_fin") or t.get("fecha", "?")
                st.markdown(f"🔴 **{t['titulo']}** — {ref}")

    col_h, col_t = st.columns([1, 2])

    # ── Horario ──
    with col_h:
        st.subheader("🏫 Horario del día")
        eventos_dia = [i for i in items_del_dia(fecha, [], horario, loyola, futbol)
                       if i["tipo"] in ("Clase", "Futbol", "Evento")]
        if eventos_dia:
            for ev in eventos_dia:
                st.success(f"**{ev['hora']}**\n\n{ev['icon']} {ev['titulo']}\n\n{ev['subtitulo']}")
        else:
            st.info("Sin clases ni eventos hoy.")

    # ── Tareas ──
    with col_t:
        st.subheader(f"📝 {fecha.strftime('%A %d %b')}")
        items_t = [i for i in items_del_dia(fecha, tareas, [], [], [])
                   if i["tipo"] in ("Tarea", "Deadline")]

        if not items_t:
            st.info("✅ Sin tareas para este día.")

        tareas_dia       = [i for i in items_t if i["tipo"] == "Tarea"]
        deadlines_hoy    = [i for i in items_t if i["tipo"] == "Deadline"]

        def _tarjeta(i):
            raw   = i["raw"]
            color = COLORES_TIPO.get(raw.get("tipo", "Otro"), "#888")
            hora_badge = (f"<span style='background:#333;color:#fff;"
                          f"padding:2px 6px;border-radius:4px;font-size:.8em'>"
                          f"🕒 {raw['hora']}</span> "
                          if raw.get("hora") else "")
            with st.container(border=True):
                c1, c2 = st.columns([5, 1])
                c1.markdown(
                    f"{hora_badge}<strong>{raw['titulo']}</strong> "
                    f"<span style='background:{color};color:#fff;"
                    f"padding:2px 6px;border-radius:4px;font-size:.8em'>"
                    f"{raw.get('tipo','')}</span>",
                    unsafe_allow_html=True
                )
                if raw.get("estado") != "Completada":
                    if c2.button("✅", key=f"ok_d_{raw['id']}"):
                        db.tareas_actualizar(raw["id"], estado="Completada")
                        st.rerun()
                else:
                    c2.write("✅")

        if tareas_dia:
            st.markdown("### 📅 Tareas del día")
            for i in tareas_dia:
                _tarjeta(i)

        if deadlines_hoy and fecha == hoy:
            st.markdown("### ⏰ Deadlines")
            for i in deadlines_hoy:
                _tarjeta(i)


def _semana_css():
    st.markdown("""<style>
        @media (max-width:900px) {
            div[data-testid="stHorizontalBlock"] {
                flex-direction:row!important; flex-wrap:nowrap!important; gap:0!important;
            }
            div[data-testid="column"] { flex:1 1 0!important; min-width:0!important; padding:0!important; }
        }
        @media (orientation:portrait) and (max-width:600px) {
            div[data-testid="stHorizontalBlock"] {
                display:grid!important; grid-template-columns:repeat(7,1fr)!important; gap:1px!important;
            }
            div[data-testid="column"] { width:auto!important; min-width:0!important; padding:0!important; }
            div[data-testid="column"] p { font-size:3vw!important; text-align:center!important; margin:0!important; }
            div[data-testid="stButton"] button { font-size:4vw!important; padding:0!important;
                min-height:25px!important; border:none!important; background:transparent!important; }
            div[data-testid="stButton"] button p {
                max-width:1.5em!important; overflow:hidden!important; margin:0 auto!important; }
            div[data-testid="stDialog"] div[data-testid="stHorizontalBlock"] {
                display:flex!important; flex-direction:column!important; }
        }
    </style>""", unsafe_allow_html=True)


def vista_semanal(tareas, horario, loyola, futbol, fecha: date):
    _semana_css()
    st.subheader("Vista Semanal")
    lunes = fecha - timedelta(days=fecha.weekday())
    cols  = st.columns(7)

    for i, col in enumerate(cols):
        dia = lunes + timedelta(days=i)
        is_today    = dia == hoy_madrid()
        is_selected = dia == fecha

        if is_selected:
            bg, border, tc = "#1E90FF", "3px solid #1E90FF", "white"
        elif is_today:
            bg, border, tc = "#FF4B4B", "3px solid #FF4B4B", "white"
        else:
            bg, border, tc = "transparent", "1px solid #444", "var(--text-color)"

        with col:
            st.markdown(f"""
            <div style='text-align:center;border-bottom:{border};margin-bottom:5px'>
              <div style='background:{bg};color:{tc};border-radius:4px 4px 0 0;padding:2px'>
                <strong>{DIAS_ABR[i]}</strong>
              </div>
              <div style='font-size:1.2em;padding:5px'>{dia.day}</div>
            </div>""", unsafe_allow_html=True)

            for item in items_del_dia(dia, tareas, horario, loyola, futbol):
                trunc = (item["titulo"][:10] + "..") if len(item["titulo"]) > 10 else item["titulo"]
                label = f"{item['icon']} {item['hora_sort']} {trunc}"
                key   = f"sw_{i}_{dia}_{item['hora_sort']}_{item['titulo'][:6]}"
                if st.button(label, key=key, use_container_width=True):
                    dialogo_detalle(item)


def vista_mensual(tareas, horario, loyola, futbol, fecha: date):
    _semana_css()
    st.subheader(f"Vista Mensual — {MESES_ES[fecha.month-1]} {fecha.year}")

    calendar.setfirstweekday(calendar.MONDAY)
    cal = calendar.monthcalendar(fecha.year, fecha.month)

    for semana in cal:
        cols = st.columns(7)
        for i, day_num in enumerate(semana):
            with cols[i]:
                if day_num == 0:
                    st.markdown("<div style='min-height:80px'></div>", unsafe_allow_html=True)
                    continue

                dia = date(fecha.year, fecha.month, day_num)
                is_today    = dia == hoy_madrid()
                is_selected = dia == fecha

                color_num = "#FF4B4B" if is_today else "#1E90FF" if is_selected else "#AAA"
                border    = f"2px solid {color_num}" if (is_today or is_selected) else "1px solid #444"

                st.markdown(
                    f"<div style='text-align:right;font-weight:bold;border-bottom:{border};"
                    f"margin-bottom:4px;color:{color_num}'>{day_num} "
                    f"<span style='font-size:.75em;opacity:.7;font-weight:normal'>{DIAS_ABR[i]}</span></div>",
                    unsafe_allow_html=True
                )

                for item in items_del_dia(dia, tareas, horario, loyola, futbol):
                    trunc = (item["titulo"][:8] + "..") if len(item["titulo"]) > 8 else item["titulo"]
                    label = f"{item['icon']} {trunc}"
                    key   = f"sm_{day_num}_{i}_{item['hora_sort']}_{item['titulo'][:6]}"
                    if st.button(label, key=key, use_container_width=True,
                                 help=f"{item['hora_sort']} — {item['titulo']}"):
                        dialogo_detalle(item)


# ─────────────────────── FORMULARIOS ───────────────────────────────────────

def vista_nueva_tarea():
    st.subheader("➕ Nueva Tarea")
    with st.container(border=True):
        c_conf, c_form = st.columns([1, 3])

        with c_conf:
            modo = st.radio("Modo", ["📅 Día concreto", "⏰ Deadline"])

        with c_form:
            titulo = st.text_input("Título *")
            c1, c2 = st.columns(2)

            if "Deadline" in modo:
                f_fin = c1.date_input("Fecha límite", hoy_madrid())
                f_ini = None
            else:
                f_ini = c1.date_input("Fecha de realización", hoy_madrid())
                f_fin = None

            dia_completo = c2.checkbox("Todo el día", value=True)
            hora = None
            if not dia_completo:
                hora = c2.time_input("Hora", time(10, 0), step=900)

            prio = c1.selectbox("Prioridad", ["Normal", "Importante", "Urgente"])
            tipo = c2.selectbox("Tipo", TIPOS_TAREA)

            if st.button("💾 Guardar", type="primary", use_container_width=True):
                if not titulo.strip():
                    st.error("El título es obligatorio.")
                    return
                db.tareas_crear(
                    titulo=titulo.strip(), tipo=tipo, prioridad=prio,
                    fecha=fecha_str(f_ini) if f_ini else fecha_str(hoy_madrid()),
                    fecha_fin=fecha_str(f_fin) if f_fin else None,
                    hora=hora.strftime("%H:%M") if hora else None,
                    dia_completo=dia_completo,
                )
                notificar("exito", "✅ Tarea guardada")
                st.rerun()


def vista_nuevo_evento():
    st.subheader("➕ Nuevo Evento / Horario")
    with st.container(border=True):
        c_conf, c_form = st.columns([1, 3])

        with c_conf:
            tipo_entrada = st.radio("Tipo", ["🔄 Rutina Semanal", "📅 Evento Único"])
            st.caption("Rutina: se repite cada semana.\nEvento: ocurre un día específico.")

        with c_form:
            titulo    = st.text_input("Título / Asignatura")
            ubicacion = st.text_input("Ubicación / Aula")

            dias_sel  = []
            fecha_ev  = None

            if "Rutina" in tipo_entrada:
                st.write("Días de la semana:")
                cols_d = st.columns(7)
                for idx, col in enumerate(cols_d):
                    if col.checkbox(DIAS_ABR[idx], key=f"chk_dia_{idx}"):
                        dias_sel.append(idx)
            else:
                fecha_ev = st.date_input("Fecha del evento", hoy_madrid())

            c1, c2 = st.columns(2)
            h_ini = c1.time_input("Hora inicio", time(10, 0))
            h_fin = c2.time_input("Hora fin",    time(11, 0))

            if st.button("💾 Guardar", type="primary", use_container_width=True):
                if not titulo.strip():
                    st.error("El título es obligatorio.")
                    return
                es_rutina = "Rutina" in tipo_entrada
                if es_rutina and not dias_sel:
                    st.error("Selecciona al menos un día.")
                    return
                db.horario_crear(
                    titulo=titulo.strip(), ubicacion=ubicacion,
                    es_rutina=es_rutina, dias_semana=dias_sel,
                    fecha=fecha_str(fecha_ev) if fecha_ev else None,
                    hora_inicio=h_ini.strftime("%H:%M"),
                    hora_fin=h_fin.strftime("%H:%M"),
                )
                notificar("exito", "✅ Evento guardado")
                st.rerun()


def _tarjeta_tarea_gestion(t: dict):
    """Tarjeta reutilizable para la lista de gestión."""
    color_prio = {"Urgente": "red", "Importante": "orange"}.get(t["prioridad"], "green")
    opac = "0.5" if t["estado"] == "Completada" else "1"
    icon = "✅" if t["estado"] == "Completada" else "⬜"

    ref = t.get("fecha_fin") or t.get("fecha", "?")
    hora_str = f" @ {t['hora']}" if not t["dia_completo"] and t.get("hora") else ""

    with st.container(border=True):
        c_info, c_btn = st.columns([5, 2])
        with c_info:
            st.markdown(
                f"<h4 style='margin:0;opacity:{opac}'>{icon} {t['titulo']}</h4>",
                unsafe_allow_html=True
            )
            st.markdown(
                f"<span style='color:{color_prio};font-weight:bold'>{t['prioridad']}</span>"
                f" | {t['tipo']} | **{ref}{hora_str}**",
                unsafe_allow_html=True
            )

        with c_btn:
            ca1, ca2, ca3 = st.columns(3)

            # Completar / Deshacer
            if t["estado"] != "Completada":
                if ca1.button("✅", key=f"g_ok_{t['id']}", help="Completar"):
                    db.tareas_actualizar(t["id"], estado="Completada")
                    st.rerun()
            else:
                if ca1.button("↩️", key=f"g_undo_{t['id']}", help="Deshacer"):
                    db.tareas_actualizar(t["id"], estado="Pendiente")
                    st.rerun()

            # Editar
            with ca2.popover("✏️"):
                with st.form(f"edit_t_{t['id']}"):
                    e_tit    = st.text_input("Título", t["titulo"])
                    es_dl    = bool(t.get("fecha_fin"))
                    e_fecha  = st.date_input(
                        "Deadline" if es_dl else "Fecha",
                        datetime.strptime(t.get("fecha_fin") or t.get("fecha", fecha_str(hoy_madrid())), "%Y-%m-%d").date()
                    )
                    e_all    = st.checkbox("Todo el día", value=bool(t.get("dia_completo", True)))
                    e_hora   = None
                    if not e_all:
                        e_hora = st.time_input("Hora",
                            datetime.strptime(t.get("hora") or "09:00", "%H:%M").time())
                    e_estado = st.selectbox("Estado", ["Pendiente", "Completada"],
                                            index=0 if t["estado"] == "Pendiente" else 1)
                    e_prio   = st.selectbox("Prioridad", ["Normal", "Importante", "Urgente"],
                                            index=["Normal","Importante","Urgente"].index(t["prioridad"]))
                    if st.form_submit_button("Guardar"):
                        campos = dict(
                            titulo=e_tit, estado=e_estado, prioridad=e_prio,
                            dia_completo=e_all,
                            hora=e_hora.strftime("%H:%M") if e_hora else None,
                        )
                        if es_dl: campos["fecha_fin"] = fecha_str(e_fecha)
                        else:     campos["fecha"]     = fecha_str(e_fecha)
                        db.tareas_actualizar(t["id"], **campos)
                        notificar("exito", "✏️ Tarea actualizada")
                        st.rerun()

            # Borrar
            if ca3.button("🗑️", key=f"g_del_{t['id']}", help="Borrar"):
                db.tareas_borrar(t["id"])
                notificar("exito", "🗑️ Tarea eliminada")
                st.rerun()


def _tarjeta_evento_gestion(ev: dict):
    icon = "🔄" if ev["es_rutina"] else "📅"
    dias_txt = ", ".join(DIAS_ABR[i] for i in ev["dias_semana"]) if ev["es_rutina"] else ev.get("fecha","?")

    with st.container(border=True):
        c1, c2 = st.columns([5, 2])
        c1.markdown(f"**{icon} {ev['titulo']}** ({ev['hora_inicio']} - {ev['hora_fin']})")
        c1.caption(f"{ev.get('ubicacion','')} | {dias_txt}")

        with c2:
            ca_e, ca_d = st.columns(2)

            with ca_e.popover("✏️"):
                with st.form(f"edit_ev_{ev['id']}"):
                    e_tit  = st.text_input("Título", ev["titulo"])
                    e_ubi  = st.text_input("Ubicación", ev.get("ubicacion",""))
                    e_rut  = st.toggle("Rutina semanal", ev["es_rutina"])
                    e_dias = []
                    e_fev  = None
                    if e_rut:
                        sel = st.multiselect("Días", DIAS_ABR,
                                             default=[DIAS_ABR[i] for i in ev["dias_semana"]])
                        e_dias = [DIAS_ABR.index(d) for d in sel]
                    else:
                        try:
                            def_f = datetime.strptime(ev.get("fecha", fecha_str(hoy_madrid())), "%Y-%m-%d").date()
                        except Exception:
                            def_f = hoy_madrid()
                        e_fev = st.date_input("Fecha", def_f)

                    try: t_i = datetime.strptime(ev.get("hora_inicio","09:00"), "%H:%M").time()
                    except: t_i = time(9,0)
                    try: t_f = datetime.strptime(ev.get("hora_fin","10:00"), "%H:%M").time()
                    except: t_f = time(10,0)

                    e_hi = st.time_input("Inicio", t_i)
                    e_hf = st.time_input("Fin",    t_f)

                    if st.form_submit_button("Guardar"):
                        db.horario_actualizar(ev["id"],
                            titulo=e_tit, ubicacion=e_ubi, es_rutina=e_rut,
                            dias_semana=e_dias,
                            fecha=fecha_str(e_fev) if e_fev else None,
                            hora_inicio=e_hi.strftime("%H:%M"),
                            hora_fin=e_hf.strftime("%H:%M"),
                        )
                        notificar("exito", "✏️ Evento actualizado")
                        st.rerun()

            if ca_d.button("🗑️", key=f"g_dev_del_{ev['id']}"):
                db.horario_borrar(ev["id"])
                notificar("exito", "🗑️ Evento eliminado")
                st.rerun()


def vista_gestionar(tareas):
    st.subheader("📋 Gestión Global")
    tab_t, tab_h = st.tabs(["📝 Tareas", "📅 Horarios"])

    with tab_t:
        pendientes  = sorted(
            [t for t in tareas if t["estado"] != "Completada"],
            key=lambda t: ({"Urgente":0,"Importante":1,"Normal":2}.get(t["prioridad"],3),
                           t.get("fecha_fin") or t.get("fecha","9999"))
        )
        completadas = [t for t in tareas if t["estado"] == "Completada"]

        st.markdown(f"**Pendientes: {len(pendientes)}**")
        for t in pendientes:
            _tarjeta_tarea_gestion(t)
        st.divider()
        with st.expander(f"Completadas ({len(completadas)})"):
            for t in completadas:
                _tarjeta_tarea_gestion(t)

    with tab_h:
        horario = db.horario_listar()
        if not horario:
            st.info("No hay horarios ni eventos creados.")
            return
        eventos  = sorted([h for h in horario if not h["es_rutina"]], key=lambda x: x.get("fecha",""))
        rutinas  = [h for h in horario if h["es_rutina"]]
        for ev in eventos + rutinas:
            _tarjeta_evento_gestion(ev)


# ─────────────────────────── MAIN ──────────────────────────────────────────

def main():
    db.init_db()

    mostrar_notificacion()

    # Limpieza automática silenciosa
    borradas = db.tareas_limpiar_viejas()
    if borradas:
        st.toast(f"🧹 {borradas} tarea(s) antigua(s) eliminada(s)")

    # Cargar datos
    tareas   = db.tareas_listar()
    horario  = db.horario_listar()
    loyola   = cargar_cache("loyola")
    futbol   = cargar_cache("sevilla")

    # ── Sidebar ──
    with st.sidebar:
        st.title("🎓 AutoGestor")
        vistas = ["Diaria", "Semanal", "Mensual",
                  "---",
                  "➕ Nueva Tarea", "➕ Nuevo Evento",
                  "📋 Gestionar Todo"]
        vista = st.radio("Vista:", vistas, index=0, label_visibility="collapsed")
        st.divider()

        st.header("📅 Fecha")
        fecha = st.date_input("Base", hoy_madrid())
        st.info(f"📍 {fecha.strftime('%d %b %Y')}")
        st.divider()

        st.header("🔄 Scraping")
        if st.button("Actualizar Loyola", use_container_width=True):
            with st.spinner("Scrapeando Loyola..."):
                actualizar_scraping("loyola", force=True)
            st.rerun()
        if st.button("Actualizar Sevilla FC", use_container_width=True):
            with st.spinner("Scrapeando Sevilla FC..."):
                actualizar_scraping("sevilla", force=True)
            st.rerun()

        # Estado del cache
        _, ts_l = db.cache_leer("loyola")
        _, ts_s = db.cache_leer("sevilla")
        if ts_l: st.caption(f"Loyola: {ts_l.strftime('%d/%m %H:%M')}")
        if ts_s: st.caption(f"Sevilla: {ts_s.strftime('%d/%m %H:%M')}")

    # ── Router ──
    st.title(f"{'Diario' if vista=='Diaria' else vista}")

    if vista == "Diaria":
        vista_diaria(tareas, horario, loyola, futbol, fecha)
    elif vista == "Semanal":
        vista_semanal(tareas, horario, loyola, futbol, fecha)
    elif vista == "Mensual":
        vista_mensual(tareas, horario, loyola, futbol, fecha)
    elif vista == "➕ Nueva Tarea":
        vista_nueva_tarea()
    elif vista == "➕ Nuevo Evento":
        vista_nuevo_evento()
    elif vista == "📋 Gestionar Todo":
        vista_gestionar(tareas)


if __name__ == "__main__":
    main()
