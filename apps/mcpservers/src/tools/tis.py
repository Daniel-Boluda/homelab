import asyncio
from typing import Any, Dict, List, Optional, Tuple, Union
from ..deps import db , utils
from fastmcp import FastMCP

def register(mcp: FastMCP):
   
    @mcp.tool(description="""
        Updates the BigQuery sensor alias mappings table by setting a 'proposed_alias' 
        for the matching 'sensor_code'.
        Execute this tool ONLY when the user explicitly requests to update the sensor mapping with the proposed alias.
        """)
    async def update_alias_information(
        sensor_code: str,
        proposed_alias: str
    ) -> Dict[str, Any]:
        
        # 1. Define the parameterized SQL query for BigQuery
        # BigQuery syntax uses backticks (`) for table identifiers and '@' for named parameters
        sql_query = f"""
            UPDATE `plants-of-tomorrow-poc.AXIOM.LOG_INDEX_MASTER_MCP_test`
            SET proposed_alias = @proposed_alias
            WHERE sensor_code = @sensor_code;
        """

        # 2. Define the parameters for the query
        params = {
            "proposed_alias": proposed_alias,
            "sensor_code": sensor_code,
        }

        # 3. Execute the UPDATE query asynchronously
        try:
            # Assuming db.execute returns the number of rows affected (a common pattern)
            rows_affected = await db.execute(sql_query, params) 

            return_dict = {
                "status": "SUCCESS",
                "rows_affected": rows_affected,
                "query": sql_query.strip().splitlines()[0] + '...'
            }

            return return_dict
    
        except Exception as e:
            # Log the error and return a detailed failure message
            error_msg = f"Database update failed for table plants-of-tomorrow-poc.AXIOM.LOG_INDEX_MASTER_MCP_test: {e}"
            # logging_module.error(error_msg) # Use shared_modules/utils/logging_module.py
            
            return_dict = {
                "status": f"FAILURE: {error_msg}",
                "rows_affected": 0,
                "query": sql_query.strip().splitlines()[0] + '...'
            }

            return return_dict


    @mcp.tool(description="""
        Fetches detailed information (description, alias, unit) for a list of sensor 
        codes ('logname' field) from the BigQuery sensor master index table.
        This will serve supporting information to the generate_log_alias tool.
        """)
    async def lookup_sensor_info_from_bq(
        sensor_codes: List[str]
    ) -> Dict[str, Union[str, None]]:
        # Initialize all with None
        return_dict = {code: None for code in sensor_codes}
        
        # 1. Define the SQL query with parameterized 'IN' clause
        # The UNNEST is necessary for BigQuery to handle a list of parameters for an IN clause.
        sql_query = f"""
            SELECT 
                logname, description, aliasname, unit
            FROM `plants-of-tomorrow-poc.AXIOM.LOG_INDEX_MASTER_MCP_test`
            WHERE logname IN UNNEST(@lognames);
        """

        # 2. Define the query parameters
        params = {
            "lognames": sensor_codes # BigQuery parameter for UNNEST expects a list
        }

        # 3. Execute the query asynchronously
        try:
            # Use the assumed async DB client to fetch all matching rows
            db_rows = await db.fetch_all(sql_query, params) 
            row_map = {row['logname']: row for row in db_rows}
            for code in sensor_codes:
                entry = row_map.get(code)
                if entry:
                    # Construct the required information string based on fetched fields
                    info_string = (
                        entry.get("description", "N/A") + ", " + 
                        entry.get("aliasname", "N/A") + ", measured in " + 
                        entry.get("unit", "N/A")
                    )
                    return_dict[code] = info_string

        except Exception as e:
            # Log error using shared_modules/utils/logging_module.py (omitted here for brevity)
            print(f"ERROR: BigQuery fetch failed: {e}")
            # On failure, return all requested codes as None
            return return_dict


    @mcp.tool(description="""
        This tool creates new, user-friendly, and accurate alias for the log names or sensor codes(e.g., 'MDF.481-PF02.F1:PV_AVG).
        This tool is mandatory for the LLM to create, generate, and propose a new, accurate, and context-aware sensor alias.
        """
    )
    async def generate_log_alias(
        sensor_codes: List[str]
    ) -> str:
        # CRITICAL: Delegate the blocking I/O operation to an external thread
        result_dict = await asyncio.to_thread(
            lookup_sensor_info_from_bq,
            sensor_codes
        )

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
