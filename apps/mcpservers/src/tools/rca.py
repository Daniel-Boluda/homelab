from datetime import datetime, date
from typing import Any, Dict, List, Optional

from src.deps import db_cause, utils
from fastmcp import FastMCP

# -------------------------- helpers --------------------------

def _like_param(text: Optional[str]) -> Optional[str]:
    return f"%{text.strip().lower()}%" if text else None


def _parse_list(value: Optional[str]) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value]
    parts = [p.strip() for p in str(value).replace(";", ",").split(",") if p.strip()]
    return parts or None


def register(mcp: FastMCP):

    # -------------------------- rca_list_problems --------------------------
    @mcp.tool(
        description=(
            "Lista problem definitions (RCA) con filtros sobre columnas reales "
            "(event_name, what, where, when, status, plant). "
            "Puede buscar también en Solutions/Actions relacionadas (texto), enlazando pd.id = s.\"rca_ID\" / a.\"rca_ID\"."
        )
    )
    async def rca_list_problems(
        text: Optional[str] = None,
        text_in: Optional[List[str]] = None,         # ["event_name","what","where"]
        include_related_text: Optional[bool] = True,  # buscar en solutions/actions
        status: Optional[List[str]] = None,
        plant: Optional[List[str]] = None,
        when_from: Optional[str] = None,             # "YYYY-MM-DD"
        when_to: Optional[str] = None,               # "YYYY-MM-DD"
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,                # keyset: id > cursor
        detail: Optional[str] = "summary",           # summary|full
    ) -> dict:
        """Devuelve PDs filtrados con paginación keyset (id > cursor)."""
        ps = utils._page_size(page_size)
        after_id = int(cursor) if cursor else 0

        like = _like_param(text)
        allowed_text_cols = {"event_name", "what", "where"}
        text_cols = [c for c in (text_in or ["event_name", "what", "where"]) if c in allowed_text_cols]

        status_list = _parse_list(status)
        plant_list = _parse_list(plant)

        where_parts: List[str] = ["pd.id > %s"]
        params: List[Any] = [after_id]

        # Texto en columnas del PD
        if like and text_cols:
            or_blocks = [f"LOWER(pd.{col}) ILIKE %s" for col in text_cols]
            params.extend([like] * len(or_blocks))
            where_parts.append("( " + " OR ".join(or_blocks) + " )")

        # Fechas
        if when_from:
            where_parts.append("pd.when >= %s")
            params.append(when_from)
        if when_to:
            where_parts.append("pd.when <= %s")
            params.append(when_to)

        # Status / Plant
        if status_list:
            where_parts.append("LOWER(pd.status) = ANY(%s)")
            params.append([s.lower() for s in status_list])
        if plant_list:
            where_parts.append("pd.plant = ANY(%s)")
            params.append(plant_list)

        # Coincidencias en soluciones/acciones relacionadas
        if like and (include_related_text is None or include_related_text):
            where_parts.append(
                "("
                " EXISTS (SELECT 1 FROM public.rca_data_solution s "
                "         WHERE s.\"rca_ID\" = pd.id AND LOWER(COALESCE(s.solution,'')) ILIKE %s)"
                " OR "
                " EXISTS (SELECT 1 FROM public.rca_data_action a "
                "         WHERE a.\"rca_ID\" = pd.id AND LOWER(COALESCE(a.action,'')) ILIKE %s)"
                ")"
            )
            params.extend([like, like])

        where_sql = " AND ".join(where_parts)

        select_pd = (
            "SELECT "
            "  pd.id::text AS id, pd.plant, pd.country, pd.region, "
            "  pd.event_name, pd.start_date, pd.end_date, pd.facilitator, pd.leader, "
            "  pd.status, pd.what, pd.where, pd.when "
            "FROM public.rca_data_problem_definition pd "
            f"WHERE {where_sql} "
            "ORDER BY pd.id "
            "LIMIT %s"
        )
        params.append(ps)

        rows = await db_cause.fetch_all(select_pd, tuple(params))

        # 'full' -> agregar colecciones y conteos relacionados (solutions/actions)
        if (detail or "summary").lower() == "full" and rows:
            pd_ids = [int(r["id"]) for r in rows]

            sol_rows = await db_cause.fetch_all(
                """
                SELECT
                    s.id::text AS id,
                    s."rca_ID"::text AS rca_id,
                    s.related_cause, s.solution, s.tpn, s.ease, s.impact,
                    s.validation_date, s.best_solution
                FROM public.rca_data_solution s
                WHERE s."rca_ID" = ANY(%s)
                ORDER BY s.id
                """,
                (pd_ids,),
            )
            act_rows = await db_cause.fetch_all(
                """
                SELECT
                    a.id::text AS id,
                    a."rca_ID"::text AS rca_id,
                    a.related_solution, a.action, a.creation_date,
                    a.responsible, a.due_date, a.completion_date,
                    a.plant, a.country, a.region
                FROM public.rca_data_action a
                WHERE a."rca_ID" = ANY(%s)
                ORDER BY a.id
                """,
                (pd_ids,),
            )

            sols_by: Dict[str, List[Dict[str, Any]]] = {}
            for s in sol_rows:
                sols_by.setdefault(s["rca_id"], []).append(s)

            acts_by: Dict[str, List[Dict[str, Any]]] = {}
            for a in act_rows:
                acts_by.setdefault(a["rca_id"], []).append(a)

            for r in rows:
                rid = r["id"]
                r["solutions"] = sols_by.get(rid, [])
                r["actions"] = acts_by.get(rid, [])
                r["solutionsCount"] = len(r["solutions"])
                r["actionsCount"] = len(r["actions"])

        next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
        return {"count": len(rows), "next_cursor": next_cursor, "problems": rows}

    # -------------------------- rca_get_detail --------------------------
    @mcp.tool(description="Devuelve el detalle de un RCA (por rca_id = id de PD), con sus Solutions y Actions.")
    async def rca_get_detail(
        rca_id: Optional[str] = None,   # id de PD
        problem_id: Optional[str] = None,  # alias del mismo parámetro (compat.)
    ) -> dict:
        rid = rca_id or problem_id
        if not rid:
            return {"error": "Debe indicar rca_id (id del Problem Definition)"}

        pd_row = await db_cause.fetch_one(
            """
            SELECT
                pd.id::text AS id, pd.plant, pd.country, pd.region,
                pd.event_name, pd.start_date, pd.end_date, pd.facilitator, pd.leader,
                pd.status, pd.what, pd.where, pd.when
            FROM public.rca_data_problem_definition pd
            WHERE pd.id = %s
            LIMIT 1
            """,
            (int(rid),),
        )
        if not pd_row:
            return {"error": "RCA no encontrado"}

        solutions = await db_cause.fetch_all(
            """
            SELECT
                s.id::text AS id,
                s."rca_ID"::text AS rca_id,
                s.related_cause, s.solution, s.tpn, s.ease, s.impact,
                s.validation_date, s.best_solution
            FROM public.rca_data_solution s
            WHERE s."rca_ID" = %s
            ORDER BY s.id
            """,
            (int(rid),),
        )
        actions = await db_cause.fetch_all(
            """
            SELECT
                a.id::text AS id,
                a."rca_ID"::text AS rca_id,
                a.related_solution, a.action, a.creation_date,
                a.responsible, a.due_date, a.completion_date,
                a.plant, a.country, a.region
            FROM public.rca_data_action a
            WHERE a."rca_ID" = %s
            ORDER BY a.id
            """,
            (int(rid),),
        )
        pd_row["solutions"] = solutions
        pd_row["actions"] = actions
        pd_row["solutionsCount"] = len(solutions)
        pd_row["actionsCount"] = len(actions)

        return {"rca": pd_row}

    # -------------------------- rca_group_problems --------------------------
    @mcp.tool(
        description=(
            "Agrupa/contabiliza Problem Definitions por: plant | date:day|week|month | status. "
            "Incluye, opcionalmente, la suma de Solutions y Actions relacionadas (por pd.id)."
        )
    )
    async def rca_group_problems(
        by: Optional[str] = "plant",            # plant|date|status
        date_grain: Optional[str] = "day",      # day|week|month (solo si by=date)
        when_from: Optional[str] = None,
        when_to: Optional[str] = None,
        status: Optional[List[str]] = None,
        plant: Optional[List[str]] = None,
        include_counts_related: Optional[bool] = True,
    ) -> dict:
        by = (by or "plant").lower()
        if by not in {"plant", "date", "status"}:
            return {"error": "'by' debe ser plant|date|status"}

        status_list = _parse_list(status)
        plant_list = _parse_list(plant)

        where_parts = ["TRUE"]
        params: List[Any] = []
        if when_from:
            where_parts.append("pd.when >= %s")
            params.append(when_from)
        if when_to:
            where_parts.append("pd.when <= %s")
            params.append(when_to)
        if status_list:
            where_parts.append("LOWER(pd.status) = ANY(%s)")
            params.append([s.lower() for s in status_list])
        if plant_list:
            where_parts.append("pd.plant = ANY(%s)")
            params.append(plant_list)

        where_sql = " AND ".join(where_parts)

        if by == "plant":
            group_select = "pd.plant AS key"
            group_by = "pd.plant"
        elif by == "status":
            group_select = "pd.status AS key"
            group_by = "pd.status"
        else:  # date
            grain = (date_grain or "day").lower()
            if grain not in {"day", "week", "month"}:
                grain = "day"
            group_select = f"date_trunc('{grain}', pd.when)::date AS key"
            group_by = f"date_trunc('{grain}', pd.when)"

        sql = f"""
            SELECT
                {group_select},
                COUNT(*)::int AS problems
            FROM public.rca_data_problem_definition pd
            WHERE {where_sql}
            GROUP BY {group_by}
            ORDER BY {group_by}
        """
        rows = await db_cause.fetch_all(sql, tuple(params))

        if include_counts_related and rows:
            # Mapear key -> lista de pd.id
            sql_keys = f"""
                SELECT {group_select} AS key, pd.id::int AS id
                FROM public.rca_data_problem_definition pd
                WHERE {where_sql}
            """
            key_rows = await db_cause.fetch_all(sql_keys, tuple(params))
            ids_by_key: Dict[Any, List[int]] = {}
            for kr in key_rows:
                ids_by_key.setdefault(kr["key"], []).append(kr["id"])

            all_ids = list({pid for lst in ids_by_key.values() for pid in lst})
            sols_by: Dict[int, int] = {}
            acts_by: Dict[int, int] = {}
            if all_ids:
                sol_counts = await db_cause.fetch_all(
                    """
                    SELECT s."rca_ID"::int AS pid, COUNT(*)::int AS c
                    FROM public.rca_data_solution s
                    WHERE s."rca_ID" = ANY(%s)
                    GROUP BY s."rca_ID"
                    """,
                    (all_ids,),
                )
                act_counts = await db_cause.fetch_all(
                    """
                    SELECT a."rca_ID"::int AS pid, COUNT(*)::int AS c
                    FROM public.rca_data_action a
                    WHERE a."rca_ID" = ANY(%s)
                    GROUP BY a."rca_ID"
                    """,
                    (all_ids,),
                )
                sols_by = {r["pid"]: r["c"] for r in sol_counts}
                acts_by = {r["pid"]: r["c"] for r in act_counts}

            for r in rows:
                pid_list = ids_by_key.get(r["key"], [])
                r["solutions"] = sum(sols_by.get(pid, 0) for pid in pid_list)
                r["actions"]  = sum(acts_by.get(pid, 0) for pid in pid_list)

        return {"by": by, "rows": rows}

    # -------------------------- rca_list_related --------------------------
    @mcp.tool(description="Lista Solutions o Actions relacionadas a uno o varios rca_id (ids de PD).")
    async def rca_list_related(
        kind: Optional[str] = "solutions",      # solutions|actions|both
        rca_id: Optional[str] = None,
        rca_ids: Optional[List[str]] = None,
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
        responsible: Optional[str] = None,      # solo para actions
        text: Optional[str] = None,             # busca en solution/action
        due_from: Optional[str] = None,         # actions.due_date >=
        due_to: Optional[str] = None,           # actions.due_date <=
        validation_from: Optional[str] = None,  # solutions.validation_date >=
        validation_to: Optional[str] = None,    # solutions.validation_date <=
    ) -> dict:
        kind = (kind or "solutions").lower()
        if kind not in {"solutions", "actions", "both"}:
            return {"error": "kind debe ser solutions|actions|both"}

        ps = utils._page_size(page_size)
        after_id = int(cursor) if cursor else 0
        like = _like_param(text)

        ids = rca_ids or ([] if rca_id is None else [rca_id])
        try:
            ids = [int(x) for x in ids]
        except Exception:
            return {"error": "rca_id/rca_ids deben ser enteros (ids de PD)"}

        out: Dict[str, Any] = {}

        # Solutions
        if kind in {"solutions", "both"}:
            parts = ["s.id > %s"]
            prms: List[Any] = [after_id]
            if ids:
                parts.append('s."rca_ID" = ANY(%s)')
                prms.append(ids)
            if like:
                parts.append("LOWER(COALESCE(s.solution,'')) ILIKE %s")
                prms.append(like)
            if validation_from:
                parts.append("s.validation_date >= %s")
                prms.append(validation_from)
            if validation_to:
                parts.append("s.validation_date <= %s")
                prms.append(validation_to)

            where_s = " AND ".join(parts)
            sql_s = (
                "SELECT "
                " s.id::text AS id, s.\"rca_ID\"::text AS rca_id, "
                " s.related_cause, s.solution, s.tpn, s.ease, s.impact, "
                " s.validation_date, s.best_solution "
                "FROM public.rca_data_solution s "
                f"WHERE {where_s} "
                "ORDER BY s.id LIMIT %s"
            )
            prms.append(ps)
            sols = await db_cause.fetch_all(sql_s, tuple(prms))
            out["solutions"] = {
                "count": len(sols),
                "next_cursor": (sols[-1]["id"] if sols and len(sols) == ps else None),
                "rows": sols,
            }

        # Actions
        if kind in {"actions", "both"}:
            parts = ["a.id > %s"]
            prms: List[Any] = [after_id]
            if ids:
                parts.append('a."rca_ID" = ANY(%s)')
                prms.append(ids)
            if like:
                parts.append("LOWER(COALESCE(a.action,'')) ILIKE %s")
                prms.append(like)
            if responsible:
                parts.append("LOWER(COALESCE(a.responsible,'')) = %s")
                prms.append(responsible.lower())
            if due_from:
                parts.append("a.due_date >= %s")
                prms.append(due_from)
            if due_to:
                parts.append("a.due_date <= %s")
                prms.append(due_to)

            where_a = " AND ".join(parts)
            sql_a = (
                "SELECT "
                " a.id::text AS id, a.\"rca_ID\"::text AS rca_id, "
                " a.related_solution, a.action, a.creation_date, "
                " a.responsible, a.due_date, a.completion_date, "
                " a.plant, a.country, a.region "
                "FROM public.rca_data_action a "
                f"WHERE {where_a} "
                "ORDER BY a.id LIMIT %s"
            )
            prms.append(ps)
            acts = await db_cause.fetch_all(sql_a, tuple(prms))
            out["actions"] = {
                "count": len(acts),
                "next_cursor": (acts[-1]["id"] if acts and len(acts) == ps else None),
                "rows": acts,
            }

        return out

    # -------------------------- rca_simple_counters --------------------------
    @mcp.tool(description="Contadores rápidos: total PDs, Solutions totales y Actions totales (con filtros básicos sobre PD).")
    async def rca_simple_counters(
        when_from: Optional[str] = None,
        when_to: Optional[str] = None,
        plant: Optional[List[str]] = None,
        status: Optional[List[str]] = None,
    ) -> dict:
        status_list = _parse_list(status)
        plant_list = _parse_list(plant)

        where_parts = ["TRUE"]
        params: List[Any] = []
        if when_from:
            where_parts.append("pd.when >= %s")
            params.append(when_from)
        if when_to:
            where_parts.append("pd.when <= %s")
            params.append(when_to)
        if status_list:
            where_parts.append("LOWER(pd.status) = ANY(%s)")
            params.append([s.lower() for s in status_list])
        if plant_list:
            where_parts.append("pd.plant = ANY(%s)")
            params.append(plant_list)

        where_sql = " AND ".join(where_parts)

        totals = await db_cause.fetch_one(
            f"SELECT COUNT(*)::int AS problems FROM public.rca_data_problem_definition pd WHERE {where_sql}",
            tuple(params),
        )

        # Obtener ids de PD que cumplen filtros
        pd_ids_rows = await db_cause.fetch_all(
            f"SELECT pd.id::int AS id FROM public.rca_data_problem_definition pd WHERE {where_sql}",
            tuple(params),
        )
        pd_ids = [r["id"] for r in pd_ids_rows]

        sol_c = act_c = 0
        if pd_ids:
            rs = await db_cause.fetch_one(
                "SELECT COALESCE(SUM(c),0)::int AS c FROM (SELECT COUNT(*)::int AS c FROM public.rca_data_solution WHERE \"rca_ID\" = ANY(%s)) t",
                (pd_ids,),
            )
            ra = await db_cause.fetch_one(
                "SELECT COALESCE(SUM(c),0)::int AS c FROM (SELECT COUNT(*)::int AS c FROM public.rca_data_action   WHERE \"rca_ID\" = ANY(%s)) t",
                (pd_ids,),
            )
            sol_c = rs["c"] if rs else 0
            act_c = ra["c"] if ra else 0

        return {
            "problems": totals.get("problems", 0) if totals else 0,
            "solutions": sol_c,
            "actions": act_c,
        }
