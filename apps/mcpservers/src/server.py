#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Plant Risk MCP Server (compact output + pagination)

Archivos esperados (formatos estrictos):
- ./data/plants.json                   -> objeto con clave 'plants' (array)
- ./data/assets_full_<PLANT_ID>.json   -> objeto con clave 'assets' (array)

Objetivo: reducir tokens en respuestas por defecto.
- 'detail' = "summary" (por defecto) devuelve máquinas compactas
- 'detail' = "names"   devuelve {id, name}
- 'detail' = "full"    devuelve el objeto completo (ojo: grande)

Paginación:
- 'page_size' (por defecto 50) y 'cursor' (string)
- Respuesta incluye 'next_cursor' (o null si no hay más)

Alertas:
- En listados, por defecto 'include_features=false' (cero ruido)
- Si 'include_features=true', recorta a 'max_features' (por defecto 3)

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
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Callable

from fastapi.responses import JSONResponse
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.providers.google import GoogleProvider

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
LOGLEVEL = os.getenv("FASTMCP_LOGLEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOGLEVEL, logging.INFO))
logger = logging.getLogger("plant-risk-mcp")

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
ROOT = Path(__file__).parent
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
MCP_NAME = os.getenv("MCP_NAME", "mcp-server")
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "18000"))

DEFAULT_PAGE_SIZE = int(os.getenv("DEFAULT_PAGE_SIZE", "50"))
DEFAULT_MAX_FEATURES = int(os.getenv("DEFAULT_MAX_FEATURES", "3"))

