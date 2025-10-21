from datetime import datetime, date
from typing import Any, Dict, List, Optional

from src.deps import db_cause, utils
from fastmcp import FastMCP

"""
MCP tools for RCA data sets.

This module exposes tools to:
  * List/filtrar problem definitions (texto en event_name/what/where/causa; fecha en when; status; participant_code; plant).
  * Incluir coincidencias por soluciones / acciones relacionadas del mismo rca_id.
  * Obtener detalle de un RCA con sus soluciones/acciones.
  * Agrupar y contar por planta, fecha (day/week/month), participantes o responsables.

Keyset pagination compatible con el patrón usado en mpredict.py: page_size + cursor (id > cursor).
"""


def _like_param(text: Optional[str]) -> Optional[str]:
    return f"%{text.strip().lower()}%" if text else None


def _parse_list(value: Optional[str]) -> Optional[List[str]]:
    if value is None:
        return None
    if isinstance(value, list):
        return [str(v) for v in value]
    # comma/semicolon separated
    parts = [p.strip() for p in str(value).replace(";", ",").split(",") if p.strip()]
    return parts or None


def register(mcp: FastMCP):

    # -------------------------- list_rca_problems --------------------------
    @mcp.tool(
        description=(
            "Lista problem definitions (RCA) con filtros. "
            "Puede filtrar por texto (event_name, what, where, related_cause), "
            "por fecha en 'when' (desde/hasta), por status, participant_code, plant; "
            "y también por coincidencias en soluciones/acciones relacionadas."
        )
    )
    async def list_rca_problems(
        text: Optional[str] = None,
        text_in: Optional[List[str]] = None,  # ["event_name","what","where","related_cause"]
        include_related_text: Optional[bool] = True,  # buscar en solutions/actions
        status: Optional[List[str]] = None,  # lista de estatus a incluir
        participant_code: Optional[List[str]] = None,
        plant: Optional[List[str]] = None,
        when_from: Optional[str] = None,  # "YYYY-MM-DD"
        when_to: Optional[str] = None,    # "YYYY-MM-DD"
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
        detail: Optional[str] = "summary",  # summary|full
    ) -> dict:
        """Devuelve RCAs filtrados, con paginación keyset (id > cursor)."""
        ps = utils._page_size(page_size)
        after_id = int(cursor) if cursor else 0

        # Normalizamos filtros
        like = _like_param(text)
        text_cols = text_in or ["event_name", "what", "where", "related_cause"]
        status_list = _parse_list(status)
        participant_list = _parse_list(participant_code)
        plant_list = _parse_list(plant)

        where_parts: List[str] = ["pd.id > %s"]
        params: List[Any] = [after_id]

        # Texto en columnas del PD
        if like and text_cols:
            or_blocks = []
            for col in text_cols:
                # defensive: avoid SQL injection in identifiers; whitelist expected columns
                if col not in {"event_name", "what", "where", "related_cause"}:
                    continue
                or_blocks.append(f"LOWER(pd.{col}) ILIKE %s")
                params.append(like)
            if or_blocks:
                where_parts.append("( " + " OR ".join(or_blocks) + " )")

        # Fechas
        if when_from:
            where_parts.append("pd.when >= %s")
            params.append(when_from)
        if when_to:
            where_parts.append("pd.when <= %s")
            params.append(when_to)

        # Status / participant / plant
        if status_list:
            where_parts.append("LOWER(pd.status) = ANY(%s)")
            params.append([s.lower() for s in status_list])
        if participant_list:
            where_parts.append("pd.participant_code = ANY(%s)")
            params.append(participant_list)
        if plant_list:
            where_parts.append("pd.plant = ANY(%s)")
            params.append(plant_list)

        # Coincidencias en soluciones/acciones del mismo rca_id
        related_block = ""
        if like and (include_related_text is None or include_related_text):
            related_block = (
                " OR EXISTS (SELECT 1 FROM public.rca_data_solution s "
                "            WHERE s.rca_id = pd.rca_id AND LOWER(COALESCE(s.solution,'')) ILIKE %s) "
                " OR EXISTS (SELECT 1 FROM public.rca_data_action a "
                "            WHERE a.rca_id = pd.rca_id AND LOWER(COALESCE(a.action,'')) ILIKE %s) "
            )
            params.extend([like, like])
            # ensure at least one text predicate exists
            if like and not any("ILIKE" in w for w in where_parts):
                where_parts.append("(FALSE" + related_block + ")")
            else:
                # append as an extra OR group to previous text group
                where_parts.append("(TRUE" + related_block + ")")

        where_sql = " AND ".join(where_parts) if where_parts else "TRUE"

        base_select = (
            "SELECT pd.id::text AS id, pd.rca_id::text AS rca_id, pd.event_name, pd.what, pd.where, pd.when, "
            "       pd.status, pd.participant_code, pd.plant, pd.responsible, pd.related_cause "
            "FROM public.rca_data_problem_definition pd "
            f"WHERE {where_sql} "
            "ORDER BY pd.id LIMIT %s"
        )
        params.append(ps)

        rows = await db_cause.fetch_all(base_select, tuple(params))

        # Si se pide full, colgamos agregados de counts y primeras soluciones/acciones
        if (detail or "summary").lower() == "full" and rows:
            # Traer soluciones/acciones en lote por rca_id
            rca_ids = [r["rca_id"] for r in rows]
            sol_rows = await db_cause.fetch_all(
                """
                SELECT s.id::text AS id, s.rca_id::text AS rca_id, s.solution, s.owner, s.status, s.due_date
                FROM public.rca_data_solution s
                WHERE s.rca_id = ANY(%s)
                ORDER BY s.id
                """,
                (rca_ids,),
            )
            act_rows = await db_cause.fetch_all(
                """
                SELECT a.id::text AS id, a.rca_id::text AS rca_id, a.action, a.owner, a.status, a.due_date
                FROM public.rca_data_action a
                WHERE a.rca_id = ANY(%s)
                ORDER BY a.id
                """,
                (rca_ids,),
            )
            # index
            sols_by = {}
            for s in sol_rows:
                sols_by.setdefault(s["rca_id"], []).append(s)
            acts_by = {}
            for a in act_rows:
                acts_by.setdefault(a["rca_id"], []).append(a)

            for r in rows:
                r["solutions"] = sols_by.get(r["rca_id"], [])
                r["actions"] = acts_by.get(r["rca_id"], [])
                r["solutionsCount"] = len(r["solutions"]) if r.get("solutions") else 0
                r["actionsCount"] = len(r["actions"]) if r.get("actions") else 0

        next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
        return {"count": len(rows), "next_cursor": next_cursor, "problems": rows}

    # -------------------------- get_rca_detail --------------------------
    @mcp.tool(description="Devuelve el detalle de un RCA (por rca_id o id del problem definition), con soluciones y acciones.")
    async def get_rca_detail(
        rca_id: Optional[str] = None,
        problem_id: Optional[str] = None,
    ) -> dict:
        if not rca_id and not problem_id:
            return {"error": "Debe indicar rca_id o problem_id"}

        if rca_id:
            pd_row = await db_cause.fetch_one(
                """
                SELECT pd.id::text AS id, pd.rca_id::text AS rca_id, pd.event_name, pd.what, pd.where, pd.when,
                       pd.status, pd.participant_code, pd.plant, pd.responsible, pd.related_cause
                FROM public.rca_data_problem_definition pd
                WHERE pd.rca_id = %s
                LIMIT 1
                """,
                (rca_id,),
            )
        else:
            pd_row = await db_cause.fetch_one(
                """
                SELECT pd.id::text AS id, pd.rca_id::text AS rca_id, pd.event_name, pd.what, pd.where, pd.when,
                       pd.status, pd.participant_code, pd.plant, pd.responsible, pd.related_cause
                FROM public.rca_data_problem_definition pd
                WHERE pd.id = %s
                LIMIT 1
                """,
                (int(problem_id),),
            )
        if not pd_row:
            return {"error": "RCA no encontrado"}

        rid = pd_row["rca_id"]

        solutions = await db_cause.fetch_all(
            """
            SELECT s.id::text AS id, s.rca_id::text AS rca_id, s.solution, s.owner, s.status, s.due_date
            FROM public.rca_data_solution s
            WHERE s.rca_id = %s
            ORDER BY s.id
            """,
            (rid,),
        )
        actions = await db_cause.fetch_all(
            """
            SELECT a.id::text AS id, a.rca_id::text AS rca_id, a.action, a.owner, a.status, a.due_date
            FROM public.rca_data_action a
            WHERE a.rca_id = %s
            ORDER BY a.id
            """,
            (rid,),
        )
        pd_row["solutions"] = solutions
        pd_row["actions"] = actions
        pd_row["solutionsCount"] = len(solutions)
        pd_row["actionsCount"] = len(actions)

        return {"rca": pd_row}

    # -------------------------- group_rca_problems --------------------------
    @mcp.tool(
        description=(
            "Agrupa/contabiliza problem definitions por una dimensión: "
            "plant | date:day|week|month | participant | responsible."
        )
    )
    async def group_rca_problems(
        by: Optional[str] = "plant",
        date_grain: Optional[str] = "day",  # day|week|month
        when_from: Optional[str] = None,
        when_to: Optional[str] = None,
        status: Optional[List[str]] = None,
        plant: Optional[List[str]] = None,
        participant_code: Optional[List[str]] = None,
        include_counts_related: Optional[bool] = True,
    ) -> dict:
        by = (by or "plant").lower()
        if by not in {"plant", "date", "participant", "responsible"}:
            return {"error": "'by' debe ser plant|date|participant|responsible"}

        status_list = _parse_list(status)
        plant_list = _parse_list(plant)
        participant_list = _parse_list(participant_code)

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
        if participant_list:
            where_parts.append("pd.participant_code = ANY(%s)")
            params.append(participant_list)

        where_sql = " AND ".join(where_parts)

        if by == "plant":
            group_select = "pd.plant AS key"
            group_by = "pd.plant"
        elif by == "participant":
            group_select = "pd.participant_code AS key"
            group_by = "pd.participant_code"
        elif by == "responsible":
            group_select = "pd.responsible AS key"
            group_by = "pd.responsible"
        else:  # date
            grain = (date_grain or "day").lower()
            if grain not in {"day", "week", "month"}:
                grain = "day"
            # estándar postgres
            group_select = f"date_trunc('{grain}', pd.when)::date AS key"
            group_by = f"date_trunc('{grain}', pd.when)"

        # counts base
        sql = f"""
            SELECT {group_select},
                   COUNT(*)::int               AS problems,
                   COUNT(DISTINCT pd.rca_id)::int AS rcas,
                   COALESCE(SUM(CASE WHEN LOWER(pd.status)='open'  THEN 1 ELSE 0 END),0)::int   AS open,
                   COALESCE(SUM(CASE WHEN LOWER(pd.status)='closed'THEN 1 ELSE 0 END),0)::int   AS closed
            FROM public.rca_data_problem_definition pd
            WHERE {where_sql}
            GROUP BY {group_by}
            ORDER BY {group_by}
        """

        rows = await db_cause.fetch_all(sql, tuple(params))

        if include_counts_related:
            # Para cada key, contemos soluciones y acciones
            # Nota: esto puede ser costoso con miles de keys; si es un problema, convertir a una sola consulta con JOINs y GROUP BY.
            # Aquí hacemos un enfoque en lote tomando todos los rca_id por key y contando.
            # 1) Obtener mapeo key -> lista de rca_id
            sql_keys = f"""
                SELECT {group_select} AS key, pd.rca_id::text AS rca_id
                FROM public.rca_data_problem_definition pd
                WHERE {where_sql}
            """
            key_rows = await db_cause.fetch_all(sql_keys, tuple(params))
            rcas_by_key: Dict[Any, List[str]] = {}
            for kr in key_rows:
                rcas_by_key.setdefault(kr["key"], []).append(kr["rca_id"])

            # 2) Traer conteos por rca_id en masa
            all_rcas = list({rid for lst in rcas_by_key.values() for rid in lst})
            if all_rcas:
                sol_counts = await db_cause.fetch_all(
                    "SELECT rca_id::text AS rca_id, COUNT(*)::int AS c FROM public.rca_data_solution WHERE rca_id = ANY(%s) GROUP BY rca_id",
                    (all_rcas,),
                )
                act_counts = await db_cause.fetch_all(
                    "SELECT rca_id::text AS rca_id, COUNT(*)::int AS c FROM public.rca_data_action   WHERE rca_id = ANY(%s) GROUP BY rca_id",
                    (all_rcas,),
                )
                sols_by = {r["rca_id"]: r["c"] for r in sol_counts}
                acts_by = {r["rca_id"]: r["c"] for r in act_counts}

                # 3) sumar por key
                totals_by_key: Dict[Any, Dict[str, int]] = {}
                for k, rid_list in rcas_by_key.items():
                    sc = sum(sols_by.get(rid, 0) for rid in rid_list)
                    ac = sum(acts_by.get(rid, 0) for rid in rid_list)
                    totals_by_key[k] = {"solutions": sc, "actions": ac}

                # 4) merge a rows
                for r in rows:
                    t = totals_by_key.get(r["key"], {"solutions": 0, "actions": 0})
                    r["solutions"] = t["solutions"]
                    r["actions"] = t["actions"]

        return {"by": by, "rows": rows}

    # -------------------------- list_related --------------------------
    @mcp.tool(description="Lista soluciones o acciones relacionadas a un rca_id o conjunto de rca_ids.")
    async def list_related(
        kind: Optional[str] = "solutions",  # solutions|actions|both
        rca_id: Optional[str] = None,
        rca_ids: Optional[List[str]] = None,
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
        owner: Optional[str] = None,
        status: Optional[List[str]] = None,
        text: Optional[str] = None,
        due_from: Optional[str] = None,
        due_to: Optional[str] = None,
    ) -> dict:
        kind = (kind or "solutions").lower()
        if kind not in {"solutions", "actions", "both"}:
            return {"error": "kind debe ser solutions|actions|both"}

        ps = utils._page_size(page_size)
        after_id = int(cursor) if cursor else 0
        like = _like_param(text)
        status_list = _parse_list(status)

        ids = rca_ids or ([] if rca_id is None else [rca_id])
        ids = [str(x) for x in ids]

        def build_where(alias: str) -> (str, List[Any]):
            parts = [f"{alias}.id > %s"]
            prms: List[Any] = [after_id]
            if ids:
                parts.append(f"{alias}.rca_id = ANY(%s)")
                prms.append(ids)
            if owner:
                parts.append(f"LOWER(COALESCE({alias}.owner,'')) = %s")
                prms.append(owner.lower())
            if status_list:
                parts.append(f"LOWER(COALESCE({alias}.status,'')) = ANY(%s)")
                prms.append([s.lower() for s in status_list])
            if like:
                col = "solution" if alias == "s" else "action"
                parts.append(f"LOWER(COALESCE({alias}.{col},'')) ILIKE %s")
                prms.append(like)
            if due_from:
                parts.append(f"{alias}.due_date >= %s")
                prms.append(due_from)
            if due_to:
                parts.append(f"{alias}.due_date <= %s")
                prms.append(due_to)
            return " AND ".join(parts), prms

        out: Dict[str, Any] = {}

        if kind in {"solutions", "both"}:
            where_s, params_s = build_where("s")
            sql_s = (
                "SELECT s.id::text AS id, s.rca_id::text AS rca_id, s.solution, s.owner, s.status, s.due_date "
                "FROM public.rca_data_solution s WHERE " + where_s + " ORDER BY s.id LIMIT %s"
            )
            params_s.append(ps)
            sols = await db_cause.fetch_all(sql_s, tuple(params_s))
            out["solutions"] = {"count": len(sols), "next_cursor": (sols[-1]["id"] if sols and len(sols) == ps else None), "rows": sols}

        if kind in {"actions", "both"}:
            where_a, params_a = build_where("a")
            sql_a = (
                "SELECT a.id::text AS id, a.rca_id::text AS rca_id, a.action, a.owner, a.status, a.due_date "
                "FROM public.rca_data_action a WHERE " + where_a + " ORDER BY a.id LIMIT %s"
            )
            params_a.append(ps)
            acts = await db_cause.fetch_all(sql_a, tuple(params_a))
            out["actions"] = {"count": len(acts), "next_cursor": (acts[-1]["id"] if acts and len(acts) == ps else None), "rows": acts}

        return out

    # -------------------------- simple_counters --------------------------
    @mcp.tool(description="Contadores rápidos: total RCAs, abiertos/cerrados, soluciones y acciones totales (con filtros básicos).")
    async def simple_counters(
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
            f"SELECT COUNT(*)::int AS problems, COUNT(DISTINCT pd.rca_id)::int AS rcas FROM public.rca_data_problem_definition pd WHERE {where_sql}",
            tuple(params),
        )

        # relacionadas
        # Obtenemos los rca_ids que cumplen para contar soluciones/acciones
        rids = await db_cause.fetch_all(
            f"SELECT DISTINCT pd.rca_id::text AS rca_id FROM public.rca_data_problem_definition pd WHERE {where_sql}",
            tuple(params),
        )
        rid_list = [x["rca_id"] for x in rids]
        sol_c = act_c = 0
        if rid_list:
            rs = await db_cause.fetch_one(
                "SELECT COALESCE(SUM(c),0)::int AS c FROM (SELECT COUNT(*)::int AS c FROM public.rca_data_solution WHERE rca_id = ANY(%s)) t",
                (rid_list,),
            )
            ra = await db_cause.fetch_one(
                "SELECT COALESCE(SUM(c),0)::int AS c FROM (SELECT COUNT(*)::int AS c FROM public.rca_data_action   WHERE rca_id = ANY(%s)) t",
                (rid_list,),
            )
            sol_c = rs["c"] if rs else 0
            act_c = ra["c"] if ra else 0

        return {
            "problems": totals.get("problems", 0) if totals else 0,
            "rcas": totals.get("rcas", 0) if totals else 0,
            "solutions": sol_c,
            "actions": act_c,
        }
