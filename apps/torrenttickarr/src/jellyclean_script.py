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

# Jellyfin
JELLYFIN_URL = os.getenv("JELLYFIN_URL", "").rstrip("/")
JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY", "")
JELLYFIN_USER_IDS_JSON = os.getenv("JELLYFIN_USER_IDS", "")

INCLUDE_ITEM_TYPES = os.getenv("INCLUDE_ITEM_TYPES", "Movie,Episode")
OLDER_THAN_DAYS = int(os.getenv("OLDER_THAN_DAYS", "60"))
MAX_TOTAL_SECONDS = int(os.getenv("MAX_TOTAL_SECONDS", "600"))
EXPORT_JSON_PATH = os.getenv("EXPORT_JSON_PATH", "").strip()

# Radarr
RADARR_URL = os.getenv("RADARR_URL", "").rstrip("/")
RADARR_API_KEY = os.getenv("RADARR_API_KEY", "")
RADARR_IMPORT_TAGS = [t.strip() for t in os.getenv("RADARR_IMPORT_TAGS", "").split(",") if t.strip()]

# Sonarr
SONARR_URL = os.getenv("SONARR_URL", "").rstrip("/")
SONARR_API_KEY = os.getenv("SONARR_API_KEY", "")
SONARR_IMPORT_TAGS = [t.strip() for t in os.getenv("SONARR_IMPORT_TAGS", "").split(",") if t.strip()]

# Jellyseerr (opcional)
JELLYSEERR_URL = os.getenv("JELLYSEERR_URL", "").rstrip("/")
JELLYSEERR_API_KEY = os.getenv("JELLYSEERR_API_KEY", "")

# Seguridad
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1","true","yes","y")

# HTTP
TIMEOUT = 30
HEADERS_JF = {"X-Emby-Token": JELLYFIN_API_KEY, "Accept": "application/json"}

TICKS_PER_SECOND = 10_000_000

# =========================
# Utilidades
# =========================
def iso_to_dt(s: str):
    if not s:
        return None
    try:
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

def log(msg: str):
    print(msg, flush=True)

# =========================
# API Jellyfin
# =========================
def jf_get_all_users():
    url = f"{JELLYFIN_URL}/Users"
    r = requests.get(url, headers=HEADERS_JF, timeout=TIMEOUT)
    r.raise_for_status()
    users = r.json()
    return [u["Id"] for u in users if not u.get("Policy", {}).get("IsDisabled", False)]

def jf_get_item_size(item_id: str, user_id: str) -> int | None:
    url = f"{JELLYFIN_URL}/Items/{item_id}/PlaybackInfo"
    r = requests.get(url, headers=HEADERS_JF, params={"UserId": user_id}, timeout=TIMEOUT)
    if r.status_code != 200:
        return None
    data = r.json() or {}
    sources = data.get("MediaSources") or []
    if not sources:
        return None
    return sources[0].get("Size")

def jf_get_all_items_basic(user_id: str):
    """
    Devuelve diccionario por Id con campos necesarios + ProviderIds para mapear con Radarr/Sonarr.
    """
    url = f"{JELLYFIN_URL}/Items"
    params = {
        "Recursive": "true",
        "IncludeItemTypes": INCLUDE_ITEM_TYPES,
        "Fields": "DateCreated,RunTimeTicks,ParentId,Type,ProductionYear,SeriesName,ParentIndexNumber,IndexNumber,Size,ProviderIds",
        "SortBy": "DateCreated",
        "SortOrder": "Ascending"
    }
    r = requests.get(url, headers=HEADERS_JF, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    items = r.json().get("Items", []) or []
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
            "ProviderIds": it.get("ProviderIds") or {},
            "Size": jf_get_item_size(it["Id"], user_id)
        }
    return by_id

