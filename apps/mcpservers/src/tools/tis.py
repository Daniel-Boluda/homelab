import asyncio
from typing import Any, Dict, List, Optional, Tuple, Union
from ..deps import db, utils
from fastmcp import FastMCP


def register(mcp: FastMCP):
    """
    Registers MCP tools for working with sensor alias information in BigQuery.
    """

    # -----------------------------
    # Update proposed alias in BQ
    # -----------------------------
    @mcp.tool(description="""
        Updates the BigQuery sensor alias mappings table by setting a 'proposed_alias' 
        for the matching 'sensor_code'.
        Execute this tool ONLY when the user explicitly requests to update the sensor mapping with the proposed alias.
        """)
    async def update_alias_information(
        sensor_code: str,
        proposed_alias: str
    ) -> Dict[str, Any]:
        """
        Update the proposed_alias for a given sensor_code in BigQuery.
        """
        sql_query = """
            UPDATE `plants-of-tomorrow-poc.AXIOM.LOG_INDEX_MASTER_MCP_test`
            SET proposed_alias = @proposed_alias
            WHERE sensor_code = @sensor_code;
        """
        params = {
            "proposed_alias": proposed_alias,
            "sensor_code": sensor_code,
        }

        try:
            rows_affected = await db.execute(sql_query, params)
            return {
                "status": "SUCCESS",
                "rows_affected": rows_affected,
                "query": sql_query.strip().splitlines()[0] + ".",
            }
        except Exception as e:
            error_msg = (
                "Database update failed for table "
                "plants-of-tomorrow-poc.AXIOM.LOG_INDEX_MASTER_MCP_test: "
                f"{e}"
            )
            # logging could go here via shared_modules/utils/logging_module.py
            return {
                "status": f"FAILURE: {error_msg}",
                "rows_affected": 0,
                "query": sql_query.strip().splitlines()[0] + ".",
            }

    # -----------------------------------------------------
    # Internal helper (NOT a tool) to fetch sensor details
    # -----------------------------------------------------
    async def _lookup_sensor_info(sensor_codes: List[str]) -> Dict[str, Union[str, None]]:
        """
        Internal async helper that queries BigQuery for supporting info about sensors.

        Returns a dict mapping each requested logname to a string:
          "<description>, <aliasname>, measured in <unit>"
        or None if not found.
        """
        # Initialize all with None so any missing rows remain present in the output.
        return_dict: Dict[str, Union[str, None]] = {code: None for code in sensor_codes}

        # The UNNEST parameter pattern is required by BigQuery to pass lists.
        sql_query = """
            SELECT 
                logname, description, aliasname, unit
            FROM `plants-of-tomorrow-poc.AXIOM.LOG_INDEX_MASTER_MCP_test`
            WHERE logname IN UNNEST(@lognames);
        """
        params = {"lognames": sensor_codes}

        try:
            db_rows = await db.fetch_all(sql_query, params)
            row_map = {row["logname"]: row for row in db_rows}
            for code in sensor_codes:
                entry = row_map.get(code)
                if entry:
                    info_string = (
                        entry.get("description", "N/A") + ", "
                        + entry.get("aliasname", "N/A") + ", measured in "
                        + entry.get("unit", "N/A")
                    )
                    return_dict[code] = info_string
        except Exception as e:
            # Log with your logging module if desired
            print(f"ERROR: BigQuery fetch failed: {e}")

        return return_dict

    # -------------------------------------------------
    # Public tool wrapper that uses the internal helper
    # -------------------------------------------------
    @mcp.tool(description="""
        Fetches detailed information (description, alias, unit) for a list of sensor 
        codes ('logname' field) from the BigQuery sensor master index table.
        This will serve supporting information to the generate_log_alias tool.
        """)
    async def lookup_sensor_info_from_bq(
        sensor_codes: List[str]
    ) -> Dict[str, Union[str, None]]:
        """
        Tool: return supporting info for each provided sensor logname.
        """
        return await _lookup_sensor_info(sensor_codes)

    # ----------------------------------------------
    # Tool to prepare alias-generation instructions
    # ----------------------------------------------
    @mcp.tool(description="""
        This tool creates new, user-friendly, and accurate alias for the log names or sensor codes (e.g., 'MDF.481-PF02.F1:PV_AVG').
        This tool is mandatory for the LLM to create, generate, and propose a new, accurate, and context-aware sensor alias.
        """)
    async def generate_log_alias(
        sensor_codes: List[str]
    ) -> str:
        """
        Returns an instruction block containing supporting info for the requested sensors
        to guide an LLM in producing short, underscore-separated aliases.
        """
        # IMPORTANT: Call the internal helper directly (NOT the tool wrapper)
        result_dict = await _lookup_sensor_info(sensor_codes)

        return f"""
        The user is requesting new, user-friendly, and accurate alias for the sensors **{sensor_codes}**.

        * Get the general naming guidelines available in the workspace context (sensor_coding_guidelines.txt) to identify the relevant sensor information in a cement plant.
        * Combine the insight from general naming guidelines with the following supporting information for each sensor
        {result_dict}
        * For each sensor, create a derived alias that is descriptive but short, using **underscore ('_') separators**.
        * Output the final result as a list of JSON objects, one for each sensor, as shown below.

        {{
            "sensor_code": "[SENSOR_CODE]",
            "sensor_alias": "[YOUR_DERIVED_ALIAS]"
        }}
        """
