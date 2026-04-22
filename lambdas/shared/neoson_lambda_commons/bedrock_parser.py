"""
lambdas/shared/neoson_lambda_commons/bedrock_parser.py

Standard Bedrock Action Group Lambda event parser and response builder.

All Neoson action-group Lambdas import from this module to ensure a single,
consistent contract with Bedrock Agent.

Event structure (Bedrock → Lambda):
{
  "messageVersion": "1.0",
  "agent": { "name": "...", "id": "...", "alias": "...", "version": "..." },
  "inputText": "...",
  "sessionId": "...",
  "actionGroup": "neoson-ti-actions",
  "function": "resetar_senha_usuario",
  "parameters": [
    {"name": "username", "type": "string", "value": "john.doe"}
  ],
  "sessionAttributes": { "departamento": "TI", "nivel_hierarquico": "2", ... },
  "promptSessionAttributes": {}
}

Response structure (Lambda → Bedrock):
{
  "messageVersion": "1.0",
  "response": {
    "actionGroup": "neoson-ti-actions",
    "function": "resetar_senha_usuario",
    "functionResponse": {
      "responseBody": {
        "TEXT": { "body": "<result string>" }
      }
    }
  },
  "sessionAttributes": { ... },
  "promptSessionAttributes": {}
}
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def parse_bedrock_event(event: dict) -> tuple[str, dict[str, Any], dict[str, str]]:
    """Extract function name, parameters dict and session attributes from a Bedrock
    Action Group Lambda event.

    Returns
    -------
    function_name : str
        Name of the function Bedrock wants to invoke (e.g. "resetar_senha_usuario").
    params : dict
        Parameters keyed by name with their string values.
    session_attrs : dict
        Session attributes forwarded from the Agent (departamento, nivel_hierarquico,
        oid, upn, etc.).
    """
    function_name: str = event.get("function", "")
    raw_params: list[dict] = event.get("parameters", [])
    session_attrs: dict[str, str] = event.get("sessionAttributes", {}) or {}

    params: dict[str, Any] = {}
    for p in raw_params:
        name = p.get("name", "")
        value = p.get("value", "")
        ptype = p.get("type", "string")
        # Coerce types so handlers can rely on proper Python types
        if ptype == "integer":
            try:
                params[name] = int(value)
            except (ValueError, TypeError):
                params[name] = value
        elif ptype == "number":
            try:
                params[name] = float(value)
            except (ValueError, TypeError):
                params[name] = value
        elif ptype == "boolean":
            params[name] = str(value).lower() in ("true", "1", "yes")
        else:
            params[name] = value

    logger.debug("Bedrock invocation: function=%s params=%s session=%s",
                 function_name, params, session_attrs)
    return function_name, params, session_attrs


def build_bedrock_response(
    event: dict,
    result: str | dict,
    *,
    updated_session_attrs: dict[str, str] | None = None,
) -> dict:
    """Build the Bedrock Action Group response envelope.

    Parameters
    ----------
    event : dict
        Original Lambda event (used to echo back actionGroup and function).
    result : str | dict
        The tool result — if a dict it will be JSON-serialised.
    updated_session_attrs : dict | None
        Optional updated session attributes to propagate back to the Agent.
    """
    if isinstance(result, dict):
        body = json.dumps(result, ensure_ascii=False)
    else:
        body = str(result)

    response = {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get("actionGroup", ""),
            "function": event.get("function", ""),
            "functionResponse": {
                "responseBody": {
                    "TEXT": {"body": body}
                }
            },
        },
        "sessionAttributes": updated_session_attrs or event.get("sessionAttributes", {}),
        "promptSessionAttributes": event.get("promptSessionAttributes", {}),
    }
    return response


def build_error_response(event: dict, error_message: str) -> dict:
    """Build a structured error response so the Agent can communicate failure gracefully.

    The body follows the convention: {"error": true, "message": "..."}
    """
    logger.error("Action group error: function=%s error=%s",
                 event.get("function"), error_message)
    result = json.dumps({"error": True, "message": error_message}, ensure_ascii=False)
    response = {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get("actionGroup", ""),
            "function": event.get("function", ""),
            "functionResponse": {
                "responseBody": {
                    "TEXT": {"body": result}
                }
            },
        },
        "sessionAttributes": event.get("sessionAttributes", {}),
        "promptSessionAttributes": event.get("promptSessionAttributes", {}),
    }
    return response
