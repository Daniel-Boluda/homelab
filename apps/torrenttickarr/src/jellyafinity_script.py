#!/usr/bin/env python3
import os, asyncio, re, json, time, math, argparse
from dataclasses import dataclass
from typing import Optional, List, Tuple
from urllib.parse import urlencode

import aiosqlite
import requests
from rapidfuzz.fuzz import ratio
from rapidfuzz.process import extractOne
from dotenv import load_dotenv
from playwright.async_api import async_playwright

# =========================
# Config / Entorno
# =========================
load_dotenv()

JELLYFIN_URL = os.getenv("JELLYFIN_URL", "").rstrip("/")
JELLYFIN_API_KEY = os.getenv("JELLYFIN_API_KEY", "")
INCLUDE_ITEM_TYPES = os.getenv("INCLUDE_ITEM_TYPES", "Movie")
FA_LOCALE = os.getenv("FA_LOCALE", "es").lower()  # es, us, uk...
DB_PATH = os.getenv("FA_DB_PATH", "fa_map.sqlite")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1","true","yes","y")

# Scraping/matching params
FA_DELAY = float(os.getenv("FA_DELAY_SECONDS", "0.8"))   # cortesía anti-baneo
TITLE_SIM_THRESHOLD = float(os.getenv("TITLE_SIM_THRESHOLD", "78"))  # 0-100
YEAR_TOLERANCE = int(os.getenv("YEAR_TOLERANCE", "1"))
MAX_RESULTS_TO_SCAN = int(os.getenv("MAX_RESULTS_TO_SCAN", "8"))     # top-N resultados de FA

HEADERS_JF = {"X-Emby-Token": JELLYFIN_API_KEY, "Accept": "application/json"}
FA_BASE = f"https://www.filmaffinity.com/{FA_LOCALE}"

# =========================
# Data models
# =========================
@dataclass
class JFMovie:
    id: str
    name: str
    year: Optional[int]
    imdb: Optional[str]

@dataclass
class FARow:
    fa_id: int
    fa_url: str
    title: str
    year: Optional[int]
    rating: Optional[float]
    votes: Optional[int]

# =========================
# DB helpers (SQLite)
# =========================
SCHEMA = """
CREATE TABLE IF NOT EXISTS fa_map (
  imdb_id TEXT PRIMARY KEY,      -- 'tt1234567'
  fa_id   INTEGER NOT NULL,      -- 123456
  fa_url  TEXT NOT NULL,
  fa_title TEXT,
  fa_year INTEGER,
  fa_rating REAL,
  fa_votes INTEGER,
  matched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fa_map_fa_id ON fa_map(fa_id);
"""

async def db_init(path=DB_PATH):
    async with aiosqlite.connect(path) as db:
        await db.executescript(SCHEMA)
        await db.commit()

async def db_upsert(row: FARow, imdb_id: str, path=DB_PATH):
    async with aiosqlite.connect(path) as db:
        await db.execute("""
            INSERT INTO fa_map (imdb_id, fa_id, fa_url, fa_title, fa_year, fa_rating, fa_votes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(imdb_id) DO UPDATE SET
              fa_id=excluded.fa_id,
              fa_url=excluded.fa_url,
              fa_title=excluded.fa_title,
              fa_year=excluded.fa_year,
              fa_rating=excluded.fa_rating,
              fa_votes=excluded.fa_votes,
              matched_at=CURRENT_TIMESTAMP
        """, (imdb_id, row.fa_id, row.fa_url, row.title, row.year, row.rating, row.votes))
        await db.commit()

async def db_get_by_imdb(imdb_id: str, path=DB_PATH) -> Optional[FARow]:
    async with aiosqlite.connect(path) as db:
        cur = await db.execute("SELECT fa_id, fa_url, fa_title, fa_year, fa_rating, fa_votes FROM fa_map WHERE imdb_id=?", (imdb_id,))
        r = await cur.fetchone()
        if not r: return None
        return FARow(fa_id=r[0], fa_url=r[1], title=r[2] or "", year=r[3], rating=r[4], votes=r[5])

