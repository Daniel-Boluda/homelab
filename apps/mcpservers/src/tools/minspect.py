from __future__ import annotations

from typing import Optional, List, Any, Dict, Tuple
from fastmcp import FastMCP
from src.deps import db_portal
from src.deps import utils
import base64, json


# ============================================================
# Cursor helpers (opaque keyset cursor based on primary key id)
# ============================================================
def _enc_cursor(last_id: Optional[int]) -> Optional[str]:
    if not last_id:
        return None
    payload = {"id": int(last_id)}
    return base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("utf-8")


def _dec_cursor(cursor: Optional[str]) -> int:
    if not cursor:
        return 0
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode("utf-8")).decode("utf-8"))
        return int(data.get("id", 0))
    except Exception:
        return 0


def _add_filter(where: List[str], params: List[Any], clause: str, *values: Any):
    where.append(clause)
    params.extend(values)


async def _resolve_plant_id(
    plant_id: Optional[str] = None,
    plant_name: Optional[str] = None,
) -> Optional[int]:
    """Resuelve id por id o nombre (normalizado)."""
    if plant_id:
        return int(plant_id)

    if plant_name:
        # Exacto
        p = await db_portal.fetch_one(
            "SELECT id FROM public.plants_plant WHERE deleted_at IS NULL AND name = %s",
            (plant_name,),
        )
        if p:
            return int(p["id"])
        # Normalizado
        allp = await db_portal.fetch_all(
            "SELECT id, name, COALESCE(acs_code,'') AS acs_code FROM public.plants_plant WHERE deleted_at IS NULL"
        )
        target = utils._norm(plant_name)
        match = next(
            (pp for pp in allp if utils._norm(pp["name"]) == target or pp["acs_code"] == plant_name),
            None,
        )
        if match:
            return int(match["id"])
    return None


# ============================================================
# NOTIFICATIONS — shared WHERE builder
# ============================================================
def _where_notifications(
    plant_ids: Optional[List[int]] = None,
    plant_id: Optional[int] = None,
    created_by: Optional[str] = None,
    system_status: Optional[str] = None,
    priority: Optional[str] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
    week: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    planner_grb: Optional[str] = None,
) -> Tuple[str, List[Any]]:
    where: List[str] = []
    params: List[Any] = []

    if plant_id:
        _add_filter(where, params, "plant_id = %s", plant_id)
    elif plant_ids:
        # Lista segura
        placeholders = ", ".join(["%s"] * len(plant_ids))
        _add_filter(where, params, f"plant_id IN ({placeholders})", *plant_ids)

    if created_by:
        _add_filter(where, params, "created_by = %s", created_by)
    if planner_grb:
        _add_filter(where, params, "planner_grb = %s", planner_grb)
    if priority:
        _add_filter(where, params, "priority = %s", priority)
    if system_status:
        _add_filter(where, params, "system_status ILIKE %s", f"%{system_status}%")
    if year:
        _add_filter(where, params, "year = %s", int(year))
    if month:
        _add_filter(where, params, "month = %s", int(month))
    if week:
        _add_filter(where, params, "week = %s", int(week))
    if date_from:
        _add_filter(where, params, "creation_date >= %s", date_from)
    if date_to:
        _add_filter(where, params, "creation_date <= %s", date_to)

    where_sql = "WHERE " + " AND ".join(where) if where else ""
    return where_sql, params


