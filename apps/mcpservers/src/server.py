#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Plant Risk MCP Server 

Endpoints (preguntas típicas):
- list_plants()
- list_machines_for_plant(plant_name?, plant_id?, detail?, page_size?, cursor?)
- machines_with_open_alerts_in_plant(plant_name?, plant_id?, detail?, page_size?, cursor?)
- machines_with_high_risk_in_plant(plant_name?, plant_id?, detail?, page_size?, cursor?)
- list_machines(detail?, page_size?, cursor?)
- machines_with_open_alerts(detail?, page_size?, cursor?)
- machines_with_high_risk(detail?, page_size?, cursor?)
- list_alerts_in_plant(plant_name?, plant_id?, state?, include_features?, max_features?, page_size?, cursor?)
- list_alerts(state?, include_features?, max_features?, page_size?, cursor?)
- get_alert_features_in_plant(plant_name?, plant_id?, alert_id?|alert_numeric_id?, machine_id?, machine_name?, max_features?)
- get_alert_features(alert_id?|alert_numeric_id?, max_features?)
- get_alert_features_for_machine(machine_name?|machine_id?, alert_id?|alert_numeric_id?, state?, pick?, max_features?)
"""

import json
import logging
import os
import asyncio
import unicodedata
from typing import Optional, Dict, Any

import asyncpg
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.dependencies import get_access_token

from dotenv import load_dotenv

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
LOGLEVEL = os.getenv("FASTMCP_LOGLEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOGLEVEL, logging.INFO))
logger = logging.getLogger("plant-risk-mcp")

load_dotenv(dotenv_path=os.environ.get("ENV_FILE", ".env"))

# -----------------------------
# Config
# -----------------------------
MCP_NAME = os.getenv("MCP_NAME", "mcp-server")
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))
MCP_PATH = os.getenv("MCP_PATH", "/mcp")

DEFAULT_PAGE_SIZE = int(os.getenv("DEFAULT_PAGE_SIZE", "50"))
DEFAULT_MAX_FEATURES = int(os.getenv("DEFAULT_MAX_FEATURES", "3"))

SERVER_INSTRUCTIONS = """
This MCP server serves plant risk data from local JSON files under ./data.
Auth: Google OAuth. Access restricted by email whitelist (ALLOWED_EMAILS).
Transport: Streamable HTTP ("http"), endpoint at MCP_PATH (default /mcp).
To keep responses small:
- Default detail is 'summary'; use 'names' for id+name only; use 'full' sparingly.
- All list tools are paginated with 'page_size' and 'cursor'.
- Alerts lists omit features by default; ask 'include_features=true' and cap with 'max_features'.
"""

# -----------------------------------------------------------------------------
# OAuth Google
# -----------------------------------------------------------------------------
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
BASE_URL = os.getenv("BASE_URL")

if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
    raise RuntimeError("Debe configurar GOOGLE_CLIENT_ID y GOOGLE_CLIENT_SECRET")

google_auth = GoogleProvider(
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    base_url=BASE_URL,
    required_scopes=[
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
    ],
)

# -----------------------------------------------------------------------------
# Whitelist por emails
# -----------------------------------------------------------------------------
ALLOWED_EMAILS = {
    e.strip().lower() for e in os.getenv("ALLOWED_EMAILS", "").split(",") if e.strip()
}
if not ALLOWED_EMAILS:
    raise RuntimeError("ALLOWED_EMAILS vacío. Define emails coma-separados.")

logger.info(f"Emails permitidos: {ALLOWED_EMAILS}")

# DB
INSTANCE = os.environ.get("INSTANCE_CONNECTION_NAME")  # opcional si usas Cloud SQL connector externo
PGHOST   = os.environ.get("PGHOST", "127.0.0.1")
PGPORT   = int(os.environ.get("PGPORT", "5432"))
PGUSER   = os.environ.get("PGUSER")
PGPASS   = os.environ.get("PGPASS")
PGDB     = os.environ.get("PGDB")

if not all([PGUSER, PGPASS, PGDB]):
    raise RuntimeError("Faltan PGUSER/PGPASS/PGDB")

# -----------------------------
# Utils mínimos
# -----------------------------
def _norm(s: str) -> str:
    s = s.strip().casefold()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return " ".join(s.split())

def _page_size(ps: Optional[int]) -> int:
    try:
        ps = int(ps or DEFAULT_PAGE_SIZE)
    except Exception:
        ps = DEFAULT_PAGE_SIZE
    return max(1, min(ps, 500))  # cap razonable

def _risk_level_rank():
    # Para topAlertRiskLevel: HIGH > MEDIUM > LOW
    return "CASE WHEN risk_level = 'HIGH' THEN 3 WHEN risk_level = 'MEDIUM' THEN 2 WHEN risk_level = 'LOW' THEN 1 ELSE 0 END"

# -----------------------------
# DB pool
# -----------------------------
_pool: Optional[asyncpg.Pool] = None

async def db_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            host=PGHOST, port=PGPORT,
            user=PGUSER, password=PGPASS, database=PGDB,
            min_size=1, max_size=10, command_timeout=60
        )
    return _pool


class EmailWhitelistMiddleware(Middleware):
    async def on_call_tool(self, context: MiddlewareContext, call_next):
        # Usa el token directamente desde la dependencia del servidor
        try:
            token = get_access_token()
        except Exception as e:
            logger.info(f"[MW on_call_tool] NO AUTH TOKEN -> denegado ({e})")
            raise ToolError("Not authenticated")

        # Claims del proveedor (GoogleProvider los coloca en token.claims)
        claims = getattr(token, "claims", None) or {}
        logger.debug(f"[MW on_call_tool] tool={getattr(context, 'tool_name', None)} claims_keys={list(claims.keys())}")

        # Extrae datos básicos (opcional, útil para logs)
        google_id = claims.get("sub")
        email = (claims.get("email") or "").strip().lower() or None
        name = claims.get("name")
        locale = claims.get("locale")

        logger.info(f"[MW on_call_tool] user sub={google_id} email={email} name={name} locale={locale}")

        if not email:
            logger.info("[MW on_call_tool] sin email en claims -> denegado")
            raise ToolError("User without email claim")

        if email not in ALLOWED_EMAILS:
            logger.info(f"[MW on_call_tool] email {email} NO autorizado -> denegado")
            raise ToolError("User not allowed")

        logger.info(f"[MW on_call_tool] acceso permitido a {email}")
        return await call_next(context)

# -----------------------------------------------------------------------------
# Server (tools)
# -----------------------------------------------------------------------------
def create_server() -> FastMCP:
    mcp = FastMCP(name=MCP_NAME, auth=google_auth, instructions=SERVER_INSTRUCTIONS)
    #mcp = FastMCP(name=MCP_NAME, instructions=SERVER_INSTRUCTIONS)

    async def root_ok(request):
        return JSONResponse({"status": "ok", "server": MCP_NAME, "time": _now_iso()})

    try:
        mcp.app.add_api_route("/", root_ok, methods=["GET"])
    except Exception as e:
        logger.warning(f"No se pudo registrar '/' (no crítico): {e}")

    # Añadimos middleware
    mcp.add_middleware(EmailWhitelistMiddleware())

    # ---------- list_plants ----------
    @mcp.tool(description="List plants with keyset pagination.")
    async def list_plants(page_size: Optional[int] = None, cursor: Optional[str] = None) -> dict:
        ps = _page_size(page_size)
        after_id = int(cursor) if cursor else 0
        sql = """
            SELECT id::text AS id, name, acs_code
            FROM public.plants_plant
            WHERE deleted_at IS NULL AND id > $1
            ORDER BY id
            LIMIT $2;
        """
        async with (await db_pool()).acquire() as con:
            rows = await con.fetch(sql, after_id, ps)
        next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
        return {"count": len(rows), "next_cursor": next_cursor, "plants": [dict(r) for r in rows]}

    # ---------- list_machines_for_plant ----------
    @mcp.tool(description="List machines for a plant. detail=names|summary|full")
    async def list_machines_for_plant(
        plant_name: Optional[str] = None,
        plant_id: Optional[str] = None,
        detail: Optional[str] = "summary",
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        # Resolver planta por id o nombre normalizado (exact match)
        async with (await db_pool()).acquire() as con:
            if plant_id:
                plant = await con.fetchrow(
                    "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND id=$1",
                    int(plant_id),
                )
            elif plant_name:
                target = _norm(plant_name)
                plant = await con.fetchrow(
                    """
                    SELECT id::text AS id, name, acs_code
                    FROM public.plants_plant
                    WHERE deleted_at IS NULL
                    AND lower(unaccent(name)) = lower(unaccent($1))
                    """,
                    plant_name,
                )
                # Si tu Postgres no tiene la extensión unaccent, cambia a comparación casefold en Python
                if not plant:
                    # fallback simple
                    p = await con.fetch("SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL")
                    plant = next((r for r in p if _norm(r["name"]) == target), None)
            else:
                return {"error": "Debe indicar plant_name o plant_id"}

            if not plant:
                return {"error": "Plant not found", "plant_name": plant_name, "plant_id": plant_id}

            ps = _page_size(page_size)
            after_id = int(cursor) if cursor else 0

            # Selección de columnas por detalle
            if detail == "names":
                sql = """
                    SELECT id::text AS id, name
                    FROM public.mpredict_asset
                    WHERE plant_id = $1 AND id > $2
                    ORDER BY id
                    LIMIT $3;
                """
                params = (int(plant["id"]), after_id, ps)
                async with (await db_pool()).acquire() as con2:
                    rows = await con2.fetch(sql, *params)
                next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
                return {
                    "plant": dict(plant),
                    "detail": "names",
                    "count": len(rows),
                    "next_cursor": next_cursor,
                    "machines": [dict(r) for r in rows],
                }

            if detail == "full":
                # full: devolvemos asset y (opcionalmente) podrás consultar alertas aparte; aquí no cargamos componentes/alertas para no engordar
                sql = """
                    SELECT id::text AS id, name, hac_code, url, plant_id::text AS plant_id, area,
                           risk_score AS "riskScore", risk_level AS "riskLevel"
                    FROM public.mpredict_asset
                    WHERE plant_id = $1 AND id > $2
                    ORDER BY id
                    LIMIT $3;
                """
            else:
                # summary: calculamos agregados en SQL, sin traer alertas completas
                sql = f"""
                    SELECT a.id::text AS id, a.name, a.plant_id::text AS plant_id, a.area,
                           a.risk_score AS "riskScore", a.risk_level AS "riskLevel",
                           COALESCE(agt.alert_count, 0) AS "alertCount",
                           (agt.alert_count > 0) AS "hasAlerts",
                           agt.last_ts AS "lastAlertAt",
                           agt.top_level AS "topAlertRiskLevel"
                    FROM public.mpredict_asset a
                    LEFT JOIN LATERAL (
                        SELECT COUNT(*)::int AS alert_count,
                               MAX(m.timestamp) AS last_ts,
                               (
                                 SELECT m2.risk_level
                                 FROM public.mpredict_mpredictalert m2
                                 WHERE m2.asset_id = a.id
                                 ORDER BY { _risk_level_rank() } DESC
                                 LIMIT 1
                               ) AS top_level
                        FROM public.mpredict_mpredictalert m
                        WHERE m.asset_id = a.id
                    ) agt ON TRUE
                    WHERE a.plant_id = $1 AND a.id > $2
                    ORDER BY a.id
                    LIMIT $3;
                """
            params = (int(plant["id"]), after_id, ps)
            async with (await db_pool()).acquire() as con3:
                rows = await con3.fetch(sql, *params)
            next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
            return {
                "plant": dict(plant),
                "detail": detail or "summary",
                "count": len(rows),
                "next_cursor": next_cursor,
                "machines": [dict(r) for r in rows],
            }

    # ---------- list_alerts_in_plant ----------
    @mcp.tool(description="List alerts in a plant with optional state filter. Supports include_features & max_features.")
    async def list_alerts_in_plant(
        plant_name: Optional[str] = None,
        plant_id: Optional[str] = None,
        state: Optional[str] = "Open",            # Open|Closed|Any
        include_features: Optional[bool] = False, # si true, devuelve feature_contribution recortado
        max_features: Optional[int] = None,
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,             # keyset por id de alerta
    ) -> dict:
        s = (state or "Any").strip().lower()
        if s not in ("open", "closed", "any"):
            return {"error": "state debe ser Open|Closed|Any"}

        # Resolver planta
        async with (await db_pool()).acquire() as con:
            if plant_id:
                plant = await con.fetchrow(
                    "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND id=$1",
                    int(plant_id),
                )
            elif plant_name:
                target = _norm(plant_name)
                p = await con.fetch("SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL")
                plant = next((r for r in p if _norm(r["name"]) == target or r["acs_code"] == plant_name), None)
            else:
                return {"error": "Debe indicar plant_name o plant_id"}

            if not plant:
                return {"error": "Plant not found"}

            ps = _page_size(page_size)
            after_id = int(cursor) if cursor else 0

            # Filtro de estado
            where_state = "" if s == "any" else "AND lower(m.state) = $4"
            # Elegimos columnas; features opcionales
            features_select = "NULL::jsonb AS features"
            params = [int(plant["id"]), after_id, ps]
            if s != "any":
                params.append(s)

            if include_features:
                # Recorte de features en SQL si viene como JSONB con pares {feature, contribution}
                # Si no fuese JSONB estructurado, el recorte se hará en cliente como fallback.
                features_select = "m.feature_contribution AS features"

            sql = f"""
                SELECT
                    m.id::text AS id,
                    m.alert_id,
                    m.timestamp,
                    m.name,
                    m.state,
                    m.risk_score AS "riskScore",
                    m.risk_level AS "riskLevel",
                    a.id::text AS machine_id,
                    a.name AS machine_name,
                    {features_select}
                FROM public.mpredict_mpredictalert m
                JOIN public.mpredict_asset a ON a.id = m.asset_id
                WHERE a.plant_id = $1
                  AND m.id > $2
                  {where_state}
                ORDER BY m.id
                LIMIT $3;
            """
            async with (await db_pool()).acquire() as con2:
                rows = await con2.fetch(sql, *params)

            # recorte de features (fallback) si procede
            mf = int(max_features) if (include_features and max_features) else 0
            out_alerts = []
            for r in rows:
                item = {
                    "machine": {"id": r["machine_id"], "name": r["machine_name"]},
                    "alert": {
                        "id": r["id"],
                        "alert_id": r["alert_id"],
                        "timestamp": r["timestamp"],
                        "name": r["name"],
                        "state": r["state"],
                        "riskScore": r["riskScore"],
                        "riskLevel": r["riskLevel"],
                    },
                }
                feats = r["features"]
                if include_features and feats is not None:
                    # si es lista -> recortar por contribution desc si se puede
                    try:
                        # esperamos lista de objetos {feature, contribution}
                        feats_sorted = sorted(feats, key=lambda x: float(x.get("contribution", 0) or 0), reverse=True)
                    except Exception:
                        feats_sorted = feats
                    item["alert"]["features"] = feats_sorted[:mf] if mf > 0 else feats_sorted
                out_alerts.append(item)

            next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
            return {
                "plant": dict(plant),
                "state": s.upper() if s != "any" else "ANY",
                "count": len(out_alerts),
                "next_cursor": next_cursor,
                "alerts": out_alerts,
            }

    # ---------- get_alert_features ----------
    @mcp.tool(description="Return features for an alert (by numeric id or UUID).")
    async def get_alert_features(
        alert_id: Optional[str] = None,
        alert_numeric_id: Optional[str] = None,
        max_features: Optional[int] = None,
    ) -> dict:
        if not alert_id and not alert_numeric_id:
            return {"error": "Debe indicar alert_id (UUID) o alert_numeric_id"}

        async with (await db_pool()).acquire() as con:
            if alert_numeric_id:
                row = await con.fetchrow(
                    """
                    SELECT m.id::text AS id, m.alert_id, m.feature_contribution AS features
                    FROM public.mpredict_mpredictalert m WHERE m.id=$1
                    """,
                    int(alert_numeric_id),
                )
            else:
                row = await con.fetchrow(
                    """
                    SELECT m.id::text AS id, m.alert_id, m.feature_contribution AS features
                    FROM public.mpredict_mpredictalert m WHERE m.alert_id=$1
                    """,
                    alert_id,
                )
        if not row:
            return {"error": "Alert not found"}

        feats = row["features"]
        mf = int(max_features) if max_features else 0
        if isinstance(feats, list) and mf > 0:
            try:
                feats = sorted(feats, key=lambda x: float(x.get("contribution", 0) or 0), reverse=True)[:mf]
            except Exception:
                feats = feats[:mf]

        return {
            "alert": {"id": row["id"], "alert_id": row["alert_id"]},
            "count": len(feats) if isinstance(feats, list) else (0 if feats is None else 1),
            "features": feats,
        }

    return mcp

# -----------------------------
# Main
# -----------------------------
def main():
    server = create_server()
    try:
        server.run(transport="http", host=MCP_HOST, port=MCP_PORT, path=MCP_PATH)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    # Inicia el loop y el pool en lazy al primer uso
    asyncio.run(asyncio.sleep(0))
    main()
