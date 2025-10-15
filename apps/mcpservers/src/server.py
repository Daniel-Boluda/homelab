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
import logging

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.dependencies import get_access_token
from tools import mpredict , tis, minspect
from deps import utils

from fastapi.responses import JSONResponse

from dotenv import load_dotenv

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
        return JSONResponse({"status": "ok", "server": MCP_NAME, "time": utils._now_iso()})

    try:
        mcp.app.add_api_route("/", root_ok, methods=["GET"])
    except Exception as e:
        logger.warning(f"No se pudo registrar '/' (no crítico): {e}")

    # Añadimos middleware
    mcp.add_middleware(EmailWhitelistMiddleware())

    # -------- health --------
    @mcp.tool(description="Comprueba que el servidor está vivo y devuelve {status,time}.")
    def health() -> dict:
        return {"status": "ok", "time": utils._now_iso()}

    mpredict.register(mcp)
    minspect.register(mcp)


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
