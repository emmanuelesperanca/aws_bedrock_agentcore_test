"""
neoson_agentcore/runtime/identity.py

Decodes claims from a pre-validated Microsoft Entra ID JWT.

Security model:
- Cryptographic signature verification is performed 100% by the Amazon API Gateway
  JWT Authorizer, which validates the token against https://login.microsoftonline.com/{tenant}/v2.0/keys
- By the time a request reaches this container, the token is already trust-established.
- This module only Base64url-decodes the payload to extract claims — no crypto ops.

Pre-requisite (Entra ID):
- The App Registration must have `department` and `jobTitle` enabled as Optional Claims
  on the Access Token in: Azure Portal → App Registrations → <App> → Token configuration
  → Optional claims → Access token → Add: department, jobTitle
"""

import base64
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def extract_user_profile(authorization_header: str) -> dict[str, Any]:
    """
    Build the user_profile dict from claims in a pre-validated Entra ID Access Token.

    Args:
        authorization_header: Raw value of the Authorization HTTP header
                              (e.g. "Bearer eyJ0eXAiOiJKV1Qi...").

    Returns:
        user_profile dict consumed by every @tool that performs RLS filtering:
        {
            "upn":               str  — user principal name (email)
            "oid":               str  — stable Entra ID object ID (use as actor_id)
            "full_name":         str
            "departamento":      str  — e.g. "TI", "RH", "Produção"
            "cargo":             str  — job title from Entra ID
            "nivel_hierarquico": int  — 1-5 inferred from job title
            "geografia":         str  — "BR" or "GLOBAL"
            "tenant_id":         str  — Entra tenant ID (tid claim)
        }

    Raises:
        ValueError: If the header is missing or the token is structurally invalid.
                    These errors should never reach here in production because API
                    Gateway blocks invalid tokens before forwarding the request.
    """
    if not authorization_header:
        raise ValueError(
            "Missing Authorization header — this request should have been blocked "
            "by the API Gateway JWT Authorizer."
        )

    token = authorization_header.removeprefix("Bearer ").strip()
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError(
            f"Malformed JWT: expected 3 dot-separated segments, got {len(parts)}."
        )

    # JWT payload is the middle segment (index 1), Base64url-encoded
    payload_b64 = parts[1]
    # Restore standard base64 padding (Base64url strips it)
    padding_needed = 4 - len(payload_b64) % 4
    if padding_needed != 4:
        payload_b64 += "=" * padding_needed

    try:
        payload: dict = json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception as exc:
        raise ValueError(f"Failed to decode JWT payload: {exc}") from exc

    departamento = payload.get("department", "")
    cargo = payload.get("jobTitle", "")

    if not departamento:
        logger.warning(
            "JWT claim 'department' is empty for oid=%s. "
            "Ensure 'department' is enabled as an Optional Claim on the Access Token "
            "in the Entra ID App Registration. RLS filtering will use empty string.",
            payload.get("oid", "unknown"),
        )

    return {
        "upn": payload.get("preferred_username") or payload.get("upn", ""),
        "oid": payload.get("oid", ""),
        "full_name": payload.get("name", ""),
        "departamento": departamento,
        "cargo": cargo,
        "nivel_hierarquico": _infer_hierarchy_level(cargo),
        "geografia": _infer_geography(payload),
        "tenant_id": payload.get("tid", ""),
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _infer_hierarchy_level(job_title: str) -> int:
    """
    Map job title to a 1-5 hierarchy integer consistent with the existing
    nivel_hierarquico_minimo column in the knowledge base tables.

    Level 5 — Director / VP
    Level 4 — Manager / Gerente
    Level 3 — Coordinator / Supervisor
    Level 2 — Analyst / Engineer / Specialist
    Level 1 — Associate / Intern / default
    """
    title = job_title.lower()
    if any(kw in title for kw in ("diretor", "director", "vp", "vice-president", "presidente", "ceo", "cto", "cfo")):
        return 5
    if any(kw in title for kw in ("gerente", "manager", "head of", "head de")):
        return 4
    if any(kw in title for kw in ("coordenador", "coordinator", "supervisor", "lead", "lider", "líder")):
        return 3
    if any(kw in title for kw in ("analista", "analyst", "especialista", "specialist", "engineer", "engenheiro", "developer", "desenvolvedor")):
        return 2
    return 1


def _infer_geography(payload: dict) -> str:
    """
    Infer geography from UPN domain or tenant context.
    Returns "BR" for Brazilian accounts, "GLOBAL" otherwise.
    """
    upn = (payload.get("preferred_username") or payload.get("upn") or "").lower()
    # Straumann Brazil UPNs contain .br or br. prefix
    if ".br@" in upn or upn.endswith(".br") or "@br." in upn:
        return "BR"
    # Fallback: check country claim if present
    country = payload.get("country", "").upper()
    if country == "BR" or country == "BRAZIL" or country == "BRASIL":
        return "BR"
    return "GLOBAL"
