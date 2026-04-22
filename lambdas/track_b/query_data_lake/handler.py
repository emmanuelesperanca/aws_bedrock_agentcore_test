"""
lambdas/track_b/query_data_lake/handler.py

Track B — Natural Language → SQL → Athena Data Lake Query.

Flow:
  1. Extract user's question + context from Bedrock Action Group event
  2. Use Claude Haiku (cheap & fast) to generate a safe, read-only SQL query
     against the Glue Data Catalog tables
  3. Execute the SQL in Athena with scan-budget enforcement
  4. Poll until complete (max 15 × 2s = 30s)
  5. Return formatted results (max 50 rows) back to Bedrock Agent

Security controls:
  - Only SELECT statements are allowed (injection guard)
  - Athena workgroup `neoson-workgroup` has 10 GB BytesScannedCutoffPerQuery
  - Row-level filter WHERE clause is appended automatically based on user department

Environment variables:
  BEDROCK_MODEL_ID_HAIKU    — e.g. "us.anthropic.claude-haiku-3-5-20241022-v1:0"
  ATHENA_WORKGROUP          — default "neoson-workgroup"
  ATHENA_OUTPUT_LOCATION    — s3://neoson-athena-results/
  GLUE_DATABASE             — Glue database name, default "neoson_datalake"
  GLUE_SCHEMA_HINT          — JSON string with table descriptions for prompt context
  AWS_REGION                — default "us-east-1"
  LOG_LEVEL                 — default "INFO"
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time

import boto3
import botocore

sys.path.insert(0, "/opt/python")

from neoson_lambda_commons import (
    parse_bedrock_event,
    build_bedrock_response,
    build_error_response,
    extract_user_context,
    UserContext,
)

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
_MODEL_ID_HAIKU = os.environ.get(
    "BEDROCK_MODEL_ID_HAIKU",
    "us.anthropic.claude-haiku-3-5-20241022-v1:0",
)
_ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "neoson-workgroup")
_ATHENA_OUTPUT = os.environ.get(
    "ATHENA_OUTPUT_LOCATION", "s3://neoson-athena-results/query-results/"
)
_GLUE_DATABASE = os.environ.get("GLUE_DATABASE", "neoson_datalake")

# Default schema hint — override via env var with a JSON object
_DEFAULT_SCHEMA_HINT = json.dumps({
    "producao_diaria": {
        "description": "Produção diária de implantes e próteses por linha, turno e data",
        "columns": ["data_producao", "linha", "turno", "departamento", "produto", "quantidade_produzida",
                    "quantidade_rejeitada", "operador_id"],
    },
    "qualidade_inspe": {
        "description": "Resultados de inspeção de qualidade por lote",
        "columns": ["data_inspecao", "lote_id", "produto", "linha", "resultado", "defeito_tipo",
                    "inspector_id", "departamento"],
    },
    "manutencao_ordens": {
        "description": "Ordens de manutenção preventiva e corretiva",
        "columns": ["ordem_id", "data_abertura", "data_conclusao", "equipamento_id", "tipo",
                    "prioridade", "tecnico_id", "custo_total", "departamento"],
    },
    "indicadores_kpi": {
        "description": "KPIs consolidados por área e período",
        "columns": ["periodo", "area", "departamento", "kpi_nome", "valor", "meta", "unidade"],
    },
})

_SCHEMA_HINT: str = os.environ.get("GLUE_SCHEMA_HINT", _DEFAULT_SCHEMA_HINT)

# ─── Safety guard ────────────────────────────────────────────────────────────

_DANGEROUS_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|GRANT|REVOKE|EXEC|EXECUTE|MERGE)\b",
    re.IGNORECASE,
)


def _is_safe_sql(sql: str) -> bool:
    """Reject any SQL that contains mutation keywords."""
    return not _DANGEROUS_SQL.search(sql)


# ─── Dept-level RLS WHERE clause ─────────────────────────────────────────────

def _build_rls_where(user_ctx: UserContext) -> str:
    """Return an additional WHERE clause fragment for department filtering.
    Managers (level ≥ 4) and GLOBAL users see all departments.
    """
    if user_ctx.is_global or user_ctx.is_manager:
        return ""
    dept = user_ctx.departamento.replace("'", "''")  # basic SQL quoting
    return f"departamento = '{dept}'"


# ─── Claude Haiku — SQL generation ───────────────────────────────────────────

def _generate_sql(question: str, rls_where: str) -> str:
    """Call Claude Haiku to produce an Athena-compatible SQL query."""
    bedrock = boto3.client("bedrock-runtime", region_name=_AWS_REGION)

    rls_instruction = (
        f"\n\nIMPORTANTE: A consulta DEVE incluir o filtro WHERE {rls_where}"
        if rls_where
        else ""
    )

    system_prompt = (
        "Você é um especialista em SQL para Amazon Athena (Presto/Trino dialect).\n"
        "Gere APENAS a query SQL, sem explicações, sem markdown.\n"
        "Regras:\n"
        "- Apenas SELECT é permitido\n"
        "- Use LIMIT 50 no máximo\n"
        "- Use nomes de coluna simples (sem aspas)\n"
        f"- Database: {_GLUE_DATABASE}\n"
        f"Schema das tabelas disponíveis:\n{_SCHEMA_HINT}"
        + rls_instruction
    )

    messages = [{"role": "user", "content": question}]

    body = json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 512,
        "system": system_prompt,
        "messages": messages,
    })

    resp = bedrock.invoke_model(
        modelId=_MODEL_ID_HAIKU,
        body=body,
        contentType="application/json",
        accept="application/json",
    )
    result = json.loads(resp["body"].read())
    sql = result["content"][0]["text"].strip()

    # Strip markdown code fences if Haiku wrapped the query
    if sql.startswith("```"):
        sql = re.sub(r"```(?:sql)?", "", sql).strip().strip("`").strip()

    return sql


# ─── Athena execution ────────────────────────────────────────────────────────

def _run_athena_query(sql: str) -> list[dict]:
    """Execute sql in Athena and return results as a list of row dicts."""
    athena = boto3.client("athena", region_name=_AWS_REGION)

    start = athena.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": _GLUE_DATABASE},
        WorkGroup=_ATHENA_WORKGROUP,
        ResultConfiguration={"OutputLocation": _ATHENA_OUTPUT},
    )
    execution_id = start["QueryExecutionId"]
    logger.info("Athena query started: %s", execution_id)

    # Poll up to 30 seconds
    for _ in range(15):
        time.sleep(2)
        status_resp = athena.get_query_execution(QueryExecutionId=execution_id)
        state = status_resp["QueryExecution"]["Status"]["State"]
        if state == "SUCCEEDED":
            break
        if state in ("FAILED", "CANCELLED"):
            reason = status_resp["QueryExecution"]["Status"].get("StateChangeReason", "Unknown")
            raise RuntimeError(f"Athena query {state}: {reason}")

    # Fetch results
    results_resp = athena.get_query_results(
        QueryExecutionId=execution_id,
        MaxResults=51,  # +1 for header row
    )
    rows = results_resp.get("ResultSet", {}).get("Rows", [])
    if not rows:
        return []

    # First row is the column header
    headers = [c.get("VarCharValue", "") for c in rows[0]["Data"]]
    data_rows = []
    for row in rows[1:]:  # skip header
        values = [c.get("VarCharValue", "") for c in row["Data"]]
        data_rows.append(dict(zip(headers, values)))

    return data_rows


# ─── Lambda handler ───────────────────────────────────────────────────────────

def lambda_handler(event: dict, context) -> dict:
    try:
        function_name, params, session_attrs = parse_bedrock_event(event)
        user_ctx = extract_user_context(session_attrs)

        if function_name != "query_data_lake":
            return build_error_response(event, f"Função desconhecida: '{function_name}'")

        question: str = params.get("pergunta", "").strip()
        if not question:
            return build_error_response(event, "Parâmetro 'pergunta' é obrigatório.")

        logger.info("Data lake query: user=%s dept=%s question=%s",
                    user_ctx.upn, user_ctx.departamento, question[:120])

        # 1. Build RLS constraint
        rls_where = _build_rls_where(user_ctx)

        # 2. Generate SQL via Claude Haiku
        try:
            sql = _generate_sql(question, rls_where)
        except Exception as exc:
            logger.exception("SQL generation failed")
            return build_error_response(event, f"Não foi possível gerar a consulta SQL: {exc}")

        logger.info("Generated SQL: %s", sql)

        # 3. Safety check — reject mutations
        if not _is_safe_sql(sql):
            logger.error("Unsafe SQL generated: %s", sql)
            return build_error_response(
                event,
                "A consulta gerada contém operações não permitidas. Reformule sua pergunta."
            )

        # 4. Execute in Athena
        try:
            rows = _run_athena_query(sql)
        except RuntimeError as exc:
            return build_error_response(event, str(exc))
        except botocore.exceptions.ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code == "InvalidRequestException" and "BytesScannedCutoff" in str(exc):
                return build_error_response(
                    event,
                    "Consulta muito ampla — limite de dados varridos atingido (10 GB). "
                    "Adicione mais filtros à sua pergunta."
                )
            raise

        # 5. Return results
        result = {
            "sql_executado": sql,
            "total_linhas": len(rows),
            "resultados": rows[:50],
        }
        return build_bedrock_response(event, result)

    except Exception:
        logger.exception("Unhandled error in query_data_lake")
        return build_error_response(event, "Erro interno ao consultar o data lake.")
