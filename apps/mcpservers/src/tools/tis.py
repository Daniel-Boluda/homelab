from typing import Any, Dict, List, Optional, Union
from fastmcp import FastMCP
from google.cloud import bigquery
from fastmcp.prompts.prompt import Message
import pandas as pd
import re


def register(mcp: FastMCP):
    """
    Herramientas MCP prefijadas con 'tis_' para interactuar con BigQuery (tabla maestra de lognames/alias),
    filtradas por plant_name.
    """

    client = bigquery.Client(project="plants-of-tomorrow-poc")
    MASTER_TABLE = "`plants-of-tomorrow-poc.AXIOM.LOG_INDEX_MASTER_MCP_test`"
    # Esquema de la tabla:
    # logname, aliasname, description, unit, ri_class, log_class, plant_name, proposed_alias

    # -------------------------
    # Utilidades internas
    # -------------------------
    def _df_to_records(df: pd.DataFrame) -> List[Dict[str, Any]]:
        if df is None or df.empty:
            return []
        return df.to_dict(orient="records")

    async def _lookup_sensor_info(
        plant_name: str,
        sensor_codes: List[str],
        *,
        logname_column: str = "logname",
        plant_column: str = "plant_name",
        description_column: str = "description",
        aliasname_column: str = "aliasname",
        unit_column: str = "unit",
    ) -> Dict[str, Optional[str]]:
        """
        Devuelve {logname: "description, aliasname, measured in unit"} o None (filtrado por plant_name).
        """
        result: Dict[str, Optional[str]] = {code: None for code in sensor_codes}

        query = f"""
            SELECT
                {logname_column} AS logname,
                {description_column} AS description,
                {aliasname_column} AS aliasname,
                {unit_column} AS unit
            FROM {MASTER_TABLE}
            WHERE {plant_column} = @plant_name
              AND {logname_column} IN UNNEST(@lognames)
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("plant_name", "STRING", plant_name),
                bigquery.ArrayQueryParameter("lognames", "STRING", sensor_codes),
            ]
        )

        try:
            df = client.query(query, job_config=job_config).to_dataframe()
            for _, row in df.iterrows():
                info = f"{row.get('description','N/A')}, {row.get('aliasname','N/A')}, measured in {row.get('unit','N/A')}"
                result[str(row["logname"])] = info
        except Exception as e:
            print(f"[tis_lookup] BigQuery error: {e}")

        return result

    # -------------------------
    # MCP TOOLS (prefijo tis_)
    # -------------------------

    @mcp.tool(description="Lista las plantas disponibles (plant_name).")
    async def tis_list_plants(
        plant_column: str = "plant_name",
    ) -> List[Dict[str, Any]]:
        query = f"""
            SELECT DISTINCT
                {plant_column} AS plant_name
            FROM {MASTER_TABLE}
            WHERE {plant_column} IS NOT NULL
            ORDER BY plant_name
        """
        try:
            df = client.query(query).to_dataframe()
            return _df_to_records(df)
        except Exception as e:
            return [{"status": "FAILURE", "error": str(e)}]

    @mcp.tool(description="Obtiene (por PLANTA) información de soporte (descripción, alias, unidad) para una lista de lognames.")
    async def tis_lookup_sensor_info(
        plant_name: str,
        sensor_codes: List[str],
        logname_column: str = "logname",
        plant_column: str = "plant_name",
        description_column: str = "description",
        aliasname_column: str = "aliasname",
        unit_column: str = "unit",
    ) -> Dict[str, Optional[str]]:
        return await _lookup_sensor_info(
            plant_name,
            sensor_codes,
            logname_column=logname_column,
            plant_column=plant_column,
            description_column=description_column,
            aliasname_column=aliasname_column,
            unit_column=unit_column,
        )

    @mcp.tool(description="Crea un bloque de instrucciones para que un LLM proponga alias legibles/consistentes por planta (HAC como contexto).")
    async def tis_generate_log_alias(
        plant_name: str,
        sensor_codes: List[str],
        logname_column: str = "logname",
        plant_column: str = "plant_name",
        description_column: str = "description",
        aliasname_column: str = "aliasname",
        unit_column: str = "unit",
    ) -> str:
        result_dict = await _lookup_sensor_info(
            plant_name,
            sensor_codes,
            logname_column=logname_column,
            plant_column=plant_column,
            description_column=description_column,
            aliasname_column=aliasname_column,
            unit_column=unit_column,
        )

        return Message(f"""
        The user is requesting new, user-friendly, and accurate aliases for sensors **{sensor_codes}** in plant **{plant_name}**.

        * Use the general naming guidelines available in the workspace (HAC manual context).
        * Combine those guidelines with the following supporting information per sensor:
          {result_dict}
        * For each sensor, create a derived alias that is descriptive but short, using **underscore ('_') separators**.
        * Ensure the derived alias has the same language found in the supporting information.
        * Look at examples in the logname aliasname examples available in the workspace.
        * Output the final result as a list of JSON objects, one per sensor, like:

        {{
            "plant_name": "{plant_name}",
            "sensor_code": "[SENSOR_CODE]",
            "sensor_alias": "[YOUR_DERIVED_ALIAS]"
        }}
        """)

    # ✅ UPDATE ESTRICTO: SIEMPRE por logname + plant_name (sin opciones creativas)
    @mcp.tool(description="Actualiza 'proposed_alias' en BigQuery filtrando por logname + plant_name (modo estricto).")
    async def tis_update_alias_information(
        plant_name: str,
        logname: str,
        proposed_alias: str,
        *,
        plant_column: str = "plant_name",
        logname_column: str = "logname",
        proposed_alias_column: str = "proposed_alias",
    ) -> Dict[str, Any]:
        query = f"""
            UPDATE {MASTER_TABLE}
            SET {proposed_alias_column} = @proposed_alias
            WHERE {plant_column} = @plant_name
              AND {logname_column} = @logname
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("proposed_alias", "STRING", proposed_alias),
                bigquery.ScalarQueryParameter("plant_name", "STRING", plant_name),
                bigquery.ScalarQueryParameter("logname", "STRING", logname),
            ]
        )
        try:
            job = client.query(query, job_config=job_config)
            job.result()
            return {"status": "SUCCESS", "rows_affected": job.num_dml_affected_rows}
        except Exception as e:
            return {"status": "FAILURE", "error": str(e)}

    @mcp.tool(description="Busca lognames/alias/description similares en una PLANTA (regex personalizadas, sin hardcode).")
    async def tis_search_similar(
        plant_name: str,
        patterns: List[str],
        *,
        search_in: List[str] = ["logname", "aliasname", "description"],
        logname_column: str = "logname",
        aliasname_column: str = "aliasname",
        description_column: str = "description",
        plant_column: str = "plant_name",
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        colmap = {
            "logname": logname_column,
            "aliasname": aliasname_column,
            "description": description_column,
        }
        target_cols = [colmap[c] for c in search_in if c in colmap]
        if not target_cols:
            return [{"status": "FAILURE", "error": "search_in vacío o inválido"}]
        if not patterns:
            return [{"status": "FAILURE", "error": "patterns no puede estar vacío"}]

        ors = []
        for col in target_cols:
            for i, _ in enumerate(patterns):
                ors.append(f"REGEXP_CONTAINS({col}, @pat_{col}_{i})")
        where_or = " OR ".join(ors)

        query = f"""
            SELECT
                {logname_column} AS logname,
                {aliasname_column} AS aliasname,
                {description_column} AS description
            FROM {MASTER_TABLE}
            WHERE {plant_column} = @plant_name
              AND ({where_or})
            LIMIT {limit}
        """
        params = [bigquery.ScalarQueryParameter("plant_name", "STRING", plant_name)]
        for col in target_cols:
            for i, pat in enumerate(patterns):
                params.append(bigquery.ScalarQueryParameter(f"pat_{col}_{i}", "STRING", pat))
        job_config = bigquery.QueryJobConfig(query_parameters=params)

        try:
            df = client.query(query, job_config=job_config).to_dataframe()
            return _df_to_records(df)
        except Exception as e:
            return [{"status": "FAILURE", "error": str(e)}]

    @mcp.tool(description="Devuelve lognames/alias “parecidos” a un logname base dentro de una PLANTA (tokens + regex opcional).")
    async def tis_similar_to_logname(
        plant_name: str,
        base_logname: str,
        *,
        extra_patterns: Optional[List[str]] = None,
        search_in: List[str] = ["logname", "aliasname", "description"],
        logname_column: str = "logname",
        aliasname_column: str = "aliasname",
        description_column: str = "description",
        plant_column: str = "plant_name",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        tokens = [t for t in re.split(r"[.\-:_+#/ ]+", base_logname) if t]
        tokens = tokens[:6]

        colmap = {
            "logname": logname_column,
            "aliasname": aliasname_column,
            "description": description_column,
        }
        target_cols = [colmap[c] for c in search_in if c in colmap]
        if not target_cols:
            return [{"status": "FAILURE", "error": "search_in vacío o inválido"}]

        likes = []
        for col in target_cols:
            for i, tok in enumerate(tokens):
                likes.append(f"LOWER({col}) LIKE LOWER(@like_{col}_{i})")

        regexes = []
        if extra_patterns:
            for col in target_cols:
                for i, _ in enumerate(extra_patterns):
                    regexes.append(f"REGEXP_CONTAINS({col}, @re_{col}_{i})")

        clauses = []
        if likes:
            clauses.append("(" + " OR ".join(likes) + ")")
        if regexes:
            clauses.append("(" + " OR ".join(regexes) + ")")
        if not clauses:
            return [{"status": "FAILURE", "error": "No hay tokens ni patrones para búsqueda"}]

        where_cond = " AND ".join([
            f"{plant_column} = @plant_name",
            "(" + " OR ".join(clauses) + ")",
        ])

        query = f"""
            SELECT
                {logname_column} AS logname,
                {aliasname_column} AS aliasname,
                {description_column} AS description
            FROM {MASTER_TABLE}
            WHERE {where_cond}
            LIMIT {limit}
        """
        params: List[bigquery.ScalarQueryParameter] = [
            bigquery.ScalarQueryParameter("plant_name", "STRING", plant_name)
        ]
        for col in target_cols:
            for i, tok in enumerate(tokens):
                params.append(bigquery.ScalarQueryParameter(f"like_{col}_{i}", "STRING", f"%{tok}%"))
        if extra_patterns:
            for col in target_cols:
                for i, pat in enumerate(extra_patterns):
                    params.append(bigquery.ScalarQueryParameter(f"re_{col}_{i}", "STRING", pat))
        job_config = bigquery.QueryJobConfig(query_parameters=params)

        try:
            df = client.query(query, job_config=job_config).to_dataframe()
            return _df_to_records(df)
        except Exception as e:
            return [{"status": "FAILURE", "error": str(e)}]

    @mcp.tool(description="Consulta libre (solo lectura) filtrando por plant_name, columnas personalizables.")
    async def tis_query_by_plant(
        plant_name: str,
        select_columns: List[str] = ["logname", "aliasname", "description", "unit"],
        *,
        plant_column: str = "plant_name",
        extra_where: Optional[str] = None,
        limit: int = 200
    ) -> List[Dict[str, Any]]:
        cols = ", ".join([f"{c}" for c in select_columns]) if select_columns else "*"
        query = f"""
            SELECT {cols}
            FROM {MASTER_TABLE}
            WHERE {plant_column} = @plant_name
        """
        if extra_where:
            query += f" AND ({extra_where})"
        query += f" LIMIT {limit}"

        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("plant_name", "STRING", plant_name)]
        )
        try:
            df = client.query(query, job_config=job_config).to_dataframe()
            return _df_to_records(df)
        except Exception as e:
            return [{"status": "FAILURE", "error": str(e)}]

    @mcp.tool(description="Búsqueda guiada por CONTEXTO (HAC u otros términos) dentro de una PLANTA (plant_name).")
    async def tis_guided_search_by_context(
        plant_name: str,
        natural_query: str,
        *,
        context_terms: Optional[List[str]] = None,
        synonyms: Optional[Dict[str, List[str]]] = None,
        extra_patterns: Optional[List[str]] = None,
        search_in: List[str] = ["logname", "aliasname", "description"],
        logname_column: str = "logname",
        aliasname_column: str = "aliasname",
        description_column: str = "description",
        plant_column: str = "plant_name",
        limit: int = 200
    ) -> List[Dict[str, Any]]:
        """
        Estrategia:
          1) Tokenizar natural_query -> tokens (poco opinativo). Usar LIKE por tokens.
          2) Añadir REGEXP_CONTAINS para cada patrón de context_terms, synonyms y extra_patterns.
          3) Combinar todo con OR entre columnas objetivo.
        """
        tokens = [t for t in re.split(r"[.\-:_+#/ ]+", natural_query) if t]
        tokens = tokens[:8]  # límite defensivo

        colmap = {
            "logname": logname_column,
            "aliasname": aliasname_column,
            "description": description_column,
        }
        target_cols = [colmap[c] for c in search_in if c in colmap]
        if not target_cols:
            return [{"status": "FAILURE", "error": "search_in vacío o inválido"}]

        likes = []
        for col in target_cols:
            for i, tok in enumerate(tokens):
                likes.append(f"LOWER({col}) LIKE LOWER(@like_{col}_{i})")

        regex_patterns: List[str] = []
        if context_terms:
            regex_patterns.extend(context_terms)
        if synonyms:
            for _, arr in synonyms.items():
                if arr:
                    regex_patterns.extend(arr)
        if extra_patterns:
            regex_patterns.extend(extra_patterns)

        regexes = []
        for col in target_cols:
            for i in range(len(regex_patterns)):
                regexes.append(f"REGEXP_CONTAINS({col}, @re_{col}_{i})")

        if not likes and not regexes:
            return [{"status": "FAILURE", "error": "No hay tokens ni patrones para búsqueda"}]

        where_parts = [f"{plant_column} = @plant_name"]
        col_filters = []
        if likes:
            col_filters.append("(" + " OR ".join(likes) + ")")
        if regexes:
            col_filters.append("(" + " OR ".join(regexes) + ")")
        where_parts.append("(" + " OR ".join(col_filters) + ")")

        query = f"""
            SELECT
                {logname_column} AS logname,
                {aliasname_column} AS aliasname,
                {description_column} AS description
            FROM {MASTER_TABLE}
            WHERE {" AND ".join(where_parts)}
            LIMIT {limit}
        """

        params: List[bigquery.ScalarQueryParameter] = [
            bigquery.ScalarQueryParameter("plant_name", "STRING", plant_name)
        ]
        for col in target_cols:
            for i, tok in enumerate(tokens):
                params.append(bigquery.ScalarQueryParameter(f"like_{col}_{i}", "STRING", f"%{tok}%"))
        for col in target_cols:
            for i, pat in enumerate(regex_patterns):
                params.append(bigquery.ScalarQueryParameter(f"re_{col}_{i}", "STRING", pat))

        job_config = bigquery.QueryJobConfig(query_parameters=params)

        try:
            df = client.query(query, job_config=job_config).to_dataframe()
            return _df_to_records(df)
        except Exception as e:
            return [{"status": "FAILURE", "error": str(e)}]
