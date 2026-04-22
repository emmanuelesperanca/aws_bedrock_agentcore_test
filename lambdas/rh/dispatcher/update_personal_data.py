"""
lambdas/rh/dispatcher/update_personal_data.py

Tool: atualizar_dados_pessoais
Updates personal data fields for the employee (address, phone, emergency contact).

Only safe, self-service fields are allowed via this tool.
CPF, name, bank account changes require HR department workflow and are not exposed here.

Parameters (from Bedrock):
  campo  : str  — field to update: "endereco" | "telefone" | "contato_emergencia" | "email_pessoal"
  valor  : str  — new value
"""

from __future__ import annotations

import json
import logging
import os

import boto3
import httpx

from neoson_lambda_commons.auth_context import UserContext

logger = logging.getLogger(__name__)

_RH_API_URL = os.environ.get("RH_API_URL", "")
_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Only these fields may be changed via the self-service tool
_ALLOWED_FIELDS = {"endereco", "telefone", "contato_emergencia", "email_pessoal"}

# Field name mapping: PT-BR label → HR API field name
_FIELD_MAP = {
    "endereco": "address",
    "telefone": "mobilePhone",
    "contato_emergencia": "emergencyContact",
    "email_pessoal": "personalEmail",
}


def _get_rh_key() -> str:
    if not hasattr(_get_rh_key, "_cached"):
        client = boto3.client("secretsmanager", region_name=_AWS_REGION)
        secret = client.get_secret_value(SecretId="/neoson/rh_api_key")
        _get_rh_key._cached = secret["SecretString"]
    return _get_rh_key._cached


def atualizar_dados_pessoais(params: dict, user_ctx: UserContext) -> str:
    campo: str = params.get("campo", "").strip().lower()
    valor: str = params.get("valor", "").strip()

    if not campo:
        return json.dumps({"erro": "Parâmetro 'campo' é obrigatório."}, ensure_ascii=False)
    if not valor:
        return json.dumps({"erro": "Parâmetro 'valor' é obrigatório."}, ensure_ascii=False)
    if campo not in _ALLOWED_FIELDS:
        return json.dumps({
            "erro": f"Campo '{campo}' não pode ser alterado por autoatendimento. "
                    f"Campos permitidos: {', '.join(sorted(_ALLOWED_FIELDS))}."
        }, ensure_ascii=False)

    api_field = _FIELD_MAP[campo]
    employee_ref = user_ctx.oid or user_ctx.upn

    logger.info("Personal data update: employee=%s field=%s requested_by=%s",
                employee_ref, campo, user_ctx.upn)

    if not _RH_API_URL:
        logger.warning("RH_API_URL not configured — returning stub response")
        return json.dumps({
            "sucesso": True,
            "campo_atualizado": campo,
            "mensagem": f"[SIMULAÇÃO] Campo '{campo}' seria atualizado para: '{valor}'.",
            "aviso": "Nenhuma alteração real foi feita (sistema não conectado).",
        }, ensure_ascii=False)

    try:
        api_key = _get_rh_key()
        payload = {api_field: valor, "updatedByUpn": user_ctx.upn}
        resp = httpx.patch(
            f"{_RH_API_URL}/employees/{employee_ref}",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10.0,
        )
        resp.raise_for_status()
        return json.dumps({
            "sucesso": True,
            "campo_atualizado": campo,
            "mensagem": f"Campo '{campo}' atualizado com sucesso.",
        }, ensure_ascii=False)
    except Exception as exc:
        logger.exception("RH API error in atualizar_dados_pessoais")
        return json.dumps({"erro": True, "mensagem": str(exc)}, ensure_ascii=False)
