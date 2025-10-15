import os
import json
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
from typing import Optional
from src.deps import db , utils
from pydantic import BaseModel, Field
from fastmcp import FastMCP

class AliasGeneratorInput(BaseModel):
    """Input contract for the generate_log_alias tool."""
    sensor_codes: List[str] = Field(..., description="The list of internal sensor codes (lognames) that need an alias name.")


# Define the relative path to the mapping file
ALIAS_MAPPING_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..',
    'data',
    'sensor_alias_mappings.json'
)


def _read_and_update_json(update_map: Dict[str, str], file_path: str) -> Dict[str, Any]:
    """
    Synchronous (Blocking) I/O Function: Reads, updates multiple entries based on 
    the update_map, and writes the JSON file using the Atomic Write Pattern for safety.

    Args:
        update_map: A dictionary where keys are 'sensor_code' (logname) and 
                    values are 'proposed_alias'.
        file_path: The path to the JSON mapping file.

    Returns:
        A dictionary with success status and a detailed message.
    """
    if not os.path.exists(file_path):
        return {"success": False, "message": f"Error: Mapping file not found at {file_path}"}

    try:
        # 1. READ the existing data
        with open(file_path, 'r', encoding='utf-8') as f:
            data: List[Dict[str, Any]] = json.load(f) 

        found_count = 0
        updates_requested = len(update_map)
        missing_codes = []

        # 2. MODIFY all entries based on the update_map
        for entry in data:
            logname = entry.get("logname")
            
            # Check if this logname is one of the keys in the map
            if logname in update_map:
                new_alias = update_map[logname]
                
                # Apply the new key-value pair
                entry["proposed_alias"] = new_alias
                
                found_count += 1
                # Remove from map so we can track which were not found
                del update_map[logname] 

        # At this point, update_map contains only the codes that were NOT found
        if found_count < updates_requested:
            missing_codes = list(update_map.keys())
            
        if found_count == 0:
            return {"success": False, "message": f"Error: None of the {updates_requested} sensor codes were found in the mapping file. Missing: {missing_codes}"}

        # --- ATOMIC WRITE IMPLEMENTATION ---
        # A. Write to a temporary file
        temp_file_path = file_path + '.tmp'
        
        # Open the file for writing. Using 'with' ensures the file handle is closed immediately.
        with open(temp_file_path, 'w', encoding='utf-8') as f:
            # 3. Serialize and Write the updated data (prettify for readability)
            json.dump(data, f, indent=4)
        
        # B. Atomically replace the original file with the new, verified content
        # Note: os.replace handles permissions and closing much better than os.rename
        os.replace(temp_file_path, file_path)
        # --- END ATOMIC WRITE ---
        
        # Formulate a success message detailing missing codes, if any
        message = f"Successfully updated {found_count} out of {updates_requested} sensor entries. File updated."
        if missing_codes:
            message += f" (Note: {len(missing_codes)} code(s) not found: {', '.join(missing_codes)})"

        return {"success": True, "message": message}

    except json.JSONDecodeError:
        return {"success": False, "message": f"Critical error: Mapping file at {file_path} contains invalid JSON."}
    except Exception as e:
        # A PermissionError here is still possible if two processes call os.replace() at the exact same moment.
        # However, the atomic write pattern is the definitive best practice to mitigate this risk.
        return {"success": False, "message": f"Critical error during file operation: {type(e).__name__}: {e}"}

def register(mcp: FastMCP):
   
    @mcp.tool(description="""
        Updates the sensor_alias_mappings.json file by adding a 'proposed_alias' to the matching sensor_code entry.
        Execute this tool ONLY when the user explicitly requests to update the sensor mapping with the proposed alias.
        """)
    async def update_new_alias(update_map: Dict[str, str]) -> Dict[str, Any]:
        # CRITICAL: Delegate the blocking I/O operation to an external thread
        result_dict = await asyncio.to_thread(
            _read_and_update_json,
            update_map,
            ALIAS_MAPPING_FILE
        )

        # Convert the resulting dictionary back into the Pydantic output contract
        return result_dict


    def _lookup_sensor_info(sensor_codes: List[str], file_path: str) -> Dict[str, Union[str, None]]:
        """
        Synchronous (Blocking) I/O Function: Reads the JSON file and performs the lookup
        for a list of sensor codes.

        Returns:
            A dictionary where keys are the input sensor codes and values are the 
            combined information string or None if not found or on file error.
        """
        results: Dict[str, Union[str, None]] = {code: None for code in sensor_codes} # Initialize all with None

        if not os.path.exists(file_path):
            # File not found, all results remain None as initialized
            return results

        try:
            # 1. Read the existing data once
            with open(file_path, 'r', encoding='utf-8') as f:
                data: List[Dict[str, Any]] = json.load(f)

            # 2. Iterate over the provided sensor codes
            for sensor_code in sensor_codes:
                
                # 3. Search for the matching entry in the data
                for entry in data:
                    # Match the input 'sensor_code' against the JSON object's 'logname' key
                    if sensor_code in entry.get("logname"): 
                        
                        # Construct the required information string
                        info_string = (
                            entry.get("description", "N/A") + ", " + 
                            entry.get("aliasname", "N/A") + ", measured in " + 
                            entry.get("unit", "N/A")
                        )
                        
                        # Store the result and break the inner loop
                        results[sensor_code] = info_string
                        break 
                        
                # If the inner loop finishes without a break, results[sensor_code] remains None

        except Exception:
            # If any critical error occurs during file reading/parsing, all entries
            # will remain None, which aligns with the simplified error handling.
            pass 

        return results

    @mcp.tool(description="""
        This tool creates new, user-friendly, and accurate alias for the log names or sensor codes(e.g., 'MDF.481-PF02.F1:PV_AVG).
        This tool is mandatory for the LLM to create, generate, and propose a new, accurate, and context-aware sensor alias.
        """
    )
    async def generate_log_alias(payload: AliasGeneratorInput) -> str:
        # CRITICAL: Delegate the blocking I/O operation to an external thread
        result_dict = await asyncio.to_thread(
            _lookup_sensor_info,
            payload.sensor_codes,
            ALIAS_MAPPING_FILE
        )

        return f"""
        The user is requesting new, user-friendly, and accurate alias for the sensors **{payload.sensor_codes}**.

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
