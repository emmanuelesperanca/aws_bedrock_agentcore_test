"""
lambdas/ti/dispatcher/check_disk_space.py

Tool: verificar_espaco_disco
Checks disk space on a server or storage volume.

Parameters (from Bedrock):
  servidor   : str  — hostname or IP of the server
  volume     : str  — mount point or drive letter, e.g. "/", "C:", "/data"  (optional)
"""

from __future__ import annotations

import json
import logging
import os

import boto3
import httpx

from neoson_lambda_commons.auth_context import UserContext

logger = logging.getLogger(__name__)

_DISK_API_URL = os.environ.get("DISK_API_URL", "")
_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _cloudwatch_disk_metrics(servidor: str, volume: str) -> dict:
    """Query CloudWatch for EBS disk space metrics (requires CloudWatch Agent installed)."""
    try:
        cw = boto3.client("cloudwatch", region_name=_AWS_REGION)
        import datetime
        end = datetime.datetime.utcnow()
        start = end - datetime.timedelta(minutes=15)

        dimensions = [
            {"Name": "InstanceId", "Value": servidor},
            {"Name": "path", "Value": volume or "/"},
            {"Name": "fstype", "Value": "xfs"},
        ]
        resp = cw.get_metric_statistics(
            Namespace="CWAgent",
            MetricName="disk_used_percent",
            Dimensions=dimensions,
            StartTime=start,
            EndTime=end,
            Period=300,
            Statistics=["Average"],
        )
        datapoints = sorted(resp.get("Datapoints", []), key=lambda d: d["Timestamp"])
        if not datapoints:
            return {"servidor": servidor, "volume": volume or "/",
                    "status": "SEM_DADOS",
                    "mensagem": "Nenhum dado de disco encontrado. Verifique se o CloudWatch Agent está instalado."}
        latest = datapoints[-1]
        pct = round(latest["Average"], 1)
        status = "CRÍTICO" if pct >= 90 else ("ATENÇÃO" if pct >= 75 else "OK")
        return {
            "servidor": servidor,
            "volume": volume or "/",
            "uso_percentual": pct,
            "status": status,
            "timestamp": latest["Timestamp"].isoformat(),
        }
    except Exception as exc:
        logger.exception("CloudWatch disk metrics error")
        return {"erro": True, "mensagem": str(exc)}


def verificar_espaco_disco(params: dict, user_ctx: UserContext) -> str:
    servidor: str = params.get("servidor", "").strip()
    volume: str = params.get("volume", "").strip()

    if not servidor:
        return json.dumps({"erro": "Parâmetro 'servidor' é obrigatório."}, ensure_ascii=False)

    logger.info("Checking disk space: server=%s volume=%s user=%s", servidor, volume, user_ctx.upn)

    if not _DISK_API_URL:
        result = _cloudwatch_disk_metrics(servidor, volume)
        return json.dumps(result, ensure_ascii=False)

    try:
        resp = httpx.get(
            f"{_DISK_API_URL}/disk",
            params={"host": servidor, "mount": volume or "/"},
            timeout=10.0,
        )
        resp.raise_for_status()
        return json.dumps(resp.json(), ensure_ascii=False)
    except Exception as exc:
        logger.exception("Disk API error")
        return json.dumps({"erro": True, "mensagem": str(exc)}, ensure_ascii=False)
