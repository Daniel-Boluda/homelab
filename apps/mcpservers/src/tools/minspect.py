from typing import Optional, List, Any, Dict
from ..deps import db, utils
from fastmcp import FastMCP

def _add_filter(where: List[str], params: List[Any], clause: str, *values: Any):
    where.append(clause)
    params.extend(values)

def register(mcp: FastMCP):
    # ---------------------------
    # WORK ORDERS (minspect_workorder)
    # ---------------------------

    @mcp.tool(description="Lista work orders (global) con filtros y paginación keyset.")
    async def minspect_list_workorders(
        plant_id: Optional[str] = None,
        work_center: Optional[str] = None,
        user_status: Optional[str] = None,
        date_from: Optional[str] = None,   # ISO 'YYYY-MM-DD'
        date_to: Optional[str] = None,     # ISO 'YYYY-MM-DD'
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        ps = utils._page_size(page_size)
        after_id = int(cursor) if cursor else 0

        where, params = ["id > %s"], [after_id]

        if plant_id:
            _add_filter(where, params, "plant_id = %s", int(plant_id))
        if work_center:
            _add_filter(where, params, "work_center = %s", work_center)
        if user_status:
            # estado tal cual (ej. '1crt','5com'); ajusta a ILIKE si necesitas más laxo
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

    @mcp.tool(description="Lista work orders de una planta (detail=summary|full-n/a).")
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
        # Resolver planta (igual que en mpredict.py)
        pid = None
        if plant_id:
            pid = int(plant_id)
        elif plant_name:
            p = await db.fetch_one(
                "SELECT id FROM public.plants_plant WHERE deleted_at IS NULL AND name = %s",
                (plant_name,),
            )
            if not p:
                # fallback normalizado
                plants = await db.fetch_all(
                    "SELECT id, name FROM public.plants_plant WHERE deleted_at IS NULL"
                )
                target = utils._norm(plant_name)
                match = next((pp for pp in plants if utils._norm(pp["name"]) == target), None)
                pid = int(match["id"]) if match else None
            else:
                pid = int(p["id"])
        if not pid:
            return {"error": "Plant not found"}

        return await list_workorders(
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

    # ---------------------------
    # NOTIFICATIONS / MINSECT DATA (minspect_minspectdata)
    # ---------------------------

    @mcp.tool(description="Lista notificaciones (minspectdata) con filtros y paginación keyset.")
    async def minspect_list_notifications(
        plant_id: Optional[str] = None,
        priority: Optional[str] = None,         # 'P','U', etc.
        system_status: Optional[str] = None,     # exacto o ILIKE parcial
        year: Optional[int] = None,
        month: Optional[int] = None,
        week: Optional[int] = None,
        created_by: Optional[str] = None,
        planner_grb: Optional[str] = None,
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        ps = utils._page_size(page_size)
        after_id = int(cursor) if cursor else 0

        where, params = ["id > %s"], [after_id]
        if plant_id:     _add_filter(where, params, "plant_id = %s", int(plant_id))
        if priority:     _add_filter(where, params, "priority = %s", priority)
        if year:         _add_filter(where, params, "year = %s", int(year))
        if month:        _add_filter(where, params, "month = %s", int(month))
        if week:         _add_filter(where, params, "week = %s", int(week))
        if created_by:   _add_filter(where, params, "created_by = %s", created_by)
        if planner_grb:  _add_filter(where, params, "planner_grb = %s", planner_grb)
        if system_status:
            # La columna suele contener CSV del tipo 'ORAS,NOPR' (según tu captura),
            # por lo que aplicamos búsqueda parcial.
            _add_filter(where, params, "system_status ILIKE %s", f"%{system_status}%")

        where_sql = " AND ".join(where)
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
            WHERE {where_sql}
            ORDER BY id
            LIMIT %s;
        """
        params.append(ps)
        rows = await db.fetch_all(sql, tuple(params))
        next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None

        return {"count": len(rows), "next_cursor": next_cursor, "notifications": rows}

    @mcp.tool(description="Obtiene una notificación por su número (notification_number).")
    async def minspect_get_notification_by_number(notification_number: str) -> dict:
        row = await db.fetch_one(
            """
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
            WHERE notification_number = %s
            LIMIT 1;
            """,
            (notification_number,),
        )
        return {"notification": row} if row else {"error": "Notification not found"}

    @mcp.tool(description="Lista notificaciones de una planta con filtros comunes.")
    async def minspect_list_notifications_in_plant(
        plant_id: Optional[str] = None,
        plant_name: Optional[str] = None,
        priority: Optional[str] = None,
        system_status: Optional[str] = None,
        year: Optional[int] = None,
        month: Optional[int] = None,
        week: Optional[int] = None,
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        # Resolver plant_id como en mpredict
        pid = None
        if plant_id:
            pid = int(plant_id)
        elif plant_name:
            p = await db.fetch_one(
                "SELECT id FROM public.plants_plant WHERE deleted_at IS NULL AND name = %s",
                (plant_name,),
            )
            if not p:
                plants = await db.fetch_all(
                    "SELECT id, name FROM public.plants_plant WHERE deleted_at IS NULL"
                )
                target = utils._norm(plant_name)
                match = next((pp for pp in plants if utils._norm(pp["name"]) == target), None)
                pid = int(match["id"]) if match else None
            else:
                pid = int(p["id"])
        if not pid:
            return {"error": "Plant not found"}

        return await list_notifications(
            plant_id=str(pid),
            priority=priority,
            system_status=system_status,
            year=year,
            month=month,
            week=week,
            page_size=page_size,
            cursor=cursor,
        )

    @mcp.tool(description="Resumen de notificaciones por prioridad en una planta.")
    async def minspect_notifications_by_priority_in_plant(plant_id: str) -> dict:
        rows = await db.fetch_all(
            """
            SELECT priority, COUNT(*)::int AS count
            FROM public.minspect_minspectdata
            WHERE plant_id = %s
            GROUP BY priority
            ORDER BY priority;
            """,
            (int(plant_id),),
        )
        return {"plant_id": str(plant_id), "buckets": rows}

    @mcp.tool(description="Resumen de notificaciones por system_status (match parcial) en una planta.")
    async def minspect_notifications_by_system_status_in_plant(plant_id: str) -> dict:
        # Si system_status contiene CSV, contamos por token principal (split por coma)
        rows = await db.fetch_all(
            """
            WITH tokens AS (
              SELECT unnest(string_to_array(system_status, ',')) AS token
              FROM public.minspect_minspectdata
              WHERE plant_id = %s
            )
            SELECT trim(token) AS system_status, COUNT(*)::int AS count
            FROM tokens
            GROUP BY trim(token)
            ORDER BY trim(token);
            """,
            (int(plant_id),),
        )
        return {"plant_id": str(plant_id), "buckets": rows}

    @mcp.tool(description="Resumen semanal (year,week) de notificaciones para una planta.")
    async def minspect_notifications_weekly_trend_in_plant(plant_id: str, year: Optional[int] = None) -> dict:
        if year:
            rows = await db.fetch_all(
                """
                SELECT year, week, COUNT(*)::int AS count
                FROM public.minspect_minspectdata
                WHERE plant_id = %s AND year = %s
                GROUP BY year, week
                ORDER BY year, week;
                """,
                (int(plant_id), int(year)),
            )
        else:
            rows = await db.fetch_all(
                """
                SELECT year, week, COUNT(*)::int AS count
                FROM public.minspect_minspectdata
                WHERE plant_id = %s
                GROUP BY year, week
                ORDER BY year, week;
                """,
                (int(plant_id),),
            )
        return {"plant_id": str(plant_id), "trend": rows}