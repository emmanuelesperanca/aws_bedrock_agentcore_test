"""
lambdas/ti/dispatcher/backup_status.py

Tool: backup_status
Returns the status of the last backup job for a given server or backup policy.
Queries AWS Backup or an external backup management platform (Veeam, Commvault).

Parameters (from Bedrock):
  servidor      : str  — hostname, resource ARN, or backup job resource ID
  tipo_recurso  : str  — "ec2" | "rds" | "efs" | "s3" | "on-premises"  (default: "ec2")
"""

from __future__ import annotations

import json
import logging
import os

import boto3
import httpx

from neoson_lambda_commons.auth_context import UserContext

logger = logging.getLogger(__name__)

_BACKUP_API_URL = os.environ.get("BACKUP_API_URL", "")
_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")


def _aws_backup_status(servidor: str, tipo_recurso: str) -> dict:
    """Query AWS Backup for the latest backup job of a resource."""
    try:
        backup = boto3.client("backup", region_name=_AWS_REGION)
        # List backup jobs for this resource (most recent first)
        resp = backup.list_backup_jobs(
            ByResourceType=tipo_recurso.upper() if tipo_recurso != "on-premises" else "EC2",
            MaxResults=5,
        )
        jobs = resp.get("BackupJobs", [])
        # Filter by resource ARN or resource ID substring
        matching = [
            j for j in jobs
            if servidor.lower() in j.get("ResourceArn", "").lower()
            or servidor.lower() in j.get("BackupJobId", "").lower()
        ]
        if not matching:
            return {
                "servidor": servidor,
                "status": "NÃO_ENCONTRADO",
                "mensagem": f"Nenhum job de backup encontrado para '{servidor}'.",
            }
        job = matching[0]
        return {
            "servidor": servidor,
            "job_id": job.get("BackupJobId"),
            "status": job.get("State"),
            "data_inicio": str(job.get("StartBy", "N/A")),
            "data_conclusao": str(job.get("CompletionDate", "Em andamento")),
            "tamanho_bytes": job.get("BackupSizeInBytes", 0),
            "vault": job.get("BackupVaultName", "N/A"),
        }
    except Exception as exc:
        logger.exception("AWS Backup query error for %s", servidor)
        return {"erro": True, "mensagem": str(exc)}


def backup_status(params: dict, user_ctx: UserContext) -> str:
    servidor: str = params.get("servidor", "").strip()
    tipo_recurso: str = params.get("tipo_recurso", "ec2").strip().lower()

    if not servidor:
        return json.dumps({"erro": "Parâmetro 'servidor' é obrigatório."}, ensure_ascii=False)

    logger.info("Backup status check: server=%s type=%s user=%s",
                servidor, tipo_recurso, user_ctx.upn)

    if not _BACKUP_API_URL:
        result = _aws_backup_status(servidor, tipo_recurso)
        return json.dumps(result, ensure_ascii=False)

    try:
        resp = httpx.get(
            f"{_BACKUP_API_URL}/status",
            params={"resource": servidor, "type": tipo_recurso},
            timeout=10.0,
        )
        resp.raise_for_status()
        return json.dumps(resp.json(), ensure_ascii=False)
    except Exception as exc:
        logger.exception("Backup API error")
        return json.dumps({"erro": True, "mensagem": str(exc)}, ensure_ascii=False)
