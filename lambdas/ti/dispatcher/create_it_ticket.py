"""
lambdas/ti/dispatcher/create_it_ticket.py

Tool: criar_chamado_ti
Creates a new IT service desk ticket in ServiceNow / JIRA Service Desk.

Parameters (from Bedrock):
  titulo      : str  — short description / summary of the issue
  descricao   : str  — detailed description
  prioridade  : str  — "baixa" | "media" | "alta" | "critica"  (default: "media")
  categoria   : str  — e.g. "hardware", "software", "rede", "acesso"  (default: "geral")
"""

from __future__ import annotations

import json
import logging
import os

import boto3
import httpx

from neoson_lambda_commons.auth_context import UserContext

logger = logging.getLogger(__name__)

_ITSM_URL = os.environ.get("ITSM_API_URL", "")
_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

_PRIORITY_MAP = {
    "critica": "1",
    "alta": "2",
    "media": "3",
    "baixa": "4",
}


def _get_itsm_key() -> str:
    if not hasattr(_get_itsm_key, "_cached"):
        client = boto3.client("secretsmanager", region_name=_AWS_REGION)
        secret = client.get_secret_value(SecretId="/neoson/itsm_api_key")
        _get_itsm_key._cached = secret["SecretString"]
    return _get_itsm_key._cached


def criar_chamado_ti(params: dict, user_ctx: UserContext) -> str:
    titulo: str = params.get("titulo", "").strip()
    descricao: str = params.get("descricao", "").strip()
    prioridade: str = params.get("prioridade", "media").strip().lower()
    categoria: str = params.get("categoria", "geral").strip().lower()

    if not titulo:
        return json.dumps({"erro": "Parâmetro 'titulo' é obrigatório."}, ensure_ascii=False)

    sn_priority = _PRIORITY_MAP.get(prioridade, "3")

    if not _ITSM_URL:
        import uuid
        ticket_id = f"INC{uuid.uuid4().hex[:6].upper()}"
        logger.warning("ITSM_API_URL not configured — returning stub ticket")
        return json.dumps({
            "sucesso": True,
            "ticket_id": ticket_id,
            "mensagem": f"[SIMULAÇÃO] Chamado '{titulo}' aberto com sucesso. ID: {ticket_id}",
            "prioridade": prioridade,
            "categoria": categoria,
            "solicitante": user_ctx.upn,
        }, ensure_ascii=False)

    try:
        api_key = _get_itsm_key()
        payload = {
            "short_description": titulo,
            "description": descricao or titulo,
            "priority": sn_priority,
            "category": categoria,
            "caller_id": user_ctx.upn,
            "department": user_ctx.departamento,
        }
        resp = httpx.post(
            f"{_ITSM_URL}/api/now/table/incident",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json().get("result", {})
        return json.dumps({
            "sucesso": True,
            "ticket_id": data.get("number", "N/A"),
            "mensagem": f"Chamado aberto com sucesso: {data.get('number', 'N/A')}",
            "sys_id": data.get("sys_id", ""),
        }, ensure_ascii=False)
    except Exception as exc:
        logger.exception("ITSM API error")
        return json.dumps({"erro": True, "mensagem": str(exc)}, ensure_ascii=False)