SERVER_INSTRUCTIONS = """
This MCP server serves plant risk data from local JSON files under ./data.
Auth: Google OAuth. Access restricted by email whitelist (ALLOWED_EMAILS).
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

# -----------------------------------------------------------------------------
# Helpers (strict I/O)
# -----------------------------------------------------------------------------
def _read_json(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"

def _ensure_plants_payload(raw: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw, dict) or "plants" not in raw or not isinstance(raw["plants"], list):
        raise ValueError("plants.json debe ser un objeto con la clave 'plants' (array)")
    return raw["plants"]

def _ensure_assets_payload(raw: Any, plant_id: str) -> List[Dict[str, Any]]:
    if not isinstance(raw, dict) or "assets" not in raw or not isinstance(raw["assets"], list):
        raise ValueError(f"assets_full_{plant_id}.json debe ser un objeto con la clave 'assets' (array)")
    return raw["assets"]

def _load_plants_strict() -> List[Dict[str, Any]]:
    path = DATA_DIR / "plants.json"
    raw = _read_json(path)
    return _ensure_plants_payload(raw)

def _load_assets_full_for_plant_strict(plant_id: str) -> List[Dict[str, Any]]:
    path = DATA_DIR / f"assets_full_{plant_id}.json"
    raw = _read_json(path)
    return _ensure_assets_payload(raw, plant_id)

def _normalize(s: str) -> str:
    s = s.strip().casefold()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    s = " ".join(s.split())
    return s

def _resolve_plant(plant_name: Optional[str], plant_id: Optional[str]) -> Tuple[str, Dict[str, Any]]:
    plants = _load_plants_strict()
    pmap_by_id = {str(p.get("id")): p for p in plants}

    if plant_name:
        target = _normalize(plant_name)
        # match por nombre
        for p in plants:
            if _normalize(str(p.get("name", ""))) == target:
                return str(p.get("id")), p
        # match por acs_code
        for p in plants:
            if str(p.get("acs_code", "")).strip().upper() == plant_name.strip().upper():
                return str(p.get("id")), p
        raise ValueError(f"Plant not found by name/code: {plant_name}")

    if plant_id:
        pid = str(plant_id)
        if pid in pmap_by_id:
            return pid, pmap_by_id[pid]
        raise ValueError(f"Plant not found by id: {plant_id}")

    raise ValueError("Debe indicar 'plant_name' (o alternativamente 'plant_id').")

# -----------------------------------------------------------------------------
# Compact / pagination utils
# -----------------------------------------------------------------------------
def _compact_machine(m: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": m.get("id"),
        "name": m.get("name"),
        "plant_id": m.get("plant_id"),
        "area": m.get("area"),
        "riskScore": m.get("riskScore"),
        "riskLevel": m.get("riskLevel"),
        "hasAlerts": m.get("hasAlerts"),
        "alertCount": m.get("alertCount"),
        "lastAlertAt": m.get("lastAlertAt"),
        "topAlertRiskLevel": m.get("topAlertRiskLevel"),
    }

def _names_machine(m: Dict[str, Any]) -> Dict[str, Any]:
    return {"id": m.get("id"), "name": m.get("name")}

def _page(items: List[Any], cursor: Optional[str], page_size: Optional[int],
          key_fn: Optional[Callable[[Any], str]] = None) -> Tuple[List[Any], Optional[str]]:
    if not items:
        return [], None
    ps = max(1, int(page_size or DEFAULT_PAGE_SIZE))
    start = 0
    if cursor:
        if key_fn is None:
            # Búsqueda por id o 'id' en el dict
            for i, it in enumerate(items):
                key = str(it.get("id")) if isinstance(it, dict) and "id" in it else str(it)
                if key == str(cursor):
                    start = i + 1
                    break
        else:
            for i, it in enumerate(items):
                if key_fn(it) == str(cursor):
                    start = i + 1
                    break
    end = min(start + ps, len(items))
    page_slice = items[start:end]
    if end < len(items):
        # next cursor = clave del último elemento devuelto
        last = page_slice[-1]
        next_cursor = key_fn(last) if key_fn else (str(last.get("id")) if isinstance(last, dict) and "id" in last else str(last))
    else:
        next_cursor = None
    return page_slice, next_cursor

def _state_norm(state: Optional[str]) -> Optional[str]:
    if state is None:
        return None
    s = state.strip().lower()
    if s in ("any", "all", ""):
        return None
    if s in ("open", "closed"):
        return s
    raise ValueError("state debe ser 'Open', 'Closed' o 'Any'")

def _trim_features(features: Any, max_features: int) -> Any:
    if not isinstance(features, list):
        return [] if features is None else features
    if max_features is None or max_features <= 0:
        return features
    # ordenar por contribution desc si es posible
    try:
        feats = sorted(features, key=lambda f: float(f.get("contribution", 0) or 0), reverse=True)
    except Exception:
        feats = features
    return feats[:max_features]

def _summ_alert(a: Dict[str, Any], include_features: bool, max_features: int) -> Dict[str, Any]:
    out = {
        "id": a.get("id"),
        "alert_id": a.get("alert_id"),
        "timestamp": a.get("timestamp"),
        "name": a.get("name"),
        "state": a.get("state"),
        "riskScore": a.get("riskScore"),
        "riskLevel": a.get("riskLevel"),
        "asset_id": a.get("asset_id"),
        "origin": a.get("origin"),
    }
    if include_features:
        out["features"] = _trim_features(a.get("features", []), max_features)
    return out

def _machines_with_open_alerts(machines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for m in machines:
        alerts = m.get("alerts", []) or []
        if any(str(a.get("state", "")).strip().lower() == "open" for a in alerts):
            out.append(m)
    return out

def _machines_with_high_risk(machines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [m for m in machines if str(m.get("riskLevel", "")).upper() == "HIGH"]

def _load_all_machines() -> List[Dict[str, Any]]:
    # Lee todas las plantas y concatena los assets (OJO: solo para listados globales)
    machines: List[Dict[str, Any]] = []
    for p in _load_plants_strict():
        pid = str(p.get("id"))
        try:
            machines.extend(_load_assets_full_for_plant_strict(pid))
        except Exception as e:
            logger.warning(f"No se pudieron cargar assets de plant_id={pid}: {e}")
    return machines

def _find_alert_in_machines(machines: List[Dict[str, Any]],
                            alert_id: Optional[str],
                            alert_numeric_id: Optional[str]) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    for m in machines:
        for a in m.get("alerts", []) or []:
            if alert_id is not None and str(a.get("alert_id")) == str(alert_id):
                return m, a
            if alert_numeric_id is not None and str(a.get("id")) == str(alert_numeric_id):
                return m, a
    return None

def _parse_ts(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    s = str(ts)
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None

def _select_alert(alerts: List[Dict[str, Any]],
                  state: Optional[str],
                  alert_id: Optional[str],
                  alert_numeric_id: Optional[str],
                  pick: Optional[str]) -> Optional[Dict[str, Any]]:
    # IDs tienen prioridad
    for a in alerts or []:
        if alert_id is not None and str(a.get("alert_id")) == str(alert_id):
            return a
        if alert_numeric_id is not None and str(a.get("id")) == str(alert_numeric_id):
            return a

    s = _state_norm(state)
    cand = [a for a in (alerts or []) if (s is None or str(a.get("state", "")).strip().lower() == s)]
    if not cand:
        return None

    strategy = (pick or "latest").strip().lower()
    if strategy == "highest_risk":
        return max(cand, key=lambda a: float(a.get("riskScore", 0) or 0.0))
    # default: latest
    return max(cand, key=lambda a: (_parse_ts(a.get("timestamp")) or datetime.min))

def _normalize_name(s: Optional[str]) -> Optional[str]:
    return _normalize(s) if s else None

def _find_machine_global(machine_id: Optional[str], machine_name: Optional[str]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    machines = _load_all_machines()
    plants = _load_plants_strict()
    plant_map = {str(p.get("id")): p for p in plants}

    matches: List[Dict[str, Any]] = []
    target_name = _normalize_name(machine_name)

    for m in machines:
        if machine_id is not None and str(m.get("id")) == str(machine_id):
            matches = [m]
            break
        if target_name is not None and _normalize(str(m.get("name", ""))) == target_name:
            matches.append(m)

    if not matches:
        raise ValueError("Machine not found with the given criteria")
    if len(matches) > 1:
        raise ValueError("Ambiguous machine name; multiple matches found")

    m = matches[0]
    plant_obj = plant_map.get(str(m.get("plant_id"))) if m.get("plant_id") is not None else None
    return m, plant_obj

# -----------------------------------------------------------------------------
# Middleware con logs detallados
# -----------------------------------------------------------------------------
def _extract_email_from_context(fctx) -> Optional[str]:
    """
    Intenta sacar el email del contexto de FastMCP de forma robusta:
    - fctx.user.email
    - fctx.user.get("email")
    - fctx.token.claims["email"]
    """
    try:
        user = getattr(fctx, "user", None)
        if user:
            # Puede ser objeto con .email o dict
            e1 = getattr(user, "email", None)
            if e1:
                return str(e1).lower()
            if isinstance(user, dict) and user.get("email"):
                return str(user["email"]).lower()
        token = getattr(fctx, "token", None)
        claims = getattr(token, "claims", None) if token else None
        if isinstance(claims, dict) and claims.get("email"):
            return str(claims["email"]).lower()
    except Exception as ex:
        logger.warning(f"[AUTH] error extrayendo email del contexto: {ex}")
    return None

class EmailWhitelistMiddleware(Middleware):
    async def on_call_tool(self, context: MiddlewareContext, call_next):
        fctx = context.fastmcp_context
        logger.debug(f"[MW on_call_tool] ctx={bool(fctx)} tool={getattr(context, 'tool_name', None)}")
        if fctx is None or getattr(fctx, "token", None) is None:
            logger.info("[MW on_call_tool] NO AUTH CONTEXT o token ausente -> denegado")
            raise ToolError("Not authenticated")

        # Log de claims disponibles
        token = getattr(fctx, "token", None)
        claims = getattr(token, "claims", None) if token else None
        logger.debug(f"[MW on_call_tool] claims_keys={list(claims.keys()) if isinstance(claims, dict) else None}")

        email = _extract_email_from_context(fctx)
        logger.info(f"[MW on_call_tool] email_detectado={email}")
        if not email:
            logger.info("[MW on_call_tool] sin email en claims -> denegado")
            raise ToolError("User without email claim")

        if email not in ALLOWED_EMAILS:
            logger.info(f"[MW on_call_tool] email {email} NO autorizado -> denegado")
            raise ToolError("User not allowed")

        logger.info(f"[MW on_call_tool] acceso permitido a {email}")
        return await call_next(context)

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        fctx = context.fastmcp_context
        logger.debug(f"[MW on_list_tools] ctx={bool(fctx)}")
        if fctx is None or getattr(fctx, "token", None) is None:
            logger.info("[MW on_list_tools] NO AUTH CONTEXT o token ausente -> devolver []")
            return []

        token = getattr(fctx, "token", None)
        claims = getattr(token, "claims", None) if token else None
        logger.debug(f"[MW on_list_tools] claims_keys={list(claims.keys()) if isinstance(claims, dict) else None}")

        email = _extract_email_from_context(fctx)
        logger.info(f"[MW on_list_tools] email_detectado={email}")
        if not email:
            logger.info("[MW on_list_tools] sin email en claims -> devolver []")
            return []

        if email not in ALLOWED_EMAILS:
            logger.info(f"[MW on_list_tools] email {email} NO autorizado -> devolver []")
            return []

        logger.info(f"[MW on_list_tools] email autorizado -> mostrar tools")
        return await call_next(context)

# -----------------------------------------------------------------------------
# Server (tools)
# -----------------------------------------------------------------------------
def create_server() -> FastMCP:
    mcp = FastMCP(name=MCP_NAME, auth=google_auth, instructions=SERVER_INSTRUCTIONS)

    async def root_ok(request):
        return JSONResponse({"status": "ok", "server": MCP_NAME, "time": _now_iso()})

    try:
        mcp.app.add_api_route("/", root_ok, methods=["GET"])
    except Exception as e:
        logger.warning(f"No se pudo registrar '/' (no crítico): {e}")
        
    # Añadimos middleware
    # mcp.add_middleware(EmailWhitelistMiddleware())

    @mcp.tool()
    def health() -> dict:
        return {"status": "ok", "time": _now_iso()}

    # --- Search flexible (planta / máquina / alerta / componentes) ---
    @mcp.tool(annotations={"title": "Search info (plant / machine / alert)"})
    def search_info(
        plant_id: Optional[str] = None,
        plant_name: Optional[str] = None,
        machine_id: Optional[str] = None,
        machine_name: Optional[str] = None,
        alert_id: Optional[str] = None,
        include_components: bool = False,
        max_alerts: int = 5,
        max_components: int = 5
    ) -> Dict[str, Any]:
        try:
            pid, plant = _resolve_plant(plant_name, plant_id)
        except Exception as e:
            return {"error": f"Plant not found: {e}"}

        result: Dict[str, Any] = {
            "plant": {"id": pid, "name": plant.get("name"), "acs_code": plant.get("acs_code")}
        }

        machines = _load_assets_full_for_plant_strict(pid)

        if machine_id or machine_name:
            filtered = []
            nname = _normalize(machine_name) if machine_name else None
            for m in machines:
                if machine_id and str(m.get("id")) == str(machine_id):
                    filtered.append(m)
                elif nname and _normalize(str(m.get("name", ""))) == nname:
                    filtered.append(m)
            machines = filtered

        out_machines = []
        for m in machines:
            m_obj: Dict[str, Any] = {
                "id": m.get("id"),
                "name": m.get("name"),
                "plant_id": m.get("plant_id"),
                "area": m.get("area"),
                "riskScore": m.get("riskScore"),
                "riskLevel": m.get("riskLevel"),
                "hasAlerts": m.get("hasAlerts"),
                "alertCount": m.get("alertCount"),
                "lastAlertAt": m.get("lastAlertAt"),
                "topAlertRiskLevel": m.get("topAlertRiskLevel"),
            }
            alerts = m.get("alerts", []) or []
            if alert_id:
                alerts = [a for a in alerts if str(a.get("alert_id")) == str(alert_id) or str(a.get("id")) == str(alert_id)]
            alerts = alerts[:max_alerts]
            out_alerts = []
            for a in alerts:
                a_obj = {
                    "id": a.get("id"),
                    "alert_id": a.get("alert_id"),
                    "name": a.get("name"),
                    "state": a.get("state"),
                    "riskScore": a.get("riskScore"),
                    "riskLevel": a.get("riskLevel"),
                    "timestamp": a.get("timestamp"),
                }
                if include_components:
                    comps = a.get("features", []) or []
                    a_obj["features"] = _trim_features(comps, max_components)
                out_alerts.append(a_obj)
            m_obj["alerts"] = out_alerts
            out_machines.append(m_obj)

        result["machines"] = out_machines
        return result

    # ---------- PLANTS ----------
    @mcp.tool()
    def list_plants() -> dict:
        try:
            return {"plants": _load_plants_strict()}
        except Exception as e:
            return {"error": str(e)}

    # ---------- PLANT-SCOPED MACHINES ----------
    @mcp.tool()
    def list_machines_for_plant(
        plant_name: Optional[str] = None,
        plant_id: Optional[str] = None,
        detail: Optional[str] = "summary",  # names | summary | full
        page_size: Optional[int] = None,
        cursor: Optional[str] = None
    ) -> dict:
        try:
            pid, plant = _resolve_plant(plant_name, plant_id)
            machines = _load_assets_full_for_plant_strict(pid)

            if detail == "names":
                proj = [_names_machine(m) for m in machines]
            elif detail == "full":
                proj = machines
            else:
                proj = [_compact_machine(m) for m in machines]

            page, next_cursor = _page(proj, cursor, page_size)
            return {
                "plant": {"id": plant["id"], "name": plant["name"], "acs_code": plant.get("acs_code")},
                "detail": detail or "summary",
                "count": len(page),
                "next_cursor": next_cursor,
                "machines": page
            }
        except Exception as e:
            return {"error": str(e), "plant_name": plant_name, "plant_id": plant_id}

    @mcp.tool()
    def machines_with_open_alerts_in_plant(
        plant_name: Optional[str] = None,
        plant_id: Optional[str] = None,
        detail: Optional[str] = "summary",
        page_size: Optional[int] = None,
        cursor: Optional[str] = None
    ) -> dict:
        try:
            pid, plant = _resolve_plant(plant_name, plant_id)
            machines = _load_assets_full_for_plant_strict(pid)
            filtered = _machines_with_open_alerts(machines)

            if detail == "names":
                proj = [_names_machine(m) for m in filtered]
            elif detail == "full":
                proj = filtered
            else:
                proj = [_compact_machine(m) for m in filtered]

            page, next_cursor = _page(proj, cursor, page_size)
            return {
                "plant": {"id": plant["id"], "name": plant["name"], "acs_code": plant.get("acs_code")},
                "detail": detail or "summary",
                "count": len(page),
                "next_cursor": next_cursor,
                "machines": page
            }
        except Exception as e:
            return {"error": str(e), "plant_name": plant_name, "plant_id": plant_id}

    @mcp.tool()
    def machines_with_high_risk_in_plant(
        plant_name: Optional[str] = None,
        plant_id: Optional[str] = None,
        detail: Optional[str] = "summary",
        page_size: Optional[int] = None,
        cursor: Optional[str] = None
    ) -> dict:
        try:
            pid, plant = _resolve_plant(plant_name, plant_id)
            machines = _load_assets_full_for_plant_strict(pid)
            filtered = _machines_with_high_risk(machines)

            if detail == "names":
                proj = [_names_machine(m) for m in filtered]
            elif detail == "full":
                proj = filtered
            else:
                proj = [_compact_machine(m) for m in filtered]

            page, next_cursor = _page(proj, cursor, page_size)
            return {
                "plant": {"id": plant["id"], "name": plant["name"], "acs_code": plant.get("acs_code")},
                "detail": detail or "summary",
                "count": len(page),
                "next_cursor": next_cursor,
                "machines": page
            }
        except Exception as e:
            return {"error": str(e), "plant_name": plant_name, "plant_id": plant_id}

    # ---------- GLOBAL MACHINES ----------
    @mcp.tool()
    def list_machines(
        detail: Optional[str] = "summary",
        page_size: Optional[int] = None,
        cursor: Optional[str] = None
    ) -> dict:
        try:
            machines = _load_all_machines()

            if detail == "names":
                proj = [_names_machine(m) for m in machines]
            elif detail == "full":
                proj = machines
            else:
                proj = [_compact_machine(m) for m in machines]

            page, next_cursor = _page(proj, cursor, page_size)
            return {"detail": detail or "summary", "count": len(page), "next_cursor": next_cursor, "machines": page}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def machines_with_open_alerts(
        detail: Optional[str] = "summary",
        page_size: Optional[int] = None,
        cursor: Optional[str] = None
    ) -> dict:
        try:
            machines = _machines_with_open_alerts(_load_all_machines())
            if detail == "names":
                proj = [_names_machine(m) for m in machines]
            elif detail == "full":
                proj = machines
            else:
                proj = [_compact_machine(m) for m in machines]
            page, next_cursor = _page(proj, cursor, page_size)
            return {"detail": detail or "summary", "count": len(page), "next_cursor": next_cursor, "machines": page}
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def machines_with_high_risk(
        detail: Optional[str] = "summary",
        page_size: Optional[int] = None,
        cursor: Optional[str] = None
    ) -> dict:
        try:
            machines = _machines_with_high_risk(_load_all_machines())
            if detail == "names":
                proj = [_names_machine(m) for m in machines]
            elif detail == "full":
                proj = machines
            else:
                proj = [_compact_machine(m) for m in machines]
            page, next_cursor = _page(proj, cursor, page_size)
            return {"detail": detail or "summary", "count": len(page), "next_cursor": next_cursor, "machines": page}
        except Exception as e:
            return {"error": str(e)}

    # ---------- ALERTS (lists) ----------
    def _collect_alert_items(machines: List[Dict[str, Any]], state: Optional[str] = None) -> List[Dict[str, Any]]:
        s = _state_norm(state)
        items: List[Dict[str, Any]] = []
        for m in machines:
            m_info = {"id": m.get("id"), "name": m.get("name"), "plant_id": m.get("plant_id")}
            for a in m.get("alerts", []) or []:
                a_state = str(a.get("state", "")).strip().lower()
                if s is None or a_state == s:
                    items.append({"machine": m_info, "alert": a})
        return items

    @mcp.tool()
    def list_alerts_in_plant(
        plant_name: Optional[str] = None,
        plant_id: Optional[str] = None,
        state: Optional[str] = "Open",
        include_features: Optional[bool] = False,
        max_features: Optional[int] = None,
        page_size: Optional[int] = None,
        cursor: Optional[str] = None
    ) -> dict:
        try:
            pid, plant = _resolve_plant(plant_name, plant_id)
            machines = _load_assets_full_for_plant_strict(pid)
            items = _collect_alert_items(machines, state)

            maxf = DEFAULT_MAX_FEATURES if (include_features and not max_features) else int(max_features or 0)
            proj = [{
                "machine": {"id": it["machine"]["id"], "name": it["machine"]["name"]},
                "alert": _summ_alert(it["alert"], include_features=bool(include_features), max_features=maxf)
            } for it in items]

            def _ts_key(x): return _parse_ts(x["alert"].get("timestamp")) or datetime.min
            proj.sort(key=_ts_key, reverse=True)

            page, next_cursor = _page(proj, cursor, page_size, key_fn=lambda it: str(it["alert"]["id"]))
            return {
                "plant": {"id": plant["id"], "name": plant["name"], "acs_code": plant.get("acs_code")},
                "state": (_state_norm(state) or "any").upper(),
                "count": len(page),
                "next_cursor": next_cursor,
                "alerts": page
            }
        except Exception as e:
            return {"error": str(e), "plant_name": plant_name, "plant_id": plant_id}

    @mcp.tool()
    def list_alerts(
        state: Optional[str] = "Open",
        include_features: Optional[bool] = False,
        max_features: Optional[int] = None,
        page_size: Optional[int] = None,
        cursor: Optional[str] = None
    ) -> dict:
        try:
            machines = _load_all_machines()
            items = _collect_alert_items(machines, state)
            maxf = DEFAULT_MAX_FEATURES if (include_features and not max_features) else int(max_features or 0)
            proj = []
            plants = _load_plants_strict()
            pmap = {str(p.get("id")): {"id": p["id"], "name": p["name"], "acs_code": p.get("acs_code")} for p in plants}
            for it in items:
                pid = str(it["machine"]["plant_id"])
                proj.append({
                    "plant": pmap.get(pid),
                    "machine": {"id": it["machine"]["id"], "name": it["machine"]["name"]},
                    "alert": _summ_alert(it["alert"], include_features=bool(include_features), max_features=maxf)
                })

            def _ts_key(x): return _parse_ts(x["alert"].get("timestamp")) or datetime.min
            proj.sort(key=_ts_key, reverse=True)

            page, next_cursor = _page(proj, cursor, page_size, key_fn=lambda it: str(it["alert"]["id"]))
            return {"state": (_state_norm(state) or "any").upper(), "count": len(page), "next_cursor": next_cursor, "alerts": page}
        except Exception as e:
            return {"error": str(e)}

    # ---------- ALERT FEATURES (single alert) ----------
    @mcp.tool()
    def get_alert_features_in_plant(
        plant_name: Optional[str] = None,
        plant_id: Optional[str] = None,
        alert_id: Optional[str] = None,
        alert_numeric_id: Optional[str] = None,
        machine_id: Optional[str] = None,
        machine_name: Optional[str] = None,
        max_features: Optional[int] = None
    ) -> dict:
        try:
            if alert_id is None and alert_numeric_id is None:
                raise ValueError("Debe indicar 'alert_id' (UUID) o 'alert_numeric_id'.")
            pid, plant = _resolve_plant(plant_name, plant_id)
            machines = _load_assets_full_for_plant_strict(pid)

            if machine_id or machine_name:
                nname = _normalize(machine_name) if machine_name else None
                machines = [
                    m for m in machines
                    if (machine_id and str(m.get("id")) == str(machine_id)) or
                       (nname and _normalize(str(m.get("name",""))) == nname)
                ]

            found = _find_alert_in_machines(machines, alert_id, alert_numeric_id)
            if not found:
                return {"plant": {"id": plant["id"], "name": plant["name"]}, "error": "Alert not found with given criteria"}
            machine, alert = found
            feats = _trim_features(alert.get("features", []) or [], int(max_features or 0))
            return {
                "context": {
                    "plant": {"id": plant["id"], "name": plant["name"], "acs_code": plant.get("acs_code")},
                    "machine": {"id": machine.get("id"), "name": machine.get("name")},
                    "alert": {
                        "id": alert.get("id"), "alert_id": alert.get("alert_id"),
                        "name": alert.get("name"), "timestamp": alert.get("timestamp"),
                        "state": alert.get("state"), "riskScore": alert.get("riskScore"), "riskLevel": alert.get("riskLevel")
                    }
                },
                "count": len(feats),
                "features": feats
            }
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def get_alert_features(
        alert_id: Optional[str] = None,
        alert_numeric_id: Optional[str] = None,
        max_features: Optional[int] = None
    ) -> dict:
        try:
            if alert_id is None and alert_numeric_id is None:
                raise ValueError("Debe indicar 'alert_id' (UUID) o 'alert_numeric_id'.")
            machines = _load_all_machines()
            found = _find_alert_in_machines(machines, alert_id, alert_numeric_id)
            if not found:
                return {"error": "Alert not found"}
            machine, alert = found
            plant = next((p for p in _load_plants_strict() if str(p.get("id")) == str(machine.get("plant_id"))), None)
            feats = _trim_features(alert.get("features", []) or [], int(max_features or 0))
            return {
                "context": {
                    "plant": ({"id": plant["id"], "name": plant["name"], "acs_code": plant.get("acs_code")} if plant else None),
                    "machine": {"id": machine.get("id"), "name": machine.get("name")},
                    "alert": {
                        "id": alert.get("id"), "alert_id": alert.get("alert_id"),
                        "name": alert.get("name"), "timestamp": alert.get("timestamp"),
                        "state": alert.get("state"), "riskScore": alert.get("riskScore"), "riskLevel": alert.get("riskLevel")
                    }
                },
                "count": len(feats),
                "features": feats
            }
        except Exception as e:
            return {"error": str(e)}

    @mcp.tool()
    def get_alert_features_for_machine(
        machine_name: Optional[str] = None,
        machine_id: Optional[str] = None,
        alert_id: Optional[str] = None,
        alert_numeric_id: Optional[str] = None,
        state: Optional[str] = None,   # Open | Closed | Any
        pick: Optional[str] = "latest",
        max_features: Optional[int] = None
    ) -> dict:
        try:
            if machine_name is None and machine_id is None:
                raise ValueError("Debe indicar 'machine_name' o 'machine_id'.")
            machine, plant_obj = _find_machine_global(machine_id, machine_name)
            selected = _select_alert(machine.get("alerts", []) or [], state, alert_id, alert_numeric_id, pick)
            if not selected:
                return {
                    "plant": plant_obj,
                    "machine": {"id": machine.get("id"), "name": machine.get("name")},
                    "error": "Alert not found for the given criteria"
                }
            feats = _trim_features(selected.get("features", []) or [], int(max_features or 0))
            return {
                "context": {
                    "plant": ({"id": plant_obj["id"], "name": plant_obj["name"], "acs_code": plant_obj.get("acs_code")} if plant_obj else None),
                    "machine": {"id": machine.get("id"), "name": machine.get("name")},
                    "alert": {
                        "id": selected.get("id"), "alert_id": selected.get("alert_id"),
                        "name": selected.get("name"), "timestamp": selected.get("timestamp"),
                        "state": selected.get("state"), "riskScore": selected.get("riskScore"), "riskLevel": selected.get("riskLevel")
                    },
                    "selection": {"state_filter": (_state_norm(state) or "any").upper(), "pick": (pick or "latest").lower()}
                },
                "count": len(feats),
                "features": feats
            }
        except Exception as e:
            return {"error": str(e)}

    return mcp

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------
def main():
    logger.info(f"Data dir (STRICT): {DATA_DIR}")
    server = create_server()
    logger.info(f"Starting MCP server '{MCP_NAME}' on {MCP_HOST}:{MCP_PORT} (SSE) with email whitelist")
    try:
        server.run(transport="sse", host=MCP_HOST, port=MCP_PORT)
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.exception(f"Server error: {e}")
        raise

if __name__ == "__main__":
    main()
