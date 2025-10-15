from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from typing import Optional
from ..deps import db , utils
from fastmcp import FastMCP

def register(mcp: FastMCP):
    
    # -------- list_plants --------
    @mcp.tool(description="Lista plantas con paginación keyset.")
    async def list_plants(page_size: Optional[int] = None, cursor: Optional[str] = None) -> dict:
        ps = utils._page_size(page_size)
        after_id = int(cursor) if cursor else 0
        sql = """
            SELECT id::text AS id, name, acs_code
            FROM public.plants_plant
            WHERE deleted_at IS NULL AND id > %s
            ORDER BY id
            LIMIT %s;
        """
        rows = await db.fetch_all(sql, (after_id, ps))
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
            plant = await db.fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND id = %s",
                (int(plant_id),),
            )
        elif plant_name:
            # match exacto por nombre; puedes cambiar a ILIKE si quieres más laxo
            plant = await db.fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND name = %s",
                (plant_name,),
            )
            if not plant:
                # fallback: normalización en Python
                candidates = await db.fetch_all(
                    "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL"
                )
                target = utils._norm(plant_name)
                plant = next((p for p in candidates if utils._norm(p["name"]) == target or p["acs_code"] == plant_name), None)

        if not plant:
            return {"error": "Plant not found", "plant_name": plant_name, "plant_id": plant_id}

        ps = utils._page_size(page_size)
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
            rows = await db.fetch_all(sql, (pid, after_id, ps))
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
            rows = await db.fetch_all(sql, (pid, after_id, ps))
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
                         ORDER BY { utils._risk_order_sql('m2') } DESC
                         LIMIT 1
                       ) AS top_level
                FROM public.mpredict_mpredictalert m
                WHERE m.asset_id = a.id
            ) agt ON TRUE
            WHERE a.plant_id = %s AND a.id > %s
            ORDER BY a.id
            LIMIT %s;
        """
        rows = await db.fetch_all(sql, (pid, after_id, ps))
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
            plant = await db.fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND id = %s",
                (int(plant_id),),
            )
        elif plant_name:
            plant = await db.fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND name = %s",
                (plant_name,),
            )
            if not plant:
                candidates = await db.fetch_all(
                    "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL"
                )
                target = utils._norm(plant_name)
                plant = next((p for p in candidates if utils._norm(p["name"]) == target or p["acs_code"] == plant_name), None)
        if not plant:
            return {"error": "Plant not found"}

        ps = utils._page_size(page_size)
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
                             ORDER BY { utils._risk_order_sql('m2') } DESC
                             LIMIT 1
                           ) AS top_level
                    FROM public.mpredict_mpredictalert m
                    WHERE m.asset_id = a.id AND lower(m.state) = 'open'
                ) agt ON TRUE
                WHERE a.plant_id = %s AND a.id > %s AND COALESCE(agt.alert_count, 0) > 0
                ORDER BY a.id
                LIMIT %s;
            """
        rows = await db.fetch_all(sql, (pid, after_id, ps))
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
            plant = await db.fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND id = %s",
                (int(plant_id),),
            )
        elif plant_name:
            plant = await db.fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND name = %s",
                (plant_name,),
            )
            if not plant:
                candidates = await db.fetch_all(
                    "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL"
                )
                target = utils._norm(plant_name)
                plant = next((p for p in candidates if utils._norm(p["name"]) == target or p["acs_code"] == plant_name), None)
        if not plant:
            return {"error": "Plant not found"}

        ps = utils._page_size(page_size)
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
                             ORDER BY { utils._risk_order_sql('m2') } DESC
                             LIMIT 1
                           ) AS top_level
                    FROM public.mpredict_mpredictalert m
                    WHERE m.asset_id = a.id
                ) agt ON TRUE
                WHERE a.plant_id = %s AND a.id > %s AND a.risk_level = 'HIGH'
                ORDER BY a.id
                LIMIT %s;
            """
        rows = await db.fetch_all(sql, (pid, after_id, ps))
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
        ps = utils._page_size(page_size)
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
                             ORDER BY { utils._risk_order_sql('m2') } DESC
                             LIMIT 1
                           ) AS top_level
                    FROM public.mpredict_mpredictalert m
                    WHERE m.asset_id = a.id
                ) agt ON TRUE
                WHERE a.id > %s
                ORDER BY a.id
                LIMIT %s;
            """
        rows = await db.fetch_all(sql, (after_id, ps))
        next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
        return {"detail": detail or "summary", "count": len(rows), "next_cursor": next_cursor, "machines": rows}

    # -------- machines_with_open_alerts (global) --------
    @mcp.tool(description="Máquinas con alertas abiertas (global).")
    async def machines_with_open_alerts(
        detail: Optional[str] = "summary",
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        ps = utils._page_size(page_size)
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
                             ORDER BY { utils._risk_order_sql('m2') } DESC
                             LIMIT 1
                           ) AS top_level
                    FROM public.mpredict_mpredictalert m
                    WHERE m.asset_id = a.id AND lower(m.state)='open'
                ) agt ON TRUE
                WHERE a.id > %s AND COALESCE(agt.alert_count, 0) > 0
                ORDER BY a.id
                LIMIT %s;
            """
        rows = await db.fetch_all(sql, (after_id, ps))
        next_cursor = rows[-1]["id"] if rows and len(rows) == ps else None
        return {"detail": detail or "summary", "count": len(rows), "next_cursor": next_cursor, "machines": rows}

    # -------- machines_with_high_risk (global) --------
    @mcp.tool(description="Máquinas con riesgo HIGH (global).")
    async def machines_with_high_risk(
        detail: Optional[str] = "summary",
        page_size: Optional[int] = None,
        cursor: Optional[str] = None,
    ) -> dict:
        ps = utils._page_size(page_size)
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
                             ORDER BY { utils._risk_order_sql('m2') } DESC
                             LIMIT 1
                           ) AS top_level
                    FROM public.mpredict_mpredictalert m
                    WHERE m.asset_id = a.id
                ) agt ON TRUE
                WHERE a.id > %s AND a.risk_level='HIGH'
                ORDER BY a.id
                LIMIT %s;
            """
        rows = await db.fetch_all(sql, (after_id, ps))
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
            plant = await db.fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND id = %s",
                (int(plant_id),),
            )
        elif plant_name:
            plant = await db.fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND name = %s",
                (plant_name,),
            )
            if not plant:
                candidates = await db.fetch_all(
                    "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL"
                )
                target = utils._norm(plant_name)
                plant = next((p for p in candidates if utils._norm(p["name"]) == target or p["acs_code"] == plant_name), None)
        if not plant:
            return {"error": "Plant not found"}

        ps = utils._page_size(page_size)
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

        rows = await db.fetch_all(sql, params)

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

        ps = utils._page_size(page_size)
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

        rows = await db.fetch_all(sql, params)

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
            row = await db.fetch_one(
                "SELECT id::text AS id, alert_id, feature_contribution AS features FROM public.mpredict_mpredictalert WHERE id = %s",
                (int(alert_numeric_id),),
            )
        else:
            row = await db.fetch_one(
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
            plant = await db.fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND id = %s",
                (int(plant_id),),
            )
        elif plant_name:
            plant = await db.fetch_one(
                "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL AND name = %s",
                (plant_name,),
            )
            if not plant:
                candidates = await db.fetch_all(
                    "SELECT id::text AS id, name, acs_code FROM public.plants_plant WHERE deleted_at IS NULL"
                )
                target = utils._norm(plant_name)
                plant = next((p for p in candidates if utils._norm(p["name"]) == target or p["acs_code"] == plant_name), None)
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
        row = await db.fetch_one(sql, tuple(params))
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