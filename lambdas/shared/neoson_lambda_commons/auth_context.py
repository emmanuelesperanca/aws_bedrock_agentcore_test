"""
lambdas/shared/neoson_lambda_commons/auth_context.py

Helpers to extract and validate the Neoson user context forwarded via
Bedrock Agent sessionAttributes.

When the Supervisor (neoson_agentcore/supervisor/main.py) invokes an Action Group,
it propagates the user profile into sessionAttributes so that Lambda tools can enforce
RLS and create audit trails without re-validating the JWT.

Session attributes injected by the Supervisor:
  - departamento:        e.g. "TI", "RH", "PRODUCAO"
  - nivel_hierarquico:   "1" through "5" (string — Bedrock SA values are always strings)
  - cargo:               e.g. "Analista de TI"
  - oid:                 Entra ID Object ID — used for audit logs
  - upn:                 user@straumann.com — human-readable audit identity
  - geografia:           "BR" or "GLOBAL"
  - tenant_id:           Entra ID tenant GUID
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Departments that may access TI tools
_TI_DEPARTMENTS = {"TI", "INFRAESTRUTURA", "DESENVOLVIMENTO", "HELPDESK"}
# Departments that may access RH tools
_RH_DEPARTMENTS = {"RH", "RECURSOS_HUMANOS", "RECURSOS HUMANOS"}


@dataclass
class UserContext:
    departamento: str = "DESCONHECIDO"
    nivel_hierarquico: int = 1
    cargo: str = ""
    oid: str = ""
    upn: str = "anonimo"
    geografia: str = "BR"
    tenant_id: str = ""

    # Derived helpers
    @property
    def is_global(self) -> bool:
        return self.geografia == "GLOBAL"

    @property
    def is_manager(self) -> bool:
        return self.nivel_hierarquico >= 4

    @property
    def department_upper(self) -> str:
        return self.departamento.upper()


def extract_user_context(session_attrs: dict[str, str]) -> UserContext:
    """Build a UserContext from Bedrock sessionAttributes.

    All session attribute values are strings — this function coerces types.
    """
    try:
        nivel = int(session_attrs.get("nivel_hierarquico", "1"))
    except ValueError:
        nivel = 1

    ctx = UserContext(
        departamento=session_attrs.get("departamento", "DESCONHECIDO"),
        nivel_hierarquico=nivel,
        cargo=session_attrs.get("cargo", ""),
        oid=session_attrs.get("oid", ""),
        upn=session_attrs.get("upn", "anonimo"),
        geografia=session_attrs.get("geografia", "BR"),
        tenant_id=session_attrs.get("tenant_id", ""),
    )

    if ctx.departamento == "DESCONHECIDO":
        logger.warning(
            "sessionAttributes missing 'departamento' — RLS may be incorrect. "
            "Verify that the Supervisor is forwarding user_profile into sessionAttributes."
        )
    return ctx


def require_department(ctx: UserContext, allowed: set[str]) -> None:
    """Raise PermissionError if user's department is not in the allowed set.

    Parameters
    ----------
    ctx : UserContext
    allowed : set[str]
        Upper-cased department names that may access this tool.
    """
    if ctx.department_upper not in allowed:
        raise PermissionError(
            f"Departamento '{ctx.departamento}' não tem acesso a esta ferramenta. "
            f"Acesso restrito a: {', '.join(sorted(allowed))}."
        )


def require_level(ctx: UserContext, min_level: int) -> None:
    """Raise PermissionError if user's hierarchy level is below the minimum."""
    if ctx.nivel_hierarquico < min_level:
        raise PermissionError(
            f"Nível hierárquico {ctx.nivel_hierarquico} insuficiente. "
            f"Nível mínimo exigido: {min_level}."
        )