# =========================
# Jellyfin
# =========================
def jf_list_movies() -> List[JFMovie]:
    if not JELLYFIN_URL or not JELLYFIN_API_KEY:
        raise SystemExit("Faltan JELLYFIN_URL o JELLYFIN_API_KEY")
    url = f"{JELLYFIN_URL}/Items"
    params = {
        "Recursive": "true",
        "IncludeItemTypes": INCLUDE_ITEM_TYPES,
        "Fields": "ProviderIds,ProductionYear",
        "SortBy": "SortName",
        "SortOrder": "Ascending"
    }
    r = requests.get(url, headers=HEADERS_JF, params=params, timeout=30)
    r.raise_for_status()
    out = []
    for it in r.json().get("Items", []):
        prov = it.get("ProviderIds") or {}
        imdb = prov.get("Imdb") or prov.get("IMDB")
        out.append(JFMovie(
            id=it["Id"],
            name=it.get("Name",""),
            year=it.get("ProductionYear"),
            imdb=imdb
        ))
    return out

def jf_set_tag(item_id: str, tag: str):
    # Jellyfin API para tags: POST /Items/{id}/Tags?newTag=...
    url = f"{JELLYFIN_URL}/Items/{item_id}/Tags"
    if DRY_RUN:
        print(f"[DRY] JF add tag {item_id} -> {tag}")
        return
    r = requests.post(url, headers=HEADERS_JF, params={"newTag": tag}, timeout=30)
    r.raise_for_status()

def jf_set_community_rating(item_id: str, rating: float):
    url = f"{JELLYFIN_URL}/Items/{item_id}"
    payload = {"CommunityRating": float(rating)}
    if DRY_RUN:
        print(f"[DRY] JF set CommunityRating {item_id} -> {rating}")
        return
    r = requests.post(url, headers=HEADERS_JF, json=payload, timeout=30)
    if r.status_code not in (200, 204):
        r = requests.put(url, headers=HEADERS_JF, json=payload, timeout=30)
    r.raise_for_status()

# =========================
# FilmAffinity scraping (Playwright)
# =========================
TITLE_ANCHOR_SEL = "h3 a[href*='film']"          # ancla con título en resultados
RATING_SEL = ".avgrat-box"                        # caja con media
RESULT_CARD_SEL = ".se-it"                        # tarjeta de resultado
YEAR_SEL = ".ye-w"                                # span/elem con año (dentro de la tarjeta)

FA_ID_RE = re.compile(r"/film(\d+)\.html")

async def fa_search_and_pick(pw, title: str, year: Optional[int]) -> Optional[FARow]:
    """
    Busca 'title (+ year)' y devuelve el mejor FARow (id, url, title, year, rating, votes)
    usando heurística de similitud y tolerancia de año.
    """
    query = f"{title} {year}" if year else title
    url = f"{FA_BASE}/search.php?{urlencode({'stext': query})}"

    page = await pw.new_page()
    await page.goto(url, timeout=30000)
    # Espera a que aparezcan resultados (si no hay, saldrá por timeout y devolvemos None)
    await page.wait_for_selector(RESULT_CARD_SEL, timeout=10000)

    cards = await page.query_selector_all(RESULT_CARD_SEL)
    candidates: List[FARow] = []

    for card in cards[:MAX_RESULTS_TO_SCAN]:
        a = await card.query_selector(TITLE_ANCHOR_SEL)
        if not a: continue
        href = await a.get_attribute("href") or ""
        text = (await a.inner_text()) or ""
        m = FA_ID_RE.search(href)
        if not m: continue
        fa_id = int(m.group(1))
        fa_url = f"{FA_BASE}/film{fa_id}.html"

        # Año (si está visible)
        year_el = await card.query_selector(YEAR_SEL)
        y = None
        if year_el:
            try:
                y = int((await year_el.inner_text()).strip()[:4])
            except: pass

        # Nota (en la tarjeta)
        rating_el = await card.query_selector(RATING_SEL)
        rating = None
        if rating_el:
            txt = (await rating_el.inner_text()) or ""
            txt = txt.replace(",", ".").strip()
            try:
                rating = float(txt)
            except:
                rating = None

        # Votos (en algunos layouts aparecen bajo la nota)
        votes = None
        try:
            votes_el = await card.query_selector(".rat-count")
            if votes_el:
                vt = (await votes_el.inner_text()) or ""
                vt = re.sub(r"[^\d]", "", vt)
                if vt:
                    votes = int(vt)
        except:
            pass

        candidates.append(FARow(
            fa_id=fa_id,
            fa_url=fa_url,
            title=text.strip(),
            year=y,
            rating=rating,
            votes=votes
        ))

    await page.close()

    if not candidates:
        return None

    # Elegir mejor candidato por similitud de título y cercanía de año
    def score(c: FARow) -> float:
        base = ratio(title.lower(), (c.title or "").lower())
        # penalización por año si difiere > YEAR_TOLERANCE
        if year and c.year:
            dy = abs(year - c.year)
            if dy > YEAR_TOLERANCE:
                base -= min(30 + 5*(dy - YEAR_TOLERANCE), 60)  # penaliza duro si se va mucho
        return base

    best = max(candidates, key=score)
    if score(best) < TITLE_SIM_THRESHOLD:
        return None
    return best

