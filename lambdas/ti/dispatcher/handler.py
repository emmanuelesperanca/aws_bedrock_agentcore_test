"""
lambdas/ti/dispatcher/handler.py

TI (IT) Action Group Lambda dispatcher for Neoson.

Routes Bedrock Agent function calls to individual tool modules.
All six tools from the original tools/mcp/ti_tools.py are implemented here
as standalone functions that hit real backend APIs / AWS services.

Environment variables required:
  AD_RESET_API_URL   — internal AD/Entra self-service endpoint for password resets
  AD_RESET_API_KEY   — API key stored in Secrets Manager (see get_secret below)
  MONITORING_API_URL — Zabbix / CloudWatch API URL for server status
  ITSM_API_URL       — ServiceNow / JIRA Service Desk endpoint for ticket creation
  ITSM_API_KEY       — ITSM API key (Secrets Manager)
  DISK_API_URL       — Inventory / monitoring API for disk-space queries
  VPN_API_URL        — Cisco ASA / Palo Alto API for VPN access management
  BACKUP_API_URL     — Veeam / AWS Backup status endpoint
  LOG_LEVEL          — default INFO
"""

import json
import logging
import os
import sys

# Shared commons are deployed as a Lambda Layer — path is prepended by CDK/SAM
sys.path.insert(0, "/opt/python")

from neoson_lambda_commons import (
    parse_bedrock_event,
    build_bedrock_response,
    build_error_response,
    extract_user_context,
    require_department,
)

from reset_password import resetar_senha_usuario
from check_server_status import verificar_status_servidor
from create_it_ticket import criar_chamado_ti
from check_disk_space import verificar_espaco_disco
from manage_vpn import gerenciar_acesso_vpn
from backup_status import backup_status

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

# Allowed departments for TI tools
_TI_ALLOWED = {"TI", "INFRAESTRUTURA", "DESENVOLVIMENTO", "HELPDESK", "SEGURANÇA"}

_ROUTER = {
    "resetar_senha_usuario": resetar_senha_usuario,
    "verificar_status_servidor": verificar_status_servidor,
    "criar_chamado_ti": criar_chamado_ti,
    "verificar_espaco_disco": verificar_espaco_disco,
    "gerenciar_acesso_vpn": gerenciar_acesso_vpn,
    "backup_status": backup_status,
}


def lambda_handler(event: dict, context) -> dict:
    try:
        function_name, params, session_attrs = parse_bedrock_event(event)
        user_ctx = extract_user_context(session_attrs)

        logger.info("TI dispatcher: function=%s user=%s dept=%s",
                    function_name, user_ctx.upn, user_ctx.departamento)

        # Enforce department-level access control on TI tools
        require_department(user_ctx, _TI_ALLOWED)

        handler_fn = _ROUTER.get(function_name)
        if handler_fn is None:
            return build_error_response(
                event, f"Função desconhecida: '{function_name}'"
            )

        result = handler_fn(params, user_ctx)
        return build_bedrock_response(event, result)

    except PermissionError as exc:
        return build_error_response(event, str(exc))
    except Exception as exc:
        logger.exception("Unhandled error in TI dispatcher")
        return build_error_response(
            event, "Erro interno no processamento da solicitação de TI."
        )
