import os
from typing import Any, Dict, List, Optional, Tuple

import anyio
from google.cloud.sql.connector import Connector, IPTypes


# -----------------------------------------------------------------------------
# DB (Cloud SQL Connector + pg8000)
# -----------------------------------------------------------------------------
INSTANCE = os.environ.get("PORTAL_INSTANCE") or os.environ.get("INSTANCE_CONNECTION_NAME")
PGUSER   = os.environ.get("PORTAL_PGUSER")
PGPASS   = os.environ.get("PORTAL_PGPASS")
PGDB     = os.environ.get("PORTAL_PGDB")
IP_TYPE  = (os.environ.get("PORTAL_IP_TYPE") or "PUBLIC").upper()  # PUBLIC | PRIVATE

if not all([INSTANCE, PGUSER, PGPASS, PGDB]):
    raise RuntimeError("Faltan variables DB: INSTANCE/INSTANCE_CONNECTION_NAME, PGUSER, PGPASS, PGDB")

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