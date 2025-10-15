from typing import Optional, List, Any, Dict
from fastmcp import FastMCP
from src.deps import db, utils


# ----------------------------
# Utilidades internas
# ----------------------------
def _add_filter(where: List[str], params: List[Any], clause: str, *values: Any):
    where.append(clause)
    params.extend(values)


async def _resolve_plant_id(
    plant_id: Optional[str],
    plant_name: Optional[str],
) -> Optional[int]:
    """Resuelve el id de planta por id directo o por nombre (normalizado)."""
    if plant_id:
        return int(plant_id)

    if plant_name:
        # Intento exacto por nombre
        p = await db.fetch_one(
            "SELECT id FROM public.plants_plant WHERE deleted_at IS NULL AND name = %s",
            (plant_name,),
        )
        if p:
            return int(p["id"])

        # Normalizado
        plants = await db.fetch_all(
            "SELECT id, name FROM public.plants_plant WHERE deleted_at IS NULL"
        )
        target = utils._norm(plant_name)
        match = next((pp for pp in plants if utils._norm(pp["name"]) == target), None)
        if match:
            return int(match["id"])

    return None


# ============================
#   HELPERS: WORKORDERS
# ============================
async def _list_workorders_impl(
    plant_id: Optional[str] = None,
    work_center: Optional[str] = None,
    user_status: Optional[str] = None,
    date_from: Optional[str] = None,   # ISO 'YYYY-MM-DD'
    date_to: Optional[str] = None,     # ISO 'YYYY-MM-DD'
    page_size: Optional[int] = None,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    ps = utils._page_size(page_size)
    after_id = int(cursor) if cursor else 0

    where: List[str] = ["id > %s"]
    params: List[Any] = [after_id]

    if plant_id:
        _add_filter(where, params, "plant_id = %s", int(plant_id))
    if work_center:
        _add_filter(where, params, "work_center = %s", work_center)
    if user_status:
        _add_filter(where, params, "user_status = %s", user_status)
    if date_from:
        _add_filter(where, params, "start_date >= %s", date_from)
    if date_to:
        _add_filter(where, params, "start_date <= %s", date_to)

    where_sql = " AND ".join(where)
    sql = f"""
        SELECT
            id::text AS id,
            order_number,
            description,
            work_center,
            user_status,
            start_date,
            plant_id::text AS plant_id,
            data_hash
        FROM public.minspect_workorder
        WHERE {where_sql}
        ORDER BY id
        LIMIT %s;
    """
    params.append(ps)
    rows = await db.fetch_all(sql, tuple(params))
    next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None

    return {"count": len(rows), "next_cursor": next_cursor, "workorders": rows}


# ============================
#   HELPERS: NOTIFICATIONS
# ============================
def _where_notifications(
    plant_id: Optional[str] = None,
    priority: Optional[str] = None,
    system_status: Optional[str] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
    week: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    created_by: Optional[str] = None,
    planner_grb: Optional[str] = None,
):
    """Construye WHERE + params para minspect_minspectdata."""
    where: List[str] = []
    params: List[Any] = []

    if plant_id:     _add_filter(where, params, "plant_id = %s", int(plant_id))
    if priority:     _add_filter(where, params, "priority = %s", priority)
    if year:         _add_filter(where, params, "year = %s", int(year))
    if month:        _add_filter(where, params, "month = %s", int(month))
    if week:         _add_filter(where, params, "week = %s", int(week))
    if date_from:    _add_filter(where, params, "creation_date >= %s", date_from)
    if date_to:      _add_filter(where, params, "creation_date <= %s", date_to)
    if created_by:   _add_filter(where, params, "created_by = %s", created_by)
    if planner_grb:  _add_filter(where, params, "planner_grb = %s", planner_grb)
    if system_status:
        _add_filter(where, params, "system_status ILIKE %s", f"%{system_status}%")

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    return where_sql, tuple(params)


async def _list_notifications_impl(
    plant_id: Optional[str] = None,
    priority: Optional[str] = None,
    system_status: Optional[str] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
    week: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    created_by: Optional[str] = None,
    planner_grb: Optional[str] = None,
    page_size: Optional[int] = None,
    cursor: Optional[str] = None,
) -> Dict[str, Any]:
    ps = utils._page_size(page_size)
    after_id = int(cursor) if cursor else 0

    where_sql, params = _where_notifications(
        plant_id=plant_id, priority=priority, system_status=system_status,
        year=year, month=month, week=week,
        date_from=date_from, date_to=date_to,
        created_by=created_by, planner_grb=planner_grb,
    )

    # keyset por id
    if where_sql:
        where_sql = f"{where_sql} AND id > %s"
    else:
        where_sql = "WHERE id > %s"
    params = list(params) + [after_id]

    sql = f"""
        SELECT
            id::text AS id,
            notification_number,
            fl,
            created_by,
            priority,
            description,
            planner_grb,
            device_id,
            system_status,
            year, month, week,
            creation_date,
            plant_id::text AS plant_id,
            data_hash
        FROM public.minspect_minspectdata
        {where_sql}
        ORDER BY id
        LIMIT %s;
    """
    params.append(ps)
    rows = await db.fetch_all(sql, tuple(params))
    next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
    return {"count": len(rows), "next_cursor": next_cursor, "notifications": rows}


async def _count_notifications_impl(
    plant_id: Optional[str] = None,
    priority: Optional[str] = None,
    system_status: Optional[str] = None,
    year: Optional[int] = None,
    month: Optional[int] = None,
    week: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    created_by: Optional[str] = None,
    planner_grb: Optional[str] = None,
) -> int:
    where_sql, params = _where_notifications(
        plant_id=plant_id, priority=priority, system_status=system_status,
        year=year, month=month, week=week,
        date_from=date_from, date_to=date_to,
        created_by=created_by, planner_grb=planner_grb,
    )
    row = await db.fetch_one(
        f"SELECT COUNT(*)::int AS count FROM public.minspect_minspectdata {where_sql};",
        params if params else None,
    )
    return int(row["count"]) if row else 0


# ============================
#   REGISTRO DE TOOLS
# ============================
def register(mcp: FastMCP):

    # -------- WORK ORDERS --------
    @mcp.tool(description="Lista work orders (global) con filtros y paginación keyset.")
    async def minspect_list_workorders(
        plant_id: Optional[str] = None,
        work_center: Optional[str] = None,
        user_status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        return await _list_workorders_impl(
            plant_id=plant_id,
            work_center=work_center,
            user_status=user_status,
            date_from=date_from,
            date_to=date_to,
            page_size=page_size,
            cursor=cursor,
        )

    @mcp.tool(description="Obtiene un work order por su número (order_number).")
    async def minspect_get_workorder_by_number(order_number: str) -> dict:
        row = await db.fetch_one(
            """
            SELECT
                id::text AS id,
                order_number,
                description,
                work_center,
                user_status,
                start_date,
                plant_id::text AS plant_id,
                data_hash
            FROM public.minspect_workorder
            WHERE order_number = %s
            LIMIT 1;
            """,
            (order_number,),
        )
        return {"workorder": row} if row else {"error": "Work order not found"}

    @mcp.tool(description="Lista work orders de una planta.")
    async def minspect_list_workorders_in_plant(
        plant_id: Optional[str] = None,
        plant_name: Optional[str] = None,
        work_center: Optional[str] = None,
        user_status: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        pid = await _resolve_plant_id(plant_id, plant_name)
        if not pid:
            return {"error": "Plant not found"}
        return await _list_workorders_impl(
            plant_id=str(pid),
            work_center=work_center,
            user_status=user_status,
            date_from=date_from,
            date_to=date_to,
            page_size=page_size,
            cursor=cursor,
        )

    @mcp.tool(description="Resumen de work orders por estado de usuario (user_status) en una planta.")
    async def minspect_workorders_status_summary_in_plant(plant_id: str) -> dict:
        rows = await db.fetch_all(
            """
            SELECT user_status, COUNT(*)::int AS count
            FROM public.minspect_workorder
            WHERE plant_id = %s
            GROUP BY user_status
            ORDER BY user_status;
            """,
            (int(plant_id),),
        )
        return {"plant_id": str(plant_id), "buckets": rows}

    @mcp.tool(description="Resumen de work orders por centro de trabajo (work_center) en una planta.")
    async def minspect_workorders_by_workcenter_in_plant(plant_id: str) -> dict:
        rows = await db.fetch_all(
            """
            SELECT work_center, COUNT(*)::int AS count
            FROM public.minspect_workorder
            WHERE plant_id = %s
            GROUP BY work_center
            ORDER BY work_center;
            """,
            (int(plant_id),),
        )
        return {"plant_id": str(plant_id), "buckets": rows}

    # -------- NOTIFICATIONS --------
    @mcp.tool(description="Lista notificaciones (minspectdata) con filtros y paginación keyset.")
    async def minspect_list_notifications(
        plant_id: Optional[str] = None,
        priority: Optional[str] = None,
        system_status: Optional[str] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        week: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        created_by: Optional[str] = None,
        planner_grb: Optional[str] = None,
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        return await _list_notifications_impl(
            plant_id=plant_id, priority=priority, system_status=system_status,
            year=year, month=month, week=week,
            date_from=date_from, date_to=date_to,
            created_by=created_by, planner_grb=planner_grb,
            page_size=page_size, cursor=cursor,
        )

    @mcp.tool(description="Lista notificaciones de una planta con filtros comunes.")
    async def minspect_list_notifications_in_plant(
        plant_id: Optional[str] = None,
        plant_name: Optional[str] = None,
        priority: Optional[str] = None,
        system_status: Optional[str] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        week: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        created_by: Optional[str] = None,
        planner_grb: Optional[str] = None,
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        pid = await _resolve_plant_id(plant_id, plant_name)
        if not pid:
            return {"error": "Plant not found"}
        return await _list_notifications_impl(
            plant_id=str(pid), priority=priority, system_status=system_status,
            year=year, month=month, week=week,
            date_from=date_from, date_to=date_to,
            created_by=created_by, planner_grb=planner_grb,
            page_size=page_size, cursor=cursor,
        )

    @mcp.tool(description="Cuenta notificaciones (global) con filtros: mes, semana, rango fechas, planner_grb, created_by, etc.")
    async def minspect_count_notifications(
        plant_id: Optional[str] = None,
        priority: Optional[str] = None,
        system_status: Optional[str] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        week: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        created_by: Optional[str] = None,
        planner_grb: Optional[str] = None,
    ) -> dict:
        cnt = await _count_notifications_impl(
            plant_id=plant_id, priority=priority, system_status=system_status,
            year=year, month=month, week=week,
            date_from=date_from, date_to=date_to,
            created_by=created_by, planner_grb=planner_grb,
        )
        return {"count": cnt}

    @mcp.tool(description="Cuenta notificaciones en una planta con filtros (mes, semana, entre fechas, planner_grb, created_by, etc.).")
    async def minspect_count_notifications_in_plant(
        plant_id: Optional[str] = None,
        plant_name: Optional[str] = None,
        priority: Optional[str] = None,
        system_status: Optional[str] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        week: Optional[int] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        created_by: Optional[str] = None,
        planner_grb: Optional[str] = None,
    ) -> dict:
        pid = await _resolve_plant_id(plant_id, plant_name)
        if not pid:
            return {"error": "Plant not found"}
        cnt = await _count_notifications_impl(
            plant_id=str(pid), priority=priority, system_status=system_status,
            year=year, month=month, week=week,
            date_from=date_from, date_to=date_to,
            created_by=created_by, planner_grb=planner_grb,
        )
        return {"plant_id": str(pid), "count": cnt}
