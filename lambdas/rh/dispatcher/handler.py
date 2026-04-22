"""
lambdas/rh/dispatcher/handler.py

RH (Human Resources) Action Group Lambda dispatcher for Neoson.

Routes Bedrock Agent function calls to individual RH tool modules.
All five tools from the original tools/mcp/rh_tools.py are implemented here.

Environment variables required:
  RH_API_URL       — SAP SuccessFactors / ADP / Totvs HR API base URL
  RH_API_KEY       — API key in Secrets Manager (/neoson/rh_api_key)
  LOG_LEVEL        — default INFO
"""

import json
import logging
import os
import sys

sys.path.insert(0, "/opt/python")

from neoson_lambda_commons import (
    parse_bedrock_event,
    build_bedrock_response,
    build_error_response,
    extract_user_context,
)

from get_vacation_balance import consultar_saldo_ferias
from get_hours_bank import consultar_banco_horas
from request_vacation import solicitar_ferias
from get_benefits import consultar_beneficios
from update_personal_data import atualizar_dados_pessoais

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

_ROUTER = {
    "consultar_saldo_ferias": consultar_saldo_ferias,
    "consultar_banco_horas": consultar_banco_horas,
    "solicitar_ferias": solicitar_ferias,
    "consultar_beneficios": consultar_beneficios,
    "atualizar_dados_pessoais": atualizar_dados_pessoais,
}


def lambda_handler(event: dict, context) -> dict:
    try:
        function_name, params, session_attrs = parse_bedrock_event(event)
        user_ctx = extract_user_context(session_attrs)

        logger.info("RH dispatcher: function=%s user=%s", function_name, user_ctx.upn)

        handler_fn = _ROUTER.get(function_name)
        if handler_fn is None:
            return build_error_response(
                event, f"Função de RH desconhecida: '{function_name}'"
            )

        result = handler_fn(params, user_ctx)
        return build_bedrock_response(event, result)

    except PermissionError as exc:
        return build_error_response(event, str(exc))
    except Exception:
        logger.exception("Unhandled error in RH dispatcher")
        return build_error_response(
            event, "Erro interno ao processar solicitação de RH."
        )
