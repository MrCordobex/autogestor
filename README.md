# AutoGestor

App de gestión de tareas y horario universitario con Streamlit + SQLite.

## Estructura

```
autogestor/
├── app.py           # UI Streamlit (único punto de entrada)
├── db.py            # Capa de persistencia SQLite
├── scraper.py       # Scraping Loyola + Sevilla FC
├── requirements.txt
└── README.md
```

---

## Ejecución en local

### 1. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 2. Arrancar

```bash
streamlit run app.py
```

La base de datos `autogestor.db` se creará automáticamente en la misma carpeta la primera vez.

> **Chrome necesario** para el scraping. En macOS/Windows se descarga automáticamente con `webdriver-manager`.  
> En Linux: `sudo apt install chromium-browser chromium-driver`

---

## Despliegue en Streamlit Cloud

### Problema: SQLite no persiste entre reinicios

Streamlit Cloud tiene sistema de archivos **efímero** — la DB se borra con cada redeploy o reinicio automático.

### Solución recomendada: usar `st.connection` con SQLite + GitHub LFS **o** migrar a Supabase/PlanetScale

#### Opción A (más simple): Turso (SQLite remoto gratuito)

1. Crear cuenta en [turso.tech](https://turso.tech) (tier gratuito: 500 MB, 1 BD)
2. Instalar CLI: `brew install tursodatabase/tap/turso`
3. Crear DB: `turso db create autogestor`
4. Obtener URL y token: `turso db show autogestor` y `turso db tokens create autogestor`
5. En Streamlit Cloud → Settings → Secrets:

```toml
[turso]
url   = "libsql://autogestor-TUUSUARIO.turso.io"
token = "eyJ..."
```

6. Cambiar en `db.py`:

```python
import libsql_experimental as libsql   # pip install libsql-experimental

def get_conn():
    url   = st.secrets["turso"]["url"]
    token = st.secrets["turso"]["token"]
    conn  = libsql.connect(database=url, auth_token=token)
    # El resto igual — libsql es compatible con sqlite3
    ...
```

#### Opción B (lo más fácil sin cambiar código): Streamlit Community Cloud + volumen persistente

Streamlit Cloud **sí persiste** `/mount/` en algunas configuraciones. Cambia `DB_PATH` en `db.py`:

```python
DB_PATH = Path("/mount/autogestor.db")
```

---

## Notas de arquitectura

| Aspecto | Original | Nuevo |
|---|---|---|
| Persistencia | GitHub API (1 petición HTTP por operación) | SQLite WAL (escritura local atómica, ~1ms) |
| Estructura | 1 fichero, funciones duplicadas, 2 `main()` | 3 módulos con responsabilidad única |
| Cache scraping | JSON en disco | Tabla `horario_cache` en la misma BD |
| Limpieza automática | Loop + reescritura JSON completa | `DELETE` SQL con índice |
| Items del día | Lógica repetida en 3 vistas | Función `items_del_dia()` unificada |
