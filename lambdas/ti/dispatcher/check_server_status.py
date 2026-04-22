"""
lambdas/ti/dispatcher/check_server_status.py

Tool: verificar_status_servidor
Queries server / infrastructure status from the corporate monitoring platform
(Zabbix, Datadog, or CloudWatch — configurable via MONITORING_API_URL).

Parameters (from Bedrock):
  servidor : str  — hostname, IP, or FQDN of the server to check
"""

from __future__ import annotations

import json
import logging
import os

import boto3
import httpx

from neoson_lambda_commons.auth_context import UserContext

logger = logging.getLogger(__name__)

_MONITORING_URL = os.environ.get("MONITORING_API_URL", "")
_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _cloudwatch_fallback(servidor: str) -> dict:
    """If no external monitoring API, query CloudWatch Metrics for basic EC2 health."""
    try:
        ec2 = boto3.client("ec2", region_name=_AWS_REGION)
        resp = ec2.describe_instance_status(
            Filters=[{"Name": "private-dns-name", "Values": [servidor]}],
            IncludeAllInstances=True,
        )
        statuses = resp.get("InstanceStatuses", [])
        if not statuses:
            return {"servidor": servidor, "status": "NÃO_ENCONTRADO",
                    "mensagem": f"Servidor '{servidor}' não encontrado na nuvem."}
        item = statuses[0]
        state = item["InstanceState"]["Name"]
        system_status = item["SystemStatus"]["Status"]
        instance_status = item["InstanceStatus"]["Status"]
        return {
            "servidor": servidor,
            "instance_id": item["InstanceId"],
            "estado": state,
            "system_check": system_status,
            "instance_check": instance_status,
            "status": "OK" if system_status == "ok" and instance_status == "ok" else "DEGRADADO",
        }
    except Exception as exc:
        logger.exception("CloudWatch fallback error for server %s", servidor)
        return {"servidor": servidor, "status": "ERRO", "mensagem": str(exc)}


def verificar_status_servidor(params: dict, user_ctx: UserContext) -> str:
    servidor: str = params.get("servidor", "").strip()
    if not servidor:
        return json.dumps({"erro": "Parâmetro 'servidor' é obrigatório."}, ensure_ascii=False)

    logger.info("Checking server status: %s (requested by %s)", servidor, user_ctx.upn)

    if not _MONITORING_URL:
        result = _cloudwatch_fallback(servidor)
        return json.dumps(result, ensure_ascii=False)

    try:
        resp = httpx.get(
            f"{_MONITORING_URL}/hosts",
            params={"host": servidor, "output": "extend"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data:
            return json.dumps({"servidor": servidor, "status": "NÃO_ENCONTRADO"}, ensure_ascii=False)
        host = data[0]
        return json.dumps({
            "servidor": servidor,
            "status": "DISPONÍVEL" if host.get("available") == "1" else "INDISPONÍVEL",
            "ultimo_check": host.get("lastcheck", "N/A"),
            "error": host.get("error", ""),
        }, ensure_ascii=False)
    except Exception as exc:
        logger.exception("Monitoring API error for server %s", servidor)
        return json.dumps({"erro": True, "mensagem": str(exc)}, ensure_ascii=False)
