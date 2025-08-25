import os
import json
import requests
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from textwrap import shorten

# =========================
# Configuración / Entorno
# =========================
load_dotenv()

JELLYFIN_URL = os.getenv("JELLYFIN_URL", "").rstrip("/")
JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY", "")
# Opcional: limitar a una lista concreta de usuarios (JSON array de IDs). Si no se define, se leerán todos los usuarios habilitados.
JELLYFIN_USER_IDS_JSON = os.getenv("JELLYFIN_USER_IDS", "")  # ej: '["userId1","userId2"]'

INCLUDE_ITEM_TYPES = os.getenv("INCLUDE_ITEM_TYPES", "Movie,Episode")  # puedes cambiarlo
OLDER_THAN_DAYS = int(os.getenv("OLDER_THAN_DAYS", "60"))  # ~2 meses
MAX_TOTAL_SECONDS = int(os.getenv("MAX_TOTAL_SECONDS", "600"))  # 10 minutos

EXPORT_JSON_PATH = os.getenv("EXPORT_JSON_PATH", "").strip()

HEADERS = {
    "X-Emby-Token": JELLYFIN_API_KEY,
    "Accept": "application/json"
}

TICKS_PER_SECOND = 10_000_000

# =========================
# Utilidades
# =========================
def iso_to_dt(s: str):
    """Convierte ISO8601 de Jellyfin a datetime con tz UTC si es posible."""
    if not s:
        return None
    try:
        # Jellyfin suele devolver "2024-05-01T12:34:56.789Z"
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        return datetime.fromisoformat(s)
    except Exception:
        return None

def ticks_to_seconds(ticks: int) -> float:
    return ticks / TICKS_PER_SECOND if ticks else 0.0

def seconds_to_hms(sec: float) -> str:
    s = int(round(sec or 0))
    h = s // 3600
    m = (s % 3600) // 60
    s = s % 60
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"

def bytes_to_human(n: int) -> str:
    if not n:
        return "-"
    v = float(n)
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if v < 1024 or unit == "PB":
            return f"{v:.1f}{unit}"
        v /= 1024

# =========================
# API Jellyfin
# =========================
def get_all_users():
    url = f"{JELLYFIN_URL}/Users"
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    users = r.json()
    # Filtra deshabilitados / invitados si quieres
    return [u["Id"] for u in users if not u.get("Policy", {}).get("IsDisabled", False)]

def get_all_items_basic(user_id: str):
    """
    Obtiene todos los items (Movie, Episode, etc.) con los campos necesarios.
    Añadimos SeriesName, ParentIndexNumber, IndexNumber y Size para evitar 'S?0E?0' y mostrar tamaño en disco.
    """
    url = f"{JELLYFIN_URL}/Items"
    params = {
        "Recursive": "true",
        "IncludeItemTypes": INCLUDE_ITEM_TYPES,
        "Fields": "DateCreated,RunTimeTicks,ParentId,Type,ProductionYear,SeriesName,ParentIndexNumber,IndexNumber,Size",
        "SortBy": "DateCreated",
        "SortOrder": "Ascending"
    }
    r = requests.get(url, headers=HEADERS, params=params, timeout=60)
    r.raise_for_status()
    items = r.json().get("Items", [])
    by_id = {}
    for it in items:
        by_id[it["Id"]] = {
            "Id": it["Id"],
            "Name": it.get("Name"),
            "Type": it.get("Type"),
            "RunTimeTicks": int(it.get("RunTimeTicks") or 0),
            "DateCreated": iso_to_dt(it.get("DateCreated")),
            "ProductionYear": it.get("ProductionYear"),
            "SeriesName": it.get("SeriesName"),
            "ParentIndexNumber": it.get("ParentIndexNumber"),
            "IndexNumber": it.get("IndexNumber"),
            "Size": get_item_size(it["Id"], user_id)
        }
    return by_id

def get_item_size(item_id: str, user_id: str) -> int | None:
    """Obtiene el tamaño en bytes desde PlaybackInfo (primer MediaSource)."""
    url = f"{JELLYFIN_URL}/Items/{item_id}/PlaybackInfo"
    r = requests.get(url, headers=HEADERS, params={"UserId": user_id}, timeout=30)
    if r.status_code != 200:
        return None
    data = r.json()
    sources = data.get("MediaSources") or []
    if not sources:
        return None
    return sources[0].get("Size")  # bytes

def get_user_items_userdata(user_id: str):
    """
    Para un usuario, trae UserData (Played/PlaybackPositionTicks) de todos los ítems.
    """
    url = f"{JELLYFIN_URL}/Users/{user_id}/Items"
    params = {
        "Recursive": "true",
        "IncludeItemTypes": INCLUDE_ITEM_TYPES,
        "Fields": "UserData,RunTimeTicks"
    }
    r = requests.get(url, headers=HEADERS, params=params, timeout=60)
    r.raise_for_status()
    items = r.json().get("Items", [])
    ud = {}
    for it in items:
        it_id = it["Id"]
        user_data = it.get("UserData", {}) or {}
        ud[it_id] = {
            "Played": bool(user_data.get("Played", False)),
            "PlaybackPositionTicks": int(user_data.get("PlaybackPositionTicks") or 0),
            # puede faltar RunTimeTicks aquí; usamos el de all_items_basic como referencia
            "RunTimeTicks": int(it.get("RunTimeTicks") or 0),
        }
    return ud

