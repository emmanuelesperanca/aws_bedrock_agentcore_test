"""
lambdas/ti/dispatcher/manage_vpn.py

Tool: gerenciar_acesso_vpn
Enable or disable VPN access for a user.
Requires hierarchy level ≥ 3 (Coordinator / IT Manager).

Parameters (from Bedrock):
  username : str  — UPN or sAMAccountName of target user
  acao     : str  — "habilitar" | "desabilitar"
"""

from __future__ import annotations

import json
import logging
import os

import boto3
import httpx

from neoson_lambda_commons.auth_context import UserContext, require_level

logger = logging.getLogger(__name__)

_VPN_API_URL = os.environ.get("VPN_API_URL", "")
_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _get_vpn_key() -> str:
    if not hasattr(_get_vpn_key, "_cached"):
        client = boto3.client("secretsmanager", region_name=_AWS_REGION)
        secret = client.get_secret_value(SecretId="/neoson/vpn_api_key")
        _get_vpn_key._cached = secret["SecretString"]
    return _get_vpn_key._cached


def gerenciar_acesso_vpn(params: dict, user_ctx: UserContext) -> str:
    username: str = params.get("username", "").strip()
    acao: str = params.get("acao", "").strip().lower()

    if not username:
        return json.dumps({"erro": "Parâmetro 'username' é obrigatório."}, ensure_ascii=False)
    if acao not in ("habilitar", "desabilitar"):
        return json.dumps({
            "erro": "Parâmetro 'acao' deve ser 'habilitar' ou 'desabilitar'."
        }, ensure_ascii=False)

    # Enforce: only coordinators and above can manage VPN access for others
    require_level(user_ctx, 3)

    logger.info("VPN management: user=%s action=%s requested_by=%s",
                username, acao, user_ctx.upn)

    if not _VPN_API_URL:
        logger.warning("VPN_API_URL not configured — returning stub response")
        action_pt = "habilitado" if acao == "habilitar" else "desabilitado"
        return json.dumps({
            "sucesso": True,
            "mensagem": f"[SIMULAÇÃO] Acesso VPN {action_pt} para '{username}'.",
            "solicitante": user_ctx.upn,
        }, ensure_ascii=False)

    try:
        api_key = _get_vpn_key()
        enable = acao == "habilitar"
        resp = httpx.post(
            f"{_VPN_API_URL}/access",
            json={"username": username, "enable": enable, "requestedBy": user_ctx.upn},
            headers={"X-API-Key": api_key},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        action_pt = "habilitado" if enable else "desabilitado"
        return json.dumps({
            "sucesso": True,
            "mensagem": f"Acesso VPN {action_pt} para '{username}'.",
            "ticket": data.get("ticketNumber", "N/A"),
        }, ensure_ascii=False)
    except PermissionError:
        raise
    except Exception as exc:
        logger.exception("VPN API error")
        return json.dumps({"erro": True, "mensagem": str(exc)}, ensure_ascii=False)
