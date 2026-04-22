"""
lambdas/rh/dispatcher/get_benefits.py

Tool: consultar_beneficios
Returns the list of active benefits for the employee (health plan, meal voucher, etc.).

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

# Stub benefit data for when RH API is not configured
_STUB_BENEFITS = [
    {"beneficio": "Plano de Saúde", "operadora": "Unimed", "plano": "Executivo", "status": "ATIVO"},
    {"beneficio": "Vale Refeição", "valor_diario": "R$ 45,00", "status": "ATIVO"},
    {"beneficio": "Vale Transporte", "valor_mensal": "R$ 330,00", "status": "ATIVO"},
    {"beneficio": "Seguro de Vida", "cobertura": "R$ 200.000,00", "status": "ATIVO"},
    {"beneficio": "Odontológico", "operadora": "OdontoPrev", "plano": "Básico", "status": "ATIVO"},
]


def _get_rh_key() -> str:
    if not hasattr(_get_rh_key, "_cached"):
        client = boto3.client("secretsmanager", region_name=_AWS_REGION)
        secret = client.get_secret_value(SecretId="/neoson/rh_api_key")
        _get_rh_key._cached = secret["SecretString"]
    return _get_rh_key._cached


def consultar_beneficios(params: dict, user_ctx: UserContext) -> str:
    matricula: str = params.get("matricula", "").strip()

    if matricula and matricula != user_ctx.oid:
        require_level(user_ctx, 4)

    employee_ref = matricula or user_ctx.oid or user_ctx.upn

    if not _RH_API_URL:
        logger.warning("RH_API_URL not configured — returning stub benefits")
        return json.dumps({
            "matricula": employee_ref,
            "beneficios": _STUB_BENEFITS,
            "mensagem": "[SIMULAÇÃO] Dados não conectados ao sistema de RH real.",
        }, ensure_ascii=False)

    try:
        api_key = _get_rh_key()
        resp = httpx.get(
            f"{_RH_API_URL}/employees/{employee_ref}/benefits",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return json.dumps({
            "matricula": employee_ref,
            "beneficios": data.get("benefits", []),
        }, ensure_ascii=False)
    except Exception as exc:
        logger.exception("RH API error in consultar_beneficios")
        return json.dumps({"erro": True, "mensagem": str(exc)}, ensure_ascii=False)
