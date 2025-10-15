from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from typing import Optional
from src.deps import db , utils
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
