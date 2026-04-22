"""
lambdas/rh/dispatcher/get_vacation_balance.py

Tool: consultar_saldo_ferias
Returns the employee's current vacation balance from the HR system.

Parameters (from Bedrock):
  matricula : str  — employee ID (optional — defaults to the logged-in user's oid)
"""

from __future__ import annotations

import json
import logging
import os

import boto3
import httpx

from neoson_lambda_commons.auth_context import UserContext, require_level

logger = logging.getLogger(__name__)

_RH_API_URL = os.environ.get("RH_API_URL", "")
_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _get_rh_key() -> str:
    if not hasattr(_get_rh_key, "_cached"):
        client = boto3.client("secretsmanager", region_name=_AWS_REGION)
        secret = client.get_secret_value(SecretId="/neoson/rh_api_key")
        _get_rh_key._cached = secret["SecretString"]
    return _get_rh_key._cached


def consultar_saldo_ferias(params: dict, user_ctx: UserContext) -> str:
    matricula: str = params.get("matricula", "").strip()

    # If querying another employee's balance, require manager level
    if matricula and matricula != user_ctx.oid:
        require_level(user_ctx, 4)

    # Default to caller's own identity
    employee_ref = matricula or user_ctx.oid or user_ctx.upn

    if not _RH_API_URL:
        logger.warning("RH_API_URL not configured — returning stub response")
        return json.dumps({
            "matricula": employee_ref,
            "saldo_ferias_dias": 18,
            "ferias_vencidas_dias": 0,
            "proximo_periodo": "2025-11-01",
            "mensagem": "[SIMULAÇÃO] Dados de férias não conectados ao sistema de RH real.",
        }, ensure_ascii=False)

    try:
        api_key = _get_rh_key()
        resp = httpx.get(
            f"{_RH_API_URL}/employees/{employee_ref}/vacation-balance",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return json.dumps({
            "matricula": employee_ref,
            "saldo_ferias_dias": data.get("balanceDays", 0),
            "ferias_vencidas_dias": data.get("expiredDays", 0),
            "proximo_periodo": data.get("nextPeriodStart", "N/A"),
        }, ensure_ascii=False)
    except Exception as exc:
        logger.exception("RH API error in consultar_saldo_ferias")
        return json.dumps({"erro": True, "mensagem": str(exc)}, ensure_ascii=False)
