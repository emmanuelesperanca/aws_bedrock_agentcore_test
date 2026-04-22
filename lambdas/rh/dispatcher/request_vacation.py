"""
lambdas/rh/dispatcher/request_vacation.py

Tool: solicitar_ferias
Submits a vacation request on behalf of the employee.

Parameters (from Bedrock):
  data_inicio   : str  — start date in ISO-8601 format (YYYY-MM-DD)
  data_fim      : str  — end date in ISO-8601 format (YYYY-MM-DD)
  observacao    : str  — optional note for the approver
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, date

import boto3
import httpx

from neoson_lambda_commons.auth_context import UserContext

logger = logging.getLogger(__name__)

_RH_API_URL = os.environ.get("RH_API_URL", "")
_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _get_rh_key() -> str:
    if not hasattr(_get_rh_key, "_cached"):
        client = boto3.client("secretsmanager", region_name=_AWS_REGION)
        secret = client.get_secret_value(SecretId="/neoson/rh_api_key")
        _get_rh_key._cached = secret["SecretString"]
    return _get_rh_key._cached


def _parse_date(s: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def solicitar_ferias(params: dict, user_ctx: UserContext) -> str:
    raw_inicio: str = params.get("data_inicio", "").strip()
    raw_fim: str = params.get("data_fim", "").strip()
    observacao: str = params.get("observacao", "").strip()

    if not raw_inicio or not raw_fim:
        return json.dumps({
            "erro": "Parâmetros 'data_inicio' e 'data_fim' são obrigatórios (formato YYYY-MM-DD)."
        }, ensure_ascii=False)

    dt_inicio = _parse_date(raw_inicio)
    dt_fim = _parse_date(raw_fim)

    if dt_inicio is None or dt_fim is None:
        return json.dumps({"erro": "Data inválida. Use o formato YYYY-MM-DD."}, ensure_ascii=False)

    if dt_fim < dt_inicio:
        return json.dumps({"erro": "Data de fim não pode ser anterior à data de início."}, ensure_ascii=False)

    duracao = (dt_fim - dt_inicio).days + 1

    if not _RH_API_URL:
        import uuid
        req_id = f"FER{uuid.uuid4().hex[:6].upper()}"
        logger.warning("RH_API_URL not configured — returning stub response")
        return json.dumps({
            "sucesso": True,
            "solicitacao_id": req_id,
            "data_inicio": str(dt_inicio),
            "data_fim": str(dt_fim),
            "dias_solicitados": duracao,
            "status": "PENDENTE_APROVACAO",
            "mensagem": f"[SIMULAÇÃO] Férias solicitadas de {dt_inicio} a {dt_fim} ({duracao} dias).",
        }, ensure_ascii=False)

    try:
        api_key = _get_rh_key()
        payload = {
            "employeeId": user_ctx.oid or user_ctx.upn,
            "startDate": str(dt_inicio),
            "endDate": str(dt_fim),
            "notes": observacao,
            "requestedByUpn": user_ctx.upn,
        }
        resp = httpx.post(
            f"{_RH_API_URL}/vacation-requests",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return json.dumps({
            "sucesso": True,
            "solicitacao_id": data.get("requestId", "N/A"),
            "data_inicio": str(dt_inicio),
            "data_fim": str(dt_fim),
            "dias_solicitados": duracao,
            "status": data.get("status", "PENDENTE_APROVACAO"),
        }, ensure_ascii=False)
    except Exception as exc:
        logger.exception("RH API error in solicitar_ferias")
        return json.dumps({"erro": True, "mensagem": str(exc)}, ensure_ascii=False)