# =========================
# Lógica principal
# =========================
def main():
    if not JELLYFIN_URL or not JELLYFIN_API_KEY:
        raise SystemExit("Faltan variables de entorno JELLYFIN_URL y/o JELLYFIN_API_KEY.")

    # Usuarios
    if JELLYFIN_USER_IDS_JSON.strip():
        try:
            user_ids = json.loads(JELLYFIN_USER_IDS_JSON)
            if not isinstance(user_ids, list):
                raise ValueError
        except Exception:
            raise SystemExit('JELLYFIN_USER_IDS debe ser un JSON array, p.ej. ["userId1","userId2"].')
    else:
        user_ids = get_all_users()

    # Items base (sin UserData)
    all_items = get_all_items_basic(user_ids[0])
    if not all_items:
        print("No se encontraron ítems.")
        return

    # Carga UserData por usuario y acumula tiempo visto por ítem
    aggregated_seconds = {it_id: 0.0 for it_id in all_items.keys()}

    for uid in user_ids:
        ud = get_user_items_userdata(uid)
        for it_id, u in ud.items():
            base = all_items.get(it_id)
            if not base:
                continue  # por si difieren filtros
            runtime_ticks = base["RunTimeTicks"] or u.get("RunTimeTicks") or 0
            if runtime_ticks <= 0:
                continue
            if u["Played"]:
                # Si marcado como visto por ese usuario, cuenta duración completa
                aggregated_seconds[it_id] += ticks_to_seconds(runtime_ticks)
            else:
                # Suma progreso parcial, acotado a la duración
                pos = min(u["PlaybackPositionTicks"], runtime_ticks)
                aggregated_seconds[it_id] += ticks_to_seconds(pos)

    # Filtros: antigüedad > N días y total < M segundos
    cutoff = datetime.now(timezone.utc) - timedelta(days=OLDER_THAN_DAYS)
    max_total = MAX_TOTAL_SECONDS

    candidates = []
    for it_id, secs in aggregated_seconds.items():
        info = all_items[it_id]
        created = info["DateCreated"]
        if not created or created > cutoff:
            continue  # no es suficientemente antiguo
        if secs < max_total:
            candidates.append({
                "Id": it_id,
                "Name": info.get("Name"),
                "Type": info.get("Type"),
                "ProductionYear": info.get("ProductionYear"),
                "DateCreated": created,
                "DateCreatedISO": created.isoformat(),
                "TotalWatchedSeconds": round(secs, 2),
                "TotalWatchedPretty": seconds_to_hms(secs),
                "RuntimeSeconds": round(ticks_to_seconds(info.get("RunTimeTicks", 0)), 2),
                "SeriesName": info.get("SeriesName"),
                "ParentIndexNumber": info.get("ParentIndexNumber"),
                "IndexNumber": info.get("IndexNumber"),
                "Size": info.get("Size"),
            })

    # Ordena: más antiguos primero (DateCreated asc), luego menos vistos (TotalWatchedSeconds asc)
    candidates.sort(
        key=lambda x: (x["DateCreated"] or datetime(1900,1,1, tzinfo=timezone.utc), x["TotalWatchedSeconds"])
    )

    # Salida
    if not candidates:
        print(f"🎉 No hay elementos con > {OLDER_THAN_DAYS} días y < {MAX_TOTAL_SECONDS//60} min de reproducción total.")
        return

    print(f"\nItems con > {OLDER_THAN_DAYS} días y < {MAX_TOTAL_SECONDS//60} min vistos (total usuarios): {len(candidates)}\n")
    print(f"{'Tipo':6} {'Año':4} {'Añadido':20} {'Visto':>10} {'Duración':>10} {'Tamaño':>10}  Título")
    print("-"*120)
    for c in candidates:
        if c["Type"] == "Episode":
            # Etiqueta episodio: Serie SxxExx – Título
            s = c.get("ParentIndexNumber")
            e = c.get("IndexNumber")
            s_str = f"S{s:02}" if isinstance(s, int) else "S??"
            e_str = f"E{e:02}" if isinstance(e, int) else "E??"
            series = c.get("SeriesName") or "?"
            title = f"{series} {s_str}{e_str} – {c.get('Name','')}"
        else:
            title = c.get("Name","")
        name = shorten(title, width=60, placeholder="…")
        year = c['ProductionYear'] or ''
        size_str = bytes_to_human(c.get("Size"))
        print(f"{(c['Type'] or ''):6} {str(year):4} {c['DateCreatedISO']:20} {c['TotalWatchedPretty']:>10} {seconds_to_hms(c['RuntimeSeconds']):>10} {size_str:>10}  {name}")

    # Opcional: exportar a JSON si se define ruta
    if EXPORT_JSON_PATH:
        exportable = []
        for c in candidates:
            item = dict(c)
            # Sustituimos datetime por str ISO (ya tenemos DateCreatedISO)
            item["DateCreated"] = c["DateCreatedISO"]
            exportable.append(item)
        with open(EXPORT_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(exportable, f, ensure_ascii=False, indent=2)
        print(f"\n📄 Exportado a JSON: {EXPORT_JSON_PATH}")

if __name__ == "__main__":
    main()
