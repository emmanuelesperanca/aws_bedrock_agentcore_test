"""
lambdas/rh/dispatcher/get_hours_bank.py

Tool: consultar_banco_horas
Returns the employee's current hours-bank balance.

Parameters (from Bedrock):
  matricula : str  — employee ID (optional — defaults to caller)
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


def consultar_banco_horas(params: dict, user_ctx: UserContext) -> str:
    matricula: str = params.get("matricula", "").strip()

    if matricula and matricula != user_ctx.oid:
        require_level(user_ctx, 4)

    employee_ref = matricula or user_ctx.oid or user_ctx.upn

    if not _RH_API_URL:
        logger.warning("RH_API_URL not configured — returning stub response")
        return json.dumps({
            "matricula": employee_ref,
            "saldo_horas": "+12:30",
            "status": "POSITIVO",
            "mensagem": "[SIMULAÇÃO] Banco de horas não conectado ao sistema real.",
        }, ensure_ascii=False)

    try:
        api_key = _get_rh_key()
        resp = httpx.get(
            f"{_RH_API_URL}/employees/{employee_ref}/hours-bank",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        balance_minutes = data.get("balanceMinutes", 0)
        sign = "+" if balance_minutes >= 0 else "-"
        h, m = divmod(abs(balance_minutes), 60)
        return json.dumps({
            "matricula": employee_ref,
            "saldo_horas": f"{sign}{h:02d}:{m:02d}",
            "status": "POSITIVO" if balance_minutes >= 0 else "NEGATIVO",
        }, ensure_ascii=False)
    except Exception as exc:
        logger.exception("RH API error in consultar_banco_horas")
        return json.dumps({"erro": True, "mensagem": str(exc)}, ensure_ascii=False)