def jf_get_user_items_userdata(user_id: str):
    url = f"{JELLYFIN_URL}/Users/{user_id}/Items"
    params = {
        "Recursive": "true",
        "IncludeItemTypes": INCLUDE_ITEM_TYPES,
        "Fields": "UserData,RunTimeTicks"
    }
    r = requests.get(url, headers=HEADERS_JF, params=params, timeout=TIMEOUT)
    r.raise_for_status()
    items = r.json().get("Items", []) or []
    ud = {}
    for it in items:
        it_id = it["Id"]
        user_data = it.get("UserData", {}) or {}
        ud[it_id] = {
            "Played": bool(user_data.get("Played", False)),
            "PlaybackPositionTicks": int(user_data.get("PlaybackPositionTicks") or 0),
            "RunTimeTicks": int(it.get("RunTimeTicks") or 0),
        }
    return ud

# =========================
# API Radarr
# =========================
def radarr_headers():
    return {"X-Api-Key": RADARR_API_KEY, "Accept": "application/json"}

def radarr_list_movies():
    url = f"{RADARR_URL}/api/v3/movie"
    r = requests.get(url, headers=radarr_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json() or []

def radarr_list_tags():
    url = f"{RADARR_URL}/api/v3/tag"
    r = requests.get(url, headers=radarr_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json() or []

def radarr_tag_names_to_ids(names: list[str]) -> set[int]:
    ids = set()
    if not names:
        return ids
    for t in radarr_list_tags():
        if t.get("label") in names:
            ids.add(t.get("id"))
    return ids

def radarr_find_movie(tmdb_id=None, imdb_id=None):
    # preferir tmdbId; si imdbId viene con 'tt', Radarr guarda a veces sin 'tt'
    normalized_imdb = None
    if imdb_id:
        normalized_imdb = imdb_id
        if imdb_id.startswith("tt"):
            normalized_imdb = imdb_id
            imdb_n = imdb_id[2:]
        else:
            imdb_n = imdb_id
    for m in radarr_list_movies():
        if tmdb_id and m.get("tmdbId") == tmdb_id:
            return m
        if imdb_id:
            if m.get("imdbId") == normalized_imdb or m.get("imdbId") == imdb_n:
                return m
    return None

def radarr_unmonitor(movie_obj: dict):
    movie_obj = dict(movie_obj)
    movie_obj["monitored"] = False
    url = f"{RADARR_URL}/api/v3/movie/{movie_obj['id']}"
    if DRY_RUN:
        log(f"[DRY] Radarr PUT {url} monitored=false")
        return
    r = requests.put(url, headers=radarr_headers(), json=movie_obj, timeout=TIMEOUT)
    r.raise_for_status()

def radarr_delete_movie(movie_id: int, delete_files=True, add_exclusion=True):
    url = f"{RADARR_URL}/api/v3/movie/{movie_id}"
    params = {
        "deleteFiles": str(bool(delete_files)).lower(),
        "addImportExclusion": str(bool(add_exclusion)).lower()
    }
    if DRY_RUN:
        log(f"[DRY] Radarr DELETE {url} params={params}")
        return
    r = requests.delete(url, headers=radarr_headers(), params=params, timeout=TIMEOUT)
    r.raise_for_status()

# =========================
# API Sonarr
# =========================
def sonarr_headers():
    return {"X-Api-Key": SONARR_API_KEY, "Accept": "application/json"}

def sonarr_list_series():
    url = f"{SONARR_URL}/api/v3/series"
    r = requests.get(url, headers=sonarr_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json() or []

def sonarr_list_tags():
    url = f"{SONARR_URL}/api/v3/tag"
    r = requests.get(url, headers=sonarr_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json() or []

def sonarr_tag_names_to_ids(names: list[str]) -> set[int]:
    ids = set()
    if not names:
        return ids
    for t in sonarr_list_tags():
        if t.get("label") in names:
            ids.add(t.get("id"))
    return ids

def sonarr_find_series(tvdb_id=None, tmdb_id=None):
    for s in sonarr_list_series():
        if tvdb_id and s.get("tvdbId") == tvdb_id:
            return s
        if tmdb_id and s.get("tmdbId") == tmdb_id:
            return s
    return None

def sonarr_get_episodes(series_id: int):
    url = f"{SONARR_URL}/api/v3/episode"
    r = requests.get(url, headers=sonarr_headers(), params={"seriesId": series_id}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json() or []

def sonarr_put_episodes(episodes_payload: list[dict]):
    url = f"{SONARR_URL}/api/v3/episode"
    if DRY_RUN:
        log(f"[DRY] Sonarr PUT {url} (unmonitor {len(episodes_payload)} episodios)")
        return
    r = requests.put(url, headers=sonarr_headers(), json=episodes_payload, timeout=TIMEOUT)
    r.raise_for_status()

def sonarr_delete_episode_file(file_id: int):
    url = f"{SONARR_URL}/api/v3/episodefile/{file_id}"
    if DRY_RUN:
        log(f"[DRY] Sonarr DELETE {url}")
        return
    r = requests.delete(url, headers=sonarr_headers(), timeout=TIMEOUT)
    r.raise_for_status()

# =========================
# API Jellyseerr (opcional)
# =========================
def js_headers():
    return {"X-Api-Key": JELLYSEERR_API_KEY, "Accept": "application/json"}

def jellyseerr_has_request_for_tmdb(tmdb_id: int, media_type: str) -> bool:
    """
    Devuelve True si Jellyseerr tiene media/requests para ese tmdbId (movie|tv).
    Best-effort: si tu instancia no soporta el endpoint, devolvemos False.
    """
    if not JELLYSEERR_URL or not JELLYSEERR_API_KEY or not tmdb_id:
        return False
    try:
        url = f"{JELLYSEERR_URL}/api/v1/media"
        params = {"tmdbId": tmdb_id, "mediaType": media_type}
        r = requests.get(url, headers=js_headers(), params=params, timeout=TIMEOUT)
        if r.status_code != 200:
            return False
        data = r.json() or {}
        if isinstance(data, dict) and data.get("tmdbId"):
            return True
        if isinstance(data, list) and len(data) > 0:
            return True
    except Exception:
        return False
    return False

# =========================
# Lógica candidatos (Jellyfin)
# =========================
def compute_candidates():
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
        user_ids = jf_get_all_users()
        if not user_ids:
            raise SystemExit("No se encontraron usuarios activos en Jellyfin.")

    # Items base (sin UserData)
    all_items = jf_get_all_items_basic(user_ids[0])
    if not all_items:
        log("No se encontraron ítems.")
        return []

    # Acumula tiempo visto por ítem (suma de usuarios)
    aggregated_seconds = {it_id: 0.0 for it_id in all_items.keys()}

    for uid in user_ids:
        ud = jf_get_user_items_userdata(uid)
        for it_id, u in ud.items():
            base = all_items.get(it_id)
            if not base:
                continue
            runtime_ticks = base["RunTimeTicks"] or u.get("RunTimeTicks") or 0
            if runtime_ticks <= 0:
                continue
            if u["Played"]:
                aggregated_seconds[it_id] += ticks_to_seconds(runtime_ticks)
            else:
                pos = min(u["PlaybackPositionTicks"], runtime_ticks)
                aggregated_seconds[it_id] += ticks_to_seconds(pos)

    cutoff = datetime.now(timezone.utc) - timedelta(days=OLDER_THAN_DAYS)
    max_total = MAX_TOTAL_SECONDS

    candidates = []
    for it_id, secs in aggregated_seconds.items():
        info = all_items[it_id]
        created = info["DateCreated"]
        if not created or created > cutoff:
            continue
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
                "ParentIndexNumber": info.get("ParentIndexNumber"),  # Season
                "IndexNumber": info.get("IndexNumber"),              # Episode
                "Size": info.get("Size"),
                "ProviderIds": info.get("ProviderIds") or {},
            })

    candidates.sort(key=lambda x: (x["DateCreated"] or datetime(1900,1,1, tzinfo=timezone.utc), x["TotalWatchedSeconds"]))
    return candidates

# =========================
# Filtros por origen (tags de Import List)
# =========================
def is_from_import_list_radarr(movie_obj: dict) -> bool:
    if not RADARR_IMPORT_TAGS:
        return False
    wanted_ids = radarr_tag_names_to_ids(RADARR_IMPORT_TAGS)
    tags = set(movie_obj.get("tags") or [])
    return any(tag_id in tags for tag_id in wanted_ids)

def is_from_import_list_sonarr(series_obj: dict) -> bool:
    if not SONARR_IMPORT_TAGS:
        return False
    wanted_ids = sonarr_tag_names_to_ids(SONARR_IMPORT_TAGS)
    tags = set(series_obj.get("tags") or [])
    return any(tag_id in tags for tag_id in wanted_ids)

# =========================
# Acciones en Radarr / Sonarr
# =========================
def handle_movie(candidate: dict):
    prov = candidate.get("ProviderIds") or {}
    tmdb_raw = prov.get("Tmdb") or prov.get("TMDB")
    imdb_id = prov.get("Imdb") or prov.get("IMDB")
    tmdb_id = None
    try:
        if tmdb_raw:
            tmdb_id = int(tmdb_raw)
    except Exception:
        tmdb_id = None

    movie = radarr_find_movie(tmdb_id=tmdb_id, imdb_id=imdb_id)
    if not movie:
        log(f"  • [SKIP] Movie '{candidate.get('Name')}' no encontrada en Radarr (tmdb={tmdb_id}, imdb={imdb_id})")
        return

    # Jellyseerr: si está pedida, saltar
    if tmdb_id and jellyseerr_has_request_for_tmdb(tmdb_id, media_type="movie"):
        log(f"  • [SKIP] Movie '{candidate.get('Name')}' tiene petición en Jellyseerr (tmdb={tmdb_id})")
        return

    # Solo si proviene de import list
    if not is_from_import_list_radarr(movie):
        log(f"  • [SKIP] Movie '{candidate.get('Name')}' sin tag de import-list en Radarr")
        return

    # Desmonitorizar y borrar (+ import exclusion)
    log(f"  • [OK] Movie '{candidate.get('Name')}' → unmonitor + delete (Radarr)")
    try:
        radarr_unmonitor(movie)
    except Exception as e:
        log(f"    ! Error unmonitor Radarr: {e}")
    try:
        radarr_delete_movie(movie_id=movie["id"], delete_files=True, add_exclusion=True)
    except Exception as e:
        log(f"    ! Error delete Radarr: {e}")

def handle_episode(candidate: dict):
    prov = candidate.get("ProviderIds") or {}
    tvdb_raw = prov.get("Tvdb") or prov.get("TVDB")
    tmdb_raw = prov.get("Tmdb") or prov.get("TMDB")
    season = candidate.get("ParentIndexNumber")
    episode = candidate.get("IndexNumber")

    tvdb_id = None
    tmdb_id = None
    try:
        if tvdb_raw:
            tvdb_id = int(tvdb_raw)
    except Exception:
        tvdb_id = None
    try:
        if tmdb_raw:
            tmdb_id = int(tmdb_raw)
    except Exception:
        tmdb_id = None

    series = sonarr_find_series(tvdb_id=tvdb_id, tmdb_id=tmdb_id)
    if not series:
        log(f"  • [SKIP] Episode '{candidate.get('SeriesName')}' S{season}E{episode} no encontrado en Sonarr (tvdb={tvdb_id}, tmdb={tmdb_id})")
        return

    # Jellyseerr: si la serie está pedida, saltar
    if tmdb_id and jellyseerr_has_request_for_tmdb(tmdb_id, media_type="tv"):
        log(f"  • [SKIP] Serie '{candidate.get('SeriesName')}' tiene petición en Jellyseerr (tmdb={tmdb_id})")
        return

    # Solo si serie proviene de import list
    if not is_from_import_list_sonarr(series):
        log(f"  • [SKIP] Serie '{candidate.get('SeriesName')}' sin tag de import-list en Sonarr")
        return

    eps = sonarr_get_episodes(series_id=series["id"])
    target_eps = [e for e in eps if e.get("seasonNumber")==season and e.get("episodeNumber")==episode]
    if not target_eps:
        log(f"  • [SKIP] No se encontró episodio S{season}E{episode} en Sonarr para '{candidate.get('SeriesName')}'")
        return

    # Unmonitor episodios
    payload = []
    for e in target_eps:
        e = dict(e)
        e["monitored"] = False
        payload.append(e)
    try:
        sonarr_put_episodes(payload)
    except Exception as e:
        log(f"    ! Error unmonitor Sonarr: {e}")

    # Borrar ficheros asociados
    for e in target_eps:
        fid = e.get("episodeFileId")
        if fid:
            try:
                sonarr_delete_episode_file(fid)
            except Exception as e:
                log(f"    ! Error delete episode file Sonarr: {e}")
        else:
            log(f"    · Episodio sin archivo físico (episodeFileId=null), nada que borrar.")

# =========================
# Impresión / Exportación
# =========================
def print_candidates(candidates: list[dict]):
    if not candidates:
        print(f"🎉 No hay elementos con > {OLDER_THAN_DAYS} días y < {MAX_TOTAL_SECONDS//60} min de reproducción total.")
        return
    print(f"\nItems con > {OLDER_THAN_DAYS} días y < {MAX_TOTAL_SECONDS//60} min vistos (total usuarios): {len(candidates)}\n")
    print(f"{'Tipo':6} {'Año':4} {'Añadido':20} {'Visto':>10} {'Duración':>10} {'Tamaño':>10}  Título")
    print("-"*120)
    for c in candidates:
        if c["Type"] == "Episode":
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

def export_candidates_json(candidates: list[dict]):
    if not EXPORT_JSON_PATH:
        return
    exportable = []
    for c in candidates:
        item = dict(c)
        item["DateCreated"] = c["DateCreatedISO"]
        exportable.append(item)
    with open(EXPORT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(exportable, f, ensure_ascii=False, indent=2)
    print(f"\n📄 Exportado a JSON: {EXPORT_JSON_PATH}")

# =========================
# Main
# =========================
def main():
    print(f"DRY_RUN={'ON' if DRY_RUN else 'OFF'} · Filtro: >{OLDER_THAN_DAYS} días y <{MAX_TOTAL_SECONDS//60} min vistos")
    if not RADARR_URL or not RADARR_API_KEY:
        print("Aviso: RADARR_* no configurado (no se procesarán películas).")
    if not SONARR_URL or not SONARR_API_KEY:
        print("Aviso: SONARR_* no configurado (no se procesarán episodios).")
    if JELLYSEERR_URL and not JELLYSEERR_API_KEY:
        print("Aviso: JELLYSEERR_URL definido sin JELLYSEERR_API_KEY (se ignorará Jellyseerr).")

    # 1) Detectar candidatos desde Jellyfin
    candidates = compute_candidates()
    print_candidates(candidates)
    export_candidates_json(candidates)
    if not candidates:
        return

    # 2) Ejecutar acciones por candidato
    for c in candidates:
        if c.get("Type") == "Movie":
            if RADARR_URL and RADARR_API_KEY:
                handle_movie(c)
        elif c.get("Type") == "Episode":
            if SONARR_URL and SONARR_API_KEY:
                handle_episode(c)

if __name__ == "__main__":
    main()