# ============================================================
# INTERNAL IMPLS (not MCP tools)
# ============================================================
async def _list_notifications_impl(
    plant_ids: Optional[List[int]] = None,
    plant_id: Optional[int] = None,
    created_by: Optional[str] = None,
    system_status: Optional[str] = None,
    priority: Optional[str] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
    week: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    planner_grb: Optional[str] = None,
    cursor: Optional[str] = None,
    page_size: Optional[int] = 500,
    asset: Optional[str] = None,
    sort: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Global list with filters + keyset pagination.
    sort: default = -created_at => creation_date DESC, id DESC
    """
    ps = utils._page_size(page_size)

    # keyset
    last_id = _dec_cursor(cursor)

    # Build where
    where_sql, params = _where_notifications(
        plant_ids=plant_ids,
        plant_id=plant_id,
        created_by=created_by,
        system_status=system_status,
        priority=priority,
        year=year,
        month=month,
        week=week,
        date_from=date_from,
        date_to=date_to,
        planner_grb=planner_grb,
    )

    # Sort policy => use creation_date DESC then id DESC by default; keyset uses id threshold.
    # Add optional asset prefix filter on device_id or fl before keyset condition.
    if asset:
        if where_sql:
            where_sql = f"{where_sql} AND (device_id ILIKE %s OR fl ILIKE %s)"
        else:
            where_sql = "WHERE (device_id ILIKE %s OR fl ILIKE %s)"
        params.extend((f"{asset}%", f"{asset}%"))

    # We strictly keyset on id; secondary sort is stable within page.
    if where_sql:
        where_sql = f"{where_sql} AND id > %s"
    else:
        where_sql = "WHERE id > %s"
    params = list(params) + [last_id]

    sql = """
        SELECT
            id::text                  AS id,
            notification_number       AS notification_no,
            plant_id::text            AS plant_id,
            COALESCE(fl,'')           AS fl,
            created_by,
            description,
            planner_grb,
            device_id,
            system_status,
            priority,
            creation_date             AS created_at,
            year, month, week
        FROM public.minspect_minspectdata
        {where_sql}
        ORDER BY id
        LIMIT %s;
    """.format(where_sql=where_sql)

    params.append(ps)
    rows = await db_portal.fetch_all(sql, tuple(params))

    # Attach plant_name (and country/region if available -> NULL-safe placeholders)
    # We avoid depending on extra columns; return None when unknown.
    if rows:
        pid_list = list({int(r["plant_id"]) for r in rows})
        ph = ", ".join(["%s"] * len(pid_list))
        plants = await db_portal.fetch_all(
            f"SELECT id, name FROM public.plants_plant WHERE id IN ({ph})",
            tuple(pid_list),
        )
        pmap = {str(p["id"]): p["name"] for p in plants}
    else:
        pmap = {}

    items: List[Dict[str, Any]] = []
    for r in rows:
        items.append(
            {
                "notification_no": r["notification_no"],
                "plant_id": r["plant_id"],
                "plant_name": pmap.get(r["plant_id"]),
                "country": None,
                "created_by": r["created_by"],
                "description": r["description"],
                "created_at": r["created_at"],
                "system_status": r["system_status"],
                "planner_group": r["planner_grb"],
                "asset": r["device_id"] or r["fl"] or None,
            }
        )

    next_cursor = _enc_cursor(int(rows[-1]["id"])) if rows and len(rows) == ps else None
    return {"items": items, "next_cursor": next_cursor}


async def _count_notifications_impl(**filters) -> int:
    where_sql, params = _where_notifications(**filters)
    row = await db_portal.fetch_one(
        f"SELECT COUNT(*)::int AS count FROM public.minspect_minspectdata {where_sql};",
        tuple(params) if params else None,
    )
    return int(row["count"]) if row else 0


async def _aggregate_notifications_impl(
    group_by: List[str],
    limit_per_group: Optional[int],
    **filters,
) -> Dict[str, Any]:
    """
    Server-side group-by. Allowed group_by keys mapped to SQL expressions.
    Supports 'plant' as alias for 'plant_id'.
    """
    allowed = {
        "plant_id": "plant_id",
        "plant": "plant_id",  # alias accepted
        "created_by": "created_by",
        "year": "year",
        "month": "month",
        "week": "week",
        "system_status": "system_status",
        "country": "NULL::text",
    }

    cols = []
    for key in group_by:
        if key not in allowed:
            return {"error": f"INVALID_ARGUMENT: unsupported group_by '{key}'"}
        cols.append(f"{allowed[key]} AS {key}")

    if not cols:
        return {"error": "INVALID_ARGUMENT: group_by is required"}

    where_sql, params = _where_notifications(**filters)

    sql = f"""
        SELECT {", ".join(cols)}, COUNT(*)::int AS count
        FROM public.minspect_minspectdata
        {where_sql}
        GROUP BY {", ".join([allowed[k] for k in group_by])}
        ORDER BY count DESC
    """
    if limit_per_group and len(group_by) == 1:
        sql += " LIMIT %s"
        params.append(int(limit_per_group))

    rows = await db_portal.fetch_all(sql, tuple(params) if params else None)

    # Attach plant names if grouped by plant_id or plant
    if any(k in ("plant_id", "plant") for k in group_by) and rows:
        pid_list = list({int(r["plant"]) for r in rows if r.get("plant")})
        if pid_list:
            ph = ", ".join(["%s"] * len(pid_list))
            plants = await db_portal.fetch_all(
                f"SELECT id, name FROM public.plants_plant WHERE id IN ({ph})",
                tuple(pid_list),
            )
            pmap = {int(p["id"]): p["name"] for p in plants}
            for r in rows:
                if r.get("plant"):
                    r["plant_name"] = pmap.get(int(r["plant"]))
    return {"groups": rows}


# ============================================================
# MCP tools registration
# ============================================================
def register(mcp: FastMCP):

    # ----------------------------------------
    # (1) Search & Metadata
    # ----------------------------------------
    @mcp.tool(description="Fuzzy search de plantas por nombre/alias; devuelve IDs canónicos.")
    async def minspect_search_plants(q: str, limit: Optional[int] = 20) -> dict:
        if not q or not q.strip():
            return {"error": "INVALID_ARGUMENT: q is required"}
        qn = utils._norm(q)

        # Traemos todas y hacemos matching normalizado; en SQL sólo exact/ILIKE si quieres:
        plants = await db_portal.fetch_all(
            """
            SELECT id::text AS plant_id, name AS plant_name, COALESCE(acs_code,'') AS acs_code
            FROM public.plants_plant
            WHERE deleted_at IS NULL
            ORDER BY id
            LIMIT 5000;
            """
        )
        items: List[Dict[str, Any]] = []
        for p in plants:
            if qn in utils._norm(p["plant_name"]) or qn in utils._norm(p["acs_code"]):
                items.append(
                    {
                        "plant_id": p["plant_id"],
                        "plant_name": p["plant_name"],
                        "aliases": [],  # si tienes tabla de alias, aquí puedes rellenar
                        "country": None,
                        "region": None,
                    }
                )
            if len(items) >= int(limit or 20):
                break
        return {"items": items}

    @mcp.tool(description="Lista global de plantas con paginación y flag de actividad Minspect.")
    async def minspect_list_plants_enhanced(
        active_only: Optional[bool] = False,
        country: Optional[str] = None,  # placeholder; devuelve null
        cursor: Optional[str] = None,
        page_size: Optional[int] = 200,
    ) -> dict:
        ps = utils._page_size(page_size)
        last_id = _dec_cursor(cursor)

        # Determinamos actividad Minspect por existencia de alguna notificación
        sql = """
            SELECT
                p.id::text AS plant_id,
                p.name     AS plant_name,
                EXISTS (
                    SELECT 1 FROM public.minspect_minspectdata m
                    WHERE m.plant_id = p.id
                    LIMIT 1
                ) AS minspect_active
            FROM public.plants_plant p
            WHERE p.deleted_at IS NULL AND p.id > %s
            ORDER BY p.id
            LIMIT %s;
        """
        rows = await db_portal.fetch_all(sql, (last_id, ps))
        items = []
        for r in rows:
            if active_only and not r["minspect_active"]:
                continue
            items.append(
                {
                    "plant_id": r["plant_id"],
                    "plant_name": r["plant_name"],
                    "country": None,
                    "region": None,
                    "minspect_active": bool(r["minspect_active"]),
                }
            )
        next_cursor = _enc_cursor(int(rows[-1]["plant_id"])) if rows and len(rows) == ps else None
        return {"items": items, "next_cursor": next_cursor}

    # ----------------------------------------
    # (2) Global Notifications
    # ----------------------------------------
    @mcp.tool(description="Global notifications list con filtros y paginación keyset. Admite plant_id/plant_name y filtro de asset (prefijo).")
    async def minspect_notifications_list(
        plant_ids: Optional[str] = None,  # CSV de ids
        plant_id: Optional[str] = None,
        plant_name: Optional[str] = None,
        asset: Optional[str] = None,
        limit: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        week: Optional[int] = None,
        created_by: Optional[str] = None,
        system_status: Optional[str] = None,
        priority: Optional[str] = None,
        planner_grb: Optional[str] = None,
        sort: Optional[str] = "-created_at",
        page_size: Optional[int] = 500,
        cursor: Optional[str] = None,
    ) -> dict:
        pid_list = None
        if plant_ids:
            try:
                pid_list = [int(x) for x in plant_ids.split(",") if x.strip()]
            except Exception:
                return {"error": "INVALID_ARGUMENT: plant_ids must be CSV of integers"}

        # Allow single plant_id or plant_name as an alternative
        resolved_pid = None
        if plant_id or plant_name:
            resolved_pid = await _resolve_plant_id(plant_id=plant_id, plant_name=plant_name)
            if not resolved_pid:
                return {"error": "Plant not found"}

        # limit overrides page_size if provided
        effective_page_size = int(limit) if isinstance(limit, int) and limit > 0 else page_size

        data = await _list_notifications_impl(
            plant_ids=pid_list,
            plant_id=resolved_pid,
            date_from=date_from,
            date_to=date_to,
            year=year,
            month=month,
            week=week,
            created_by=created_by,
            system_status=system_status,
            priority=priority,
            planner_grb=planner_grb,
            page_size=effective_page_size,
            cursor=cursor,
            sort=sort,
            asset=asset,
        )
        return data

    @mcp.tool(description="Global notifications count con los mismos filtros que /list.")
    async def minspect_notifications_count(
        plant_ids: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        week: Optional[int] = None,
        created_by: Optional[str] = None,
        system_status: Optional[str] = None,
        priority: Optional[str] = None,
        planner_grb: Optional[str] = None,
    ) -> dict:
        pid_list = None
        if plant_ids:
            try:
                pid_list = [int(x) for x in plant_ids.split(",") if x.strip()]
            except Exception:
                return {"error": "INVALID_ARGUMENT: plant_ids must be CSV of integers"}
        cnt = await _count_notifications_impl(
            plant_ids=pid_list,
            date_from=date_from,
            date_to=date_to,
            year=year,
            month=month,
            week=week,
            created_by=created_by,
            system_status=system_status,
            priority=priority,
            planner_grb=planner_grb,
        )
        return {"count": cnt}

    @mcp.tool(description="Aggregations (country/plant/created_by/month/status).")
    async def minspect_notifications_aggregate(
        group_by: str,  # CSV
        plant_ids: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        week: Optional[int] = None,
        created_by: Optional[str] = None,
        system_status: Optional[str] = None,
        priority: Optional[str] = None,
        planner_grb: Optional[str] = None,
        limit_per_group: Optional[int] = None,
        order_by: Optional[str] = "-count",  # placeholder
    ) -> dict:
        if not group_by:
            return {"error": "INVALID_ARGUMENT: group_by is required"}
        group_list = [g.strip() for g in group_by.split(",") if g.strip()]
        pid_list = None
        if plant_ids:
            try:
                pid_list = [int(x) for x in plant_ids.split(",") if x.strip()]
            except Exception:
                return {"error": "INVALID_ARGUMENT: plant_ids must be CSV of integers"}
        data = await _aggregate_notifications_impl(
            group_by=group_list,
            limit_per_group=limit_per_group,
            plant_ids=pid_list,
            date_from=date_from,
            date_to=date_to,
            year=year,
            month=month,
            week=week,
            created_by=created_by,
            system_status=system_status,
            priority=priority,
            planner_grb=planner_grb,
        )
        return data

    @mcp.tool(description="Leaderboard de creadores (scope=global|plant).")
    async def minspect_top_creators(
        scope: str = "global",
        plant_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        week: Optional[int] = None,
        limit: Optional[int] = 10,
    ) -> dict:
        if scope not in ("global", "plant"):
            return {"error": "INVALID_ARGUMENT: scope must be global|plant"}

        where: List[str] = []
        params: List[Any] = []
        if scope == "plant":
            pid = await _resolve_plant_id(plant_id, None) if plant_id else None
            if not pid:
                return {"error": "Plant not found (scope=plant requires plant_id)"}
            _add_filter(where, params, "plant_id = %s", pid)

        # time filters
        if date_from:
            _add_filter(where, params, "creation_date >= %s", date_from)
        if date_to:
            _add_filter(where, params, "creation_date <= %s", date_to)
        if year:
            _add_filter(where, params, "year = %s", int(year))
        if month:
            _add_filter(where, params, "month = %s", int(month))
        if week:
            _add_filter(where, params, "week = %s", int(week))

        where_sql = "WHERE " + " AND ".join(where) if where else ""
        sql = f"""
            SELECT created_by, COUNT(*)::int AS count
            FROM public.minspect_minspectdata
            {where_sql}
            GROUP BY created_by
            ORDER BY count DESC
            LIMIT %s;
        """
        params.append(int(limit or 10))
        rows = await db_portal.fetch_all(sql, tuple(params))
        out = []
        for r in rows:
            item = {"scope": scope, "created_by": r["created_by"], "count": r["count"]}
            if scope == "plant":
                item["plant_id"] = str(pid)
            out.append(item)
        return {"items": out}

    @mcp.tool(description="Últimas notificaciones por planta/asset (prefix match en asset).")
    async def minspect_notifications_latest(
        plant_id: Optional[str] = None,
        plant_name: Optional[str] = None,
        asset: Optional[str] = None,  # prefix match sobre device_id o fl
        limit: Optional[int] = 10,
    ) -> dict:
        pid = await _resolve_plant_id(plant_id, plant_name)
        if not pid:
            return {"error": "Plant not found"}

        where = ["plant_id = %s"]
        params: List[Any] = [pid]
        if asset:
            where.append("(device_id ILIKE %s OR fl ILIKE %s)")
            params.extend((f"{asset}%", f"{asset}%"))
        where_sql = " AND ".join(where)

        sql = f"""
            SELECT
                id::text AS id,
                notification_number AS notification_no,
                created_by, description,
                device_id, fl,
                system_status, priority,
                creation_date AS created_at
            FROM public.minspect_minspectdata
            WHERE {where_sql}
            ORDER BY creation_date DESC, id DESC
            LIMIT %s;
        """
        params.append(int(limit or 10))
        rows = await db_portal.fetch_all(sql, tuple(params))
        items = []
        for r in rows:
            items.append(
                {
                    "notification_no": r["notification_no"],
                    "created_by": r["created_by"],
                    "created_at": r["created_at"],
                    "asset": r["device_id"] or r["fl"] or None,
                    "system_status": r["system_status"],
                    "priority": r["priority"],
                    "description": r["description"],
                }
            )
        return {"items": items}

    # ----------------------------------------
    # (3) Per-plant rollups (top N por planta)
    # ----------------------------------------
    @mcp.tool(description="Top N creadores por planta (múltiples plantas a la vez).")
    async def minspect_plant_top_creators(
        plant_ids: str,  # CSV
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        week: Optional[int] = None,
        limit_per_plant: Optional[int] = 3,
    ) -> dict:
        try:
            pids = [int(x) for x in plant_ids.split(",") if x.strip()]
        except Exception:
            return {"error": "INVALID_ARGUMENT: plant_ids must be CSV of integers"}
        if not pids:
            return {"error": "INVALID_ARGUMENT: plant_ids is required and must not be empty"}

        # Traer nombres de planta
        ph = ", ".join(["%s"] * len(pids))
        pl = await db_portal.fetch_all(
            f"SELECT id::text AS plant_id, name AS plant_name FROM public.plants_plant WHERE id IN ({ph})",
            tuple(pids),
        )
        pmap = {int(p["plant_id"]): p["plant_name"] for p in pl}

        # Filtrado temporal común
        time_where: List[str] = []
        time_params: List[Any] = []
        if date_from:
            _add_filter(time_where, time_params, "creation_date >= %s", date_from)
        if date_to:
            _add_filter(time_where, time_params, "creation_date <= %s", date_to)
        if year:
            _add_filter(time_where, time_params, "year = %s", int(year))
        if month:
            _add_filter(time_where, time_params, "month = %s", int(month))
        if week:
            _add_filter(time_where, time_params, "week = %s", int(week))
        time_sql = " AND " + " AND ".join(time_where) if time_where else ""

        # Para cada planta: top N
        out_plants: List[Dict[str, Any]] = []
        for pid in pids:
            rows = await db_portal.fetch_all(
                f"""
                SELECT created_by, COUNT(*)::int AS count
                FROM public.minspect_minspectdata
                WHERE plant_id = %s {time_sql}
                GROUP BY created_by
                ORDER BY count DESC
                LIMIT %s;
                """,
                tuple([pid] + time_params + [int(limit_per_plant or 3)]),
            )
            out_plants.append(
                {
                    "plant_id": str(pid),
                    "plant_name": pmap.get(pid),
                    "country": None,
                    "top": rows,
                }
            )
        return {"plants": out_plants}

    # ----------------------------------------
    # (4) Alert ↔ Notification correlation
    # ----------------------------------------
    @mcp.tool(description="Correlaciona alertas predictivas con notificaciones Minspect (misma máquina/asset, ventana temporal).")
    async def minspect_correlate_alerts_notifications(
        plant_id: Optional[str] = None,
        plant_name: Optional[str] = None,
        machine_name: Optional[str] = None,
        asset: Optional[str] = None,  # usa device_id o fl
        window_before_hours: Optional[int] = 72,
        window_after_hours: Optional[int] = 168,
        state: Optional[str] = "Any",  # Open|Closed|Any
        limit: Optional[int] = 50,
    ) -> dict:
        pid = await _resolve_plant_id(plant_id, plant_name)
        if not pid:
            return {"error": "Plant not found"}
        s = (state or "Any").strip().lower()
        if s not in ("open", "closed", "any"):
            return {"error": "INVALID_ARGUMENT: state must be Open|Closed|Any"}

        # Filtro de alerta por planta y máquina opcional
        where_alert = ["a.plant_id = %s"]
        params: List[Any] = [pid]
        if machine_name:
            where_alert.append("a.name = %s")
            params.append(machine_name)
        if s != "any":
            where_alert.append("LOWER(m.state) = %s")
            params.append(s)
        where_alert_sql = " AND ".join(where_alert)

        # Traemos alertas recientes (limit) con info de asset y ts
        alerts = await db_portal.fetch_all(
            f"""
            SELECT
                m.id::text AS id, m.alert_id, m.timestamp,
                m.risk_score AS risk_score, m.risk_level AS risk_level,
                m.name AS alert_type,
                a.name AS machine_name,
                a.id::text AS machine_id,
                a.plant_id
            FROM public.mpredict_mpredictalert m
            JOIN public.mpredict_asset a ON a.id = m.asset_id
            WHERE {where_alert_sql}
            ORDER BY m.timestamp DESC, m.id DESC
            LIMIT %s;
            """,
            tuple(params + [int(limit or 50)]),
        )

        if not alerts:
            return {"pairs": []}

        # Para matching contra minspect: usamos device_id / fl. Si 'asset' llega, lo usamos como prefix.
        pairs: List[Dict[str, Any]] = []
        for al in alerts:
            ts = al["timestamp"]
            before = f"{window_before_hours or 72} hours"
            after = f"{window_after_hours or 168} hours"

            notif_where = [
                "plant_id = %s",
                "creation_date BETWEEN %s::timestamp - %s::interval AND %s::timestamp + %s::interval",
            ]
            nparams: List[Any] = [pid, ts, before, ts, after]

            if asset:
                notif_where.append("(device_id ILIKE %s OR fl ILIKE %s)")
                nparams.extend((f"{asset}%", f"{asset}%"))

            notif_sql = f"""
                SELECT
                    notification_number AS notification_no,
                    created_by,
                    creation_date AS created_at,
                    device_id, fl,
                    system_status
                FROM public.minspect_minspectdata
                WHERE {" AND ".join(notif_where)}
                ORDER BY creation_date DESC, id DESC
                LIMIT 100;
            """
            notifs = await db_portal.fetch_all(notif_sql, tuple(nparams))
            pairs.append(
                {
                    "alert": {
                        "alert_id": al["alert_id"],
                        "machine_name": al["machine_name"],
                        "risk_score": al["risk_score"],
                        "state": None if s == "any" else s.capitalize(),
                        "timestamp": ts,
                        "type": al["alert_type"],
                    },
                    "notifications": [
                        {
                            "notification_no": n["notification_no"],
                            "created_by": n["created_by"],
                            "created_at": n["created_at"],
                            "asset": n["device_id"] or n["fl"] or None,
                            "system_status": n["system_status"],
                        }
                        for n in notifs
                    ],
                }
            )
        return {"pairs": pairs}
