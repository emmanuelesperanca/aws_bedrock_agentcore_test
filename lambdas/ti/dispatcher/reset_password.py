"""
lambdas/ti/dispatcher/reset_password.py

Tool: resetar_senha_usuario
Calls an internal Active Directory Self-Service Password Reset API.

Parameters (from Bedrock):
  username : str  — sAMAccountName or UPN of the target user
"""

from __future__ import annotations

import json
import logging
import os

import boto3
import httpx

from neoson_lambda_commons.auth_context import UserContext, require_level

logger = logging.getLogger(__name__)

_RESET_API_URL = os.environ.get("AD_RESET_API_URL", "")


def _get_api_key() -> str:
    """Retrieve AD Reset API key from Secrets Manager (cached per Lambda container)."""
    if not hasattr(_get_api_key, "_cached"):
        client = boto3.client("secretsmanager", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        secret = client.get_secret_value(SecretId="/neoson/ad_reset_api_key")
        _get_api_key._cached = secret["SecretString"]
    return _get_api_key._cached


def resetar_senha_usuario(params: dict, user_ctx: UserContext) -> str:
    """Reset a user's password via the corporate AD Self-Service API.

    Requires caller to be at hierarchy level ≥ 3 (Coordinator or above)
    OR the caller to be resetting their own password.
    """
    target_username: str = params.get("username", "").strip()
    if not target_username:
        return json.dumps({"erro": "Parâmetro 'username' é obrigatório."}, ensure_ascii=False)

    # Allow self-service reset (level 1+) or admin reset (level 3+)
    caller_upn = user_ctx.upn.lower()
    target_lower = target_username.lower()
    is_self_reset = target_lower == caller_upn or target_lower == caller_upn.split("@")[0]
    if not is_self_reset:
        require_level(user_ctx, 3)

    if not _RESET_API_URL:
        logger.warning("AD_RESET_API_URL not configured — returning stub response")
        return json.dumps({
            "sucesso": True,
            "mensagem": f"[SIMULAÇÃO] Senha do usuário '{target_username}' redefinida com sucesso.",
            "ticket": "INC0000001",
        }, ensure_ascii=False)

    try:
        api_key = _get_api_key()
        resp = httpx.post(
            _RESET_API_URL,
            json={"username": target_username, "requestedBy": user_ctx.upn},
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return json.dumps({
            "sucesso": True,
            "mensagem": f"Senha redefinida com sucesso para '{target_username}'.",
            "ticket": data.get("ticketNumber", "N/A"),
        }, ensure_ascii=False)
    except httpx.HTTPStatusError as exc:
        logger.error("AD Reset API error: %s", exc.response.text)
        return json.dumps({
            "erro": True,
            "mensagem": f"API de redefinição retornou erro {exc.response.status_code}.",
        }, ensure_ascii=False)
    except Exception as exc:
        logger.exception("Unexpected error in resetar_senha_usuario")
        return json.dumps({
            "erro": True,
            "mensagem": "Erro inesperado ao redefinir senha.",
        }, ensure_ascii=False)
