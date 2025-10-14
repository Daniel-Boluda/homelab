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

import os
import json
import logging
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import anyio
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.dependencies import get_access_token

from fastapi.responses import JSONResponse

from dotenv import load_dotenv
from google.cloud.sql.connector import Connector, IPTypes
import pg8000

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
LOGLEVEL = os.getenv("FASTMCP_LOGLEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOGLEVEL, logging.INFO))
logger = logging.getLogger("plant-risk-mcp")

load_dotenv(dotenv_path=os.environ.get("ENV_FILE", ".env"))

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
MCP_NAME = os.getenv("MCP_NAME", "mcp-server")
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "8000"))
MCP_PATH = os.getenv("MCP_PATH", "/mcp")

DEFAULT_PAGE_SIZE = int(os.getenv("DEFAULT_PAGE_SIZE", "50"))
DEFAULT_MAX_FEATURES = int(os.getenv("DEFAULT_MAX_FEATURES", "3"))

SERVER_INSTRUCTIONS = """
DB-backed Plant Risk MCP Server.
Auth: Google OAuth (can be disabled via AUTH_DISABLED=true). Whitelist via ALLOWED_EMAILS.
HTTP transport at MCP_PATH (default /mcp). Responses are compact by default.
- Use detail='names' for id+name, 'summary' (default) for compact machines with aggregates, 'full' for full machine row.
- All list tools are keyset-paginated with page_size and cursor.
- Alerts omit features by default; set include_features=true and cap with max_features.
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
AUTH_DISABLED = os.getenv("AUTH_DISABLED", "false").lower() == "true"
ALLOWED_EMAILS = {
    e.strip().lower() for e in os.getenv("ALLOWED_EMAILS", "").split(",") if e.strip()
}
if not ALLOWED_EMAILS:
    raise RuntimeError("ALLOWED_EMAILS vacío. Define emails coma-separados.")

logger.info(f"Emails permitidos: {ALLOWED_EMAILS}")

# -----------------------------------------------------------------------------
# DB (Cloud SQL Connector + pg8000)
# -----------------------------------------------------------------------------
INSTANCE = os.environ.get("INSTANCE") or os.environ.get("INSTANCE_CONNECTION_NAME")
PGUSER   = os.environ.get("PGUSER")
PGPASS   = os.environ.get("PGPASS")
PGDB     = os.environ.get("PGDB")
IP_TYPE  = (os.environ.get("IP_TYPE") or "PUBLIC").upper()  # PUBLIC | PRIVATE

if not all([INSTANCE, PGUSER, PGPASS, PGDB]):
    raise RuntimeError("Faltan variables DB: INSTANCE/INSTANCE_CONNECTION_NAME, PGUSER, PGPASS, PGDB")

def open_db_connection():
    """Abre una conexión nueva a Cloud SQL (pg8000) y devuelve (connector, conn)."""
    connector = Connector()
    ip_choice = IPTypes.PRIVATE if IP_TYPE == "PRIVATE" else IPTypes.PUBLIC
    conn = connector.connect(
        INSTANCE,
        driver="pg8000",
        user=PGUSER,
        password=PGPASS,
        db=PGDB,
        ip_type=ip_choice,
    )
    return connector, conn

def _rows_to_dicts(cur) -> List[Dict[str, Any]]:
    cols = [d[0] for d in cur.description]  # DB-API
    return [dict(zip(cols, row)) for row in cur.fetchall()]

def _fetch_all_sync(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    connector, conn = open_db_connection()
    try:
        # Opcional: autocommit para evitar transacciones abiertas en lecturas
        try:
            conn.autocommit = True
        except Exception:
            pass

        cur = conn.cursor()
        try:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            return [dict(zip(cols, row)) for row in rows]
        finally:
            try:
                cur.close()
            except Exception:
                pass
    finally:
        try:
            conn.close()
        finally:
            connector.close()


def _fetch_one_sync(sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    connector, conn = open_db_connection()
    try:
        try:
            conn.autocommit = True
        except Exception:
            pass

        cur = conn.cursor()
        try:
            cur.execute(sql, params)
            row = cur.fetchone()
            if row is None:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))
        finally:
            try:
                cur.close()
            except Exception:
                pass
    finally:
        try:
            conn.close()
        finally:
            connector.close()

async def fetch_all(sql: str, params: tuple = ()):
    return await anyio.to_thread.run_sync(_fetch_all_sync, sql, params)

async def fetch_one(sql: str, params: tuple = ()):
    return await anyio.to_thread.run_sync(_fetch_one_sync, sql, params)

# -----------------------------------------------------------------------------
# Utils mínimos
# -----------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"

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

def _risk_order_sql(alias: str = "m2") -> str:
    # Rank de nivel de riesgo: HIGH > MEDIUM > LOW > otros
    return (
        f"CASE WHEN {alias}.risk_level = 'HIGH' THEN 3 "
        f"WHEN {alias}.risk_level = 'MEDIUM' THEN 2 "
        f"WHEN {alias}.risk_level = 'LOW' THEN 1 ELSE 0 END"
    )

class EmailWhitelistMiddleware(Middleware):
    async def on_call_tool(self, context: MiddlewareContext, call_next):
        if AUTH_DISABLED:
            return await call_next(context)
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
    mcp = FastMCP(
        name=MCP_NAME,
        auth=None if AUTH_DISABLED else google_auth,
        instructions=SERVER_INSTRUCTIONS
    )

    async def root_ok(request):
        return JSONResponse({"status": "ok", "server": MCP_NAME, "time": _now_iso()})

    try:
        mcp.app.add_api_route("/", root_ok, methods=["GET"])
    except Exception as e:
        logger.warning(f"No se pudo registrar '/' (no crítico): {e}")

    # Añadimos middleware
    mcp.add_middleware(EmailWhitelistMiddleware())

    # -------- health --------
    @mcp.tool(description="Comprueba que el servidor está vivo y devuelve {status,time}.")
    def health() -> dict:
        return {"status": "ok", "time": _now_iso()}


    # -------- list_plants --------
    @mcp.tool(description="Lista plantas con paginación keyset.")
    async def list_plants(page_size: Optional[int] = None, cursor: Optional[str] = None) -> dict:
        ps = _page_size(page_size)
        after_id = int(cursor) if cursor else 0
        sql = """
            SELECT id::text AS id, name, acs_code
            FROM public.plants_plant
            WHERE deleted_at IS NULL AND id > %s
            ORDER BY id
            LIMIT %s;
        """
        rows = await fetch_all(sql, (after_id, ps))
        next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
        return {"count": len(rows), "next_cursor": next_cursor, "plants": rows}

    # -------- list_machines_for_plant --------
    @mcp.tool(description="Lista máquinas de una planta. detail=names|summary|full")
    async def list_machines_for_plant(
        plant_name: Optional[str] = None,
        plant_id: Optional[str] = None,
        detail: Optional[str] = "summary",  # names | summary | full
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        # Resolver planta
        plant = None
        if plant_id:
            plant = await fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND id = %s",
                (int(plant_id),),
            )
        elif plant_name:
            # match exacto por nombre; puedes cambiar a ILIKE si quieres más laxo
            plant = await fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND name = %s",
                (plant_name,),
            )
            if not plant:
                # fallback: normalización en Python
                candidates = await fetch_all(
                    "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL"
                )
                target = _norm(plant_name)
                plant = next((p for p in candidates if _norm(p["name"]) == target or p["acs_code"] == plant_name), None)

        if not plant:
            return {"error": "Plant not found", "plant_name": plant_name, "plant_id": plant_id}

        ps = _page_size(page_size)
        after_id = int(cursor) if cursor else 0
        pid = int(plant["id"])

        if detail == "names":
            sql = """
                SELECT id::text AS id, name
                FROM public.mpredict_asset
                WHERE plant_id = %s AND id > %s
                ORDER BY id
                LIMIT %s;
            """
            rows = await fetch_all(sql, (pid, after_id, ps))
            next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
            return {
                "plant": plant,
                "detail": "names",
                "count": len(rows),
                "next_cursor": next_cursor,
                "machines": rows,
            }

        if detail == "full":
            sql = """
                SELECT id::text AS id, name, plant_id::text AS plant_id, area,
                       risk_score AS "riskScore", risk_level AS "riskLevel"
                FROM public.mpredict_asset
                WHERE plant_id = %s AND id > %s
                ORDER BY id
                LIMIT %s;
            """
            rows = await fetch_all(sql, (pid, after_id, ps))
            next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
            return {
                "plant": plant,
                "detail": "full",
                "count": len(rows),
                "next_cursor": next_cursor,
                "machines": rows,
            }

        # summary (agregados en SQL, sin traer alertas completas)
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
                         ORDER BY { _risk_order_sql('m2') } DESC
                         LIMIT 1
                       ) AS top_level
                FROM public.mpredict_mpredictalert m
                WHERE m.asset_id = a.id
            ) agt ON TRUE
            WHERE a.plant_id = %s AND a.id > %s
            ORDER BY a.id
            LIMIT %s;
        """
        rows = await fetch_all(sql, (pid, after_id, ps))
        next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
        return {
            "plant": plant,
            "detail": "summary",
            "count": len(rows),
            "next_cursor": next_cursor,
            "machines": rows,
        }

    # -------- machines_with_open_alerts_in_plant --------
    @mcp.tool(description="Máquinas con alertas abiertas en una planta (detail=names|summary|full).")
    async def machines_with_open_alerts_in_plant(
        plant_name: Optional[str] = None,
        plant_id: Optional[str] = None,
        detail: Optional[str] = "summary",
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        # Reutilizamos list_machines_for_plant summary pero filtrando por count>0 en SQL
        # Resolver planta
        plant = None
        if plant_id:
            plant = await fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND id = %s",
                (int(plant_id),),
            )
        elif plant_name:
            plant = await fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND name = %s",
                (plant_name,),
            )
            if not plant:
                candidates = await fetch_all(
                    "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL"
                )
                target = _norm(plant_name)
                plant = next((p for p in candidates if _norm(p["name"]) == target or p["acs_code"] == plant_name), None)
        if not plant:
            return {"error": "Plant not found"}

        ps = _page_size(page_size)
        after_id = int(cursor) if cursor else 0
        pid = int(plant["id"])

        # detail switch
        if detail == "names":
            sql = """
                SELECT a.id::text AS id, a.name
                FROM public.mpredict_asset a
                WHERE a.plant_id = %s AND a.id > %s
                  AND EXISTS (
                      SELECT 1 FROM public.mpredict_mpredictalert m
                      WHERE m.asset_id = a.id AND lower(m.state) = 'open'
                  )
                ORDER BY a.id
                LIMIT %s;
            """
        elif detail == "full":
            sql = """
                SELECT a.id::text AS id, a.name, a.plant_id::text AS plant_id, a.area,
                       a.risk_score AS "riskScore", a.risk_level AS "riskLevel"
                FROM public.mpredict_asset a
                WHERE a.plant_id = %s AND a.id > %s
                  AND EXISTS (
                      SELECT 1 FROM public.mpredict_mpredictalert m
                      WHERE m.asset_id = a.id AND lower(m.state) = 'open'
                  )
                ORDER BY a.id
                LIMIT %s;
            """
        else:
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
                             ORDER BY { _risk_order_sql('m2') } DESC
                             LIMIT 1
                           ) AS top_level
                    FROM public.mpredict_mpredictalert m
                    WHERE m.asset_id = a.id AND lower(m.state) = 'open'
                ) agt ON TRUE
                WHERE a.plant_id = %s AND a.id > %s AND COALESCE(agt.alert_count, 0) > 0
                ORDER BY a.id
                LIMIT %s;
            """
        rows = await fetch_all(sql, (pid, after_id, ps))
        next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
        return {
            "plant": plant,
            "detail": detail or "summary",
            "count": len(rows),
            "next_cursor": next_cursor,
            "machines": rows,
        }

    # -------- machines_with_high_risk_in_plant --------
    @mcp.tool(description="Máquinas con riesgo HIGH en una planta (detail=names|summary|full).")
    async def machines_with_high_risk_in_plant(
        plant_name: Optional[str] = None,
        plant_id: Optional[str] = None,
        detail: Optional[str] = "summary",
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        # Resolver planta
        plant = None
        if plant_id:
            plant = await fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND id = %s",
                (int(plant_id),),
            )
        elif plant_name:
            plant = await fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND name = %s",
                (plant_name,),
            )
            if not plant:
                candidates = await fetch_all(
                    "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL"
                )
                target = _norm(plant_name)
                plant = next((p for p in candidates if _norm(p["name"]) == target or p["acs_code"] == plant_name), None)
        if not plant:
            return {"error": "Plant not found"}

        ps = _page_size(page_size)
        after_id = int(cursor) if cursor else 0
        pid = int(plant["id"])

        if detail == "names":
            sql = """
                SELECT id::text AS id, name
                FROM public.mpredict_asset
                WHERE plant_id = %s AND id > %s AND risk_level = 'HIGH'
                ORDER BY id
                LIMIT %s;
            """
        elif detail == "full":
            sql = """
                SELECT id::text AS id, name, plant_id::text AS plant_id, area,
                       risk_score AS "riskScore", risk_level AS "riskLevel"
                FROM public.mpredict_asset
                WHERE plant_id = %s AND id > %s AND risk_level = 'HIGH'
                ORDER BY id
                LIMIT %s;
            """
        else:
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
                             ORDER BY { _risk_order_sql('m2') } DESC
                             LIMIT 1
                           ) AS top_level
                    FROM public.mpredict_mpredictalert m
                    WHERE m.asset_id = a.id
                ) agt ON TRUE
                WHERE a.plant_id = %s AND a.id > %s AND a.risk_level = 'HIGH'
                ORDER BY a.id
                LIMIT %s;
            """
        rows = await fetch_all(sql, (pid, after_id, ps))
        next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
        return {
            "plant": plant,
            "detail": detail or "summary",
            "count": len(rows),
            "next_cursor": next_cursor,
            "machines": rows,
        }

    # -------- list_machines (global) --------
    @mcp.tool(description="Lista máquinas global (detail=names|summary|full), paginación keyset.")
    async def list_machines(
        detail: Optional[str] = "summary",
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        ps = _page_size(page_size)
        after_id = int(cursor) if cursor else 0

        if detail == "names":
            sql = """
                SELECT id::text AS id, name
                FROM public.mpredict_asset
                WHERE id > %s
                ORDER BY id
                LIMIT %s;
            """
        elif detail == "full":
            sql = """
                SELECT id::text AS id, name, plant_id::text AS plant_id, area,
                       risk_score AS "riskScore", risk_level AS "riskLevel"
                FROM public.mpredict_asset
                WHERE id > %s
                ORDER BY id
                LIMIT %s;
            """
        else:
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
                             ORDER BY { _risk_order_sql('m2') } DESC
                             LIMIT 1
                           ) AS top_level
                    FROM public.mpredict_mpredictalert m
                    WHERE m.asset_id = a.id
                ) agt ON TRUE
                WHERE a.id > %s
                ORDER BY a.id
                LIMIT %s;
            """
        rows = await fetch_all(sql, (after_id, ps))
        next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
        return {"detail": detail or "summary", "count": len(rows), "next_cursor": next_cursor, "machines": rows}

    # -------- machines_with_open_alerts (global) --------
    @mcp.tool(description="Máquinas con alertas abiertas (global).")
    async def machines_with_open_alerts(
        detail: Optional[str] = "summary",
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        ps = _page_size(page_size)
        after_id = int(cursor) if cursor else 0

        if detail == "names":
            sql = """
                SELECT a.id::text AS id, a.name
                FROM public.mpredict_asset a
                WHERE a.id > %s
                  AND EXISTS (
                      SELECT 1 FROM public.mpredict_mpredictalert m
                      WHERE m.asset_id = a.id AND lower(m.state)='open'
                  )
                ORDER BY a.id
                LIMIT %s;
            """
        elif detail == "full":
            sql = """
                SELECT a.id::text AS id, a.name, a.plant_id::text AS plant_id, a.area,
                       a.risk_score AS "riskScore", a.risk_level AS "riskLevel"
                FROM public.mpredict_asset a
                WHERE a.id > %s
                  AND EXISTS (
                      SELECT 1 FROM public.mpredict_mpredictalert m
                      WHERE m.asset_id = a.id AND lower(m.state)='open'
                  )
                ORDER BY a.id
                LIMIT %s;
            """
        else:
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
                             ORDER BY { _risk_order_sql('m2') } DESC
                             LIMIT 1
                           ) AS top_level
                    FROM public.mpredict_mpredictalert m
                    WHERE m.asset_id = a.id AND lower(m.state)='open'
                ) agt ON TRUE
                WHERE a.id > %s AND COALESCE(agt.alert_count, 0) > 0
                ORDER BY a.id
                LIMIT %s;
            """
        rows = await fetch_all(sql, (after_id, ps))
        next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
        return {"detail": detail or "summary", "count": len(rows), "next_cursor": next_cursor, "machines": rows}

    # -------- machines_with_high_risk (global) --------
    @mcp.tool(description="Máquinas con riesgo HIGH (global).")
    async def machines_with_high_risk(
        detail: Optional[str] = "summary",
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        ps = _page_size(page_size)
        after_id = int(cursor) if cursor else 0

        if detail == "names":
            sql = """
                SELECT id::text AS id, name
                FROM public.mpredict_asset
                WHERE id > %s AND risk_level='HIGH'
                ORDER BY id
                LIMIT %s;
            """
        elif detail == "full":
            sql = """
                SELECT id::text AS id, name, plant_id::text AS plant_id, area,
                       risk_score AS "riskScore", risk_level AS "riskLevel"
                FROM public.mpredict_asset
                WHERE id > %s AND risk_level='HIGH'
                ORDER BY id
                LIMIT %s;
            """
        else:
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
                             ORDER BY { _risk_order_sql('m2') } DESC
                             LIMIT 1
                           ) AS top_level
                    FROM public.mpredict_mpredictalert m
                    WHERE m.asset_id = a.id
                ) agt ON TRUE
                WHERE a.id > %s AND a.risk_level='HIGH'
                ORDER BY a.id
                LIMIT %s;
            """
        rows = await fetch_all(sql, (after_id, ps))
        next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
        return {"detail": detail or "summary", "count": len(rows), "next_cursor": next_cursor, "machines": rows}

    # -------- list_alerts_in_plant --------
    @mcp.tool(description="Lista alertas de una planta (state=Open|Closed|Any). include_features opcional.")
    async def list_alerts_in_plant(
        plant_name: Optional[str] = None,
        plant_id: Optional[str] = None,
        state: Optional[str] = "Open",
        include_features: Optional[bool] = False,
        max_features: Optional[int] = None,
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        s = (state or "Any").strip().lower()
        if s not in ("open", "closed", "any"):
            return {"error": "state debe ser Open|Closed|Any"}

        # Resolver planta
        plant = None
        if plant_id:
            plant = await fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND id = %s",
                (int(plant_id),),
            )
        elif plant_name:
            plant = await fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND name = %s",
                (plant_name,),
            )
            if not plant:
                candidates = await fetch_all(
                    "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL"
                )
                target = _norm(plant_name)
                plant = next((p for p in candidates if _norm(p["name"]) == target or p["acs_code"] == plant_name), None)
        if not plant:
            return {"error": "Plant not found"}

        ps = _page_size(page_size)
        after_id = int(cursor) if cursor else 0
        pid = int(plant["id"])

        where_state = "" if s == "any" else "AND lower(m.state) = %s"

        if s == "any":
            params = (pid, after_id, ps)
        else:
            params = (pid, after_id, s, ps)

        select_features = "m.feature_contribution" if include_features else "NULL::jsonb"
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
                {select_features} AS features
            FROM public.mpredict_mpredictalert m
            JOIN public.mpredict_asset a ON a.id = m.asset_id
            WHERE a.plant_id = %s
            AND m.id > %s
            {where_state}
            ORDER BY m.id
            LIMIT %s;
        """

        rows = await fetch_all(sql, params)

        mf = int(max_features) if (include_features and max_features) else 0
        alerts_out = []
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
            feats = r.get("features")
            if include_features and feats is not None:
                if isinstance(feats, list):
                    try:
                        feats = sorted(feats, key=lambda x: float(x.get("contribution", 0) or 0), reverse=True)
                    except Exception:
                        pass
                    if mf > 0:
                        feats = feats[:mf]
                item["alert"]["features"] = feats
            alerts_out.append(item)

        next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
        return {
            "plant": plant,
            "state": s.upper() if s != "any" else "ANY",
            "count": len(alerts_out),
            "next_cursor": next_cursor,
            "alerts": alerts_out,
        }


    # -------- list_alerts (global) --------
    @mcp.tool(description="Lista alertas global (state=Open|Closed|Any), include_features opcional.")
    async def list_alerts(
        state: Optional[str] = "Open",
        include_features: Optional[bool] = False,
        max_features: Optional[int] = None,
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        s = (state or "Any").strip().lower()
        if s not in ("open", "closed", "any"):
            return {"error": "state debe ser Open|Closed|Any"}

        ps = _page_size(page_size)
        after_id = int(cursor) if cursor else 0

        where_state = "" if s == "any" else "AND lower(m.state) = %s"

        if s == "any":
            params = (after_id, ps)
        else:
            params = (after_id, s, ps)

        select_features = "m.feature_contribution" if include_features else "NULL::jsonb"
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
                p.id::text AS plant_id,
                p.name AS plant_name,
                p.acs_code AS plant_acs_code,
                {select_features} AS features
            FROM public.mpredict_mpredictalert m
            JOIN public.mpredict_asset a ON a.id = m.asset_id
            JOIN public.plants_plant p ON p.id = a.plant_id
            WHERE m.id > %s
            {where_state}
            ORDER BY m.id
            LIMIT %s;
        """

        rows = await fetch_all(sql, params)

        mf = int(max_features) if (include_features and max_features) else 0
        alerts_out = []
        for r in rows:
            alert_obj = {
                "id": r["id"],
                "alert_id": r["alert_id"],
                "timestamp": r["timestamp"],
                "name": r["name"],
                "state": r["state"],
                "riskScore": r["riskScore"],
                "riskLevel": r["riskLevel"],
            }
            feats = r.get("features")
            if include_features and feats is not None:
                if isinstance(feats, list):
                    try:
                        feats = sorted(feats, key=lambda x: float(x.get("contribution", 0) or 0), reverse=True)
                    except Exception:
                        pass
                    if mf > 0:
                        feats = feats[:mf]
                alert_obj["features"] = feats

            alerts_out.append({
                "plant": {"id": r["plant_id"], "name": r["plant_name"], "acs_code": r["plant_acs_code"]},
                "machine": {"id": r["machine_id"], "name": r["machine_name"]},
                "alert": alert_obj,
            })

        next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
        return {"state": s.upper() if s != "any" else "ANY", "count": len(alerts_out), "next_cursor": next_cursor, "alerts": alerts_out}

    # -------- get_alert_features --------
    @mcp.tool(description="Devuelve features para una alerta (por numeric id o UUID).")
    async def get_alert_features(
        alert_id: Optional[str] = None,
        alert_numeric_id: Optional[str] = None,
        max_features: Optional[int] = None,
    ) -> dict:
        if not alert_id and not alert_numeric_id:
            return {"error": "Debe indicar 'alert_id' (UUID) o 'alert_numeric_id'."}

        if alert_numeric_id:
            row = await fetch_one(
                "SELECT id::text AS id, alert_id, feature_contribution AS features FROM public.mpredict_mpredictalert WHERE id = %s",
                (int(alert_numeric_id),),
            )
        else:
            row = await fetch_one(
                "SELECT id::text AS id, alert_id, feature_contribution AS features FROM public.mpredict_mpredictalert WHERE alert_id = %s",
                (alert_id,),
            )
        if not row:
            return {"error": "Alert not found"}

        feats = row.get("features")
        mf = int(max_features) if max_features else 0
        if isinstance(feats, list) and mf > 0:
            try:
                feats = sorted(feats, key=lambda x: float(x.get("contribution", 0) or 0), reverse=True)[:mf]
            except Exception:
                feats = feats[:mf]

        return {
            "alert": {"id": row["id"], "alert_id": row["alert_id"]},
            "count": (len(feats) if isinstance(feats, list) else 0),
            "features": feats,
        }

    # -------- get_alert_features_in_plant --------
    @mcp.tool(description="Devuelve features de una alerta dentro de una planta.")
    async def get_alert_features_in_plant(
        plant_name: Optional[str] = None,
        plant_id: Optional[str] = None,
        alert_id: Optional[str] = None,
        alert_numeric_id: Optional[str] = None,
        machine_id: Optional[str] = None,
        machine_name: Optional[str] = None,
        max_features: Optional[int] = None,
    ) -> dict:
        if not alert_id and not alert_numeric_id:
            return {"error": "Debe indicar 'alert_id' (UUID) o 'alert_numeric_id'."}

        # Resolver planta
        plant = None
        if plant_id:
            plant = await fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND id = %s",
                (int(plant_id),),
            )
        elif plant_name:
            plant = await fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND name = %s",
                (plant_name,),
            )
            if not plant:
                candidates = await fetch_all(
                    "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL"
                )
                target = _norm(plant_name)
                plant = next((p for p in candidates if _norm(p["name"]) == target or p["acs_code"] == plant_name), None)
        if not plant:
            return {"error": "Plant not found"}

        pid = int(plant["id"])

        # Opcionalmente filtrar por máquina
        machine_filter_sql = ""
        params: List[Any] = [pid]
        if machine_id:
            machine_filter_sql = "AND a.id = %s"
            params.append(int(machine_id))
        elif machine_name:
            machine_filter_sql = "AND a.name = %s"
            params.append(machine_name)

        # IDs de alerta
        if alert_numeric_id:
            alert_where = "m.id = %s"
            params.append(int(alert_numeric_id))
        else:
            alert_where = "m.alert_id = %s"
            params.append(alert_id)

        sql = f"""
            SELECT
                p.id::text   AS plant_id, p.name AS plant_name, p.acs_code,
                a.id::text   AS machine_id, a.name AS machine_name,
                m.id::text   AS id, m.alert_id, m.timestamp, m.state,
                m.risk_score AS "riskScore", m.risk_level AS "riskLevel",
                m.feature_contribution AS features
            FROM public.mpredict_mpredictalert m
            JOIN public.mpredict_asset a ON a.id = m.asset_id
            JOIN public.plants_plant p ON p.id = a.plant_id
            WHERE p.id = %s
              {machine_filter_sql}
              AND {alert_where}
            LIMIT 1;
        """
        row = await fetch_one(sql, tuple(params))
        if not row:
            return {"plant": {"id": plant["id"], "name": plant["name"]}, "error": "Alert not found with given criteria"}

        feats = row.get("features")
        mf = int(max_features) if max_features else 0
        if isinstance(feats, list) and mf > 0:
            try:
                feats = sorted(feats, key=lambda x: float(x.get("contribution", 0) or 0), reverse=True)[:mf]
            except Exception:
                feats = feats[:mf]

        return {
            "context": {
                "plant": {"id": row["plant_id"], "name": row["plant_name"], "acs_code": row["acs_code"]},
                "machine": {"id": row["machine_id"], "name": row["machine_name"]},
                "alert": {
                    "id": row["id"], "alert_id": row["alert_id"], "timestamp": row["timestamp"],
                    "state": row["riskLevel"], "riskScore": row["riskScore"], "riskLevel": row["riskLevel"],
                },
            },
            "count": (len(feats) if isinstance(feats, list) else 0),
            "features": feats,
        }

    return mcp

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main():
    server = create_server()
    
    logger.info(f"Starting MCP server '{MCP_NAME}' on {MCP_HOST}:{MCP_PORT}{MCP_PATH} (HTTP Stream)"
                f" | AUTH_DISABLED={AUTH_DISABLED}")
    try:
        server.run(transport="http", host=MCP_HOST, port=MCP_PORT, path=MCP_PATH)
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.exception(f"Server error: {e}")
        raise

if __name__ == "__main__":
    main()