# =========================
# Flujo principal
# =========================
async def map_from_jellyfin(save_also_rating=True):
    await db_init(DB_PATH)
    movies = jf_list_movies()
    print(f"Películas en Jellyfin: {len(movies)}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(locale="es-ES")
        for mv in movies:
            if not mv.imdb:
                continue
            # ¿ya existe en cache?
            cached = await db_get_by_imdb(mv.imdb)
            if cached and (cached.rating is not None or not save_also_rating):
                print(f"= cache {mv.name} ({mv.year}) imdb={mv.imdb} -> fa={cached.fa_id} rating={cached.rating}")
                continue

            page = await context.new_page()
            try:
                pick = await fa_search_and_pick(context, mv.name, mv.year)
            except Exception as e:
                print(f"! error búsqueda FA: {mv.name} ({mv.year}) -> {e}")
                pick = None
            finally:
                await page.close()

            if not pick:
                print(f"- sin match: {mv.name} ({mv.year}) imdb={mv.imdb}")
            else:
                print(f"+ match: {mv.name} -> FA {pick.fa_id} [{pick.title} {pick.year}] rating={pick.rating} votes={pick.votes}")
                await db_upsert(pick, mv.imdb)

            await asyncio.sleep(FA_DELAY)

        await browser.close()

async def apply_to_jellyfin(use_tag=True, replace_community=False, tag_prefix="Filmaffinity: "):
    assert use_tag or replace_community, "Nada que aplicar: activa use_tag y/o replace_community"
    movies = jf_list_movies()
    for mv in movies:
        if not mv.imdb:
            continue
        row = await db_get_by_imdb(mv.imdb)
        if not row or row.rating is None:
            continue
        if use_tag:
            jf_set_tag(mv.id, f"{tag_prefix}{row.rating:.1f}")
        if replace_community:
            jf_set_community_rating(mv.id, row.rating)

# =========================
# CLI
# =========================
def main():
    ap = argparse.ArgumentParser(description="Scraper/mapeo FilmAffinity <-> IMDb y aplicación en Jellyfin")
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_map = sub.add_parser("map", help="Scrapear y construir/actualizar el mapeo desde la biblioteca de Jellyfin")
    ap_map.add_argument("--no-rating", action="store_true", help="Solo mapear fa_id (sin leer rating)")
    ap_map.add_argument("--once", action="store_true", help="Procesa y termina (por defecto ya lo hace)")

    ap_apply = sub.add_parser("apply", help="Aplicar notas de FA a Jellyfin")
    ap_apply.add_argument("--tag", action="store_true", help="Añadir etiqueta 'Filmaffinity: X.X'")
    ap_apply.add_argument("--replace-community", action="store_true", help="Sustituir CommunityRating por la nota de FA")
    ap_apply.add_argument("--tag-prefix", default="Filmaffinity: ", help="Prefijo de etiqueta")

    args = ap.parse_args()

    if args.cmd == "map":
        asyncio.run(map_from_jellyfin(save_also_rating=not args.no_rating))
    elif args.cmd == "apply":
        asyncio.run(apply_to_jellyfin(
            use_tag=bool(args.tag),
            replace_community=bool(args.replace_community),
            tag_prefix=args.tag_prefix
        ))

if __name__ == "__main__":
    main()
