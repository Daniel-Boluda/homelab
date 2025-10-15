import os
import unicodedata
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_PAGE_SIZE = int(os.getenv("DEFAULT_PAGE_SIZE", "1000"))
DEFAULT_MAX_FEATURES = int(os.getenv("DEFAULT_MAX_FEATURES", "1000"))

def _rows_to_dicts(cur) -> List[Dict[str, Any]]:
    cols = [d[0] for d in cur.description]  # DB-API
    return [dict(zip(cols, row)) for row in cur.fetchall()]

# -----------------------------------------------------------------------------
# Utils mínimos
# -----------------------------------------------------------------------------
def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"

def _norm(s: str) -> str:
    s = s.strip().casefold()
    s = "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))
    return " ".join(s.split())

def _page_size(ps: Optional[int]) -> int:
    try:
        ps = int(ps or DEFAULT_PAGE_SIZE)
    except Exception:
        ps = DEFAULT_PAGE_SIZE
    return max(1, min(ps, 500))  # cap razonable

def _risk_order_sql(alias: str = "m2") -> str:
    # Rank de nivel de riesgo: HIGH > MEDIUM > LOW > otros
    return (
        f"CASE WHEN {alias}.risk_level = 'HIGH' THEN 3 "
        f"WHEN {alias}.risk_level = 'MEDIUM' THEN 2 "
        f"WHEN {alias}.risk_level = 'LOW' THEN 1 ELSE 0 END"
    )