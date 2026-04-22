"""
neoson_agentcore/supervisor/main.py

Neoson Supervisor Agent — BedrockAgentCoreApp entrypoint.

Architecture:
- Receives requests from Amazon API Gateway (Bearer token pre-validated by JWT Authorizer)
- Decodes Entra ID JWT claims → builds user_profile for RLS
- Runs a Strands ReAct Agent (Claude Sonnet 4.5) that decides dynamically
  which tools/sub-agents to invoke — replaces the manual AgentClassifier
- Delegates to specialist sub-agents via A2A protocol (Fase 4)
- Persists conversation turns via Bedrock MemorySessionManager (Fase 5)

Usage (local dev):
    agentcore dev
    agentcore invoke '{"mensagem": "Qual meu saldo de férias?"}' --stream
"""

import asyncio
import json
import logging
import os
from functools import lru_cache
from typing import Any

import boto3
from strands import Agent, tool
from strands.models import BedrockModel

from bedrock_agentcore.runtime import BedrockAgentCoreApp, BedrockAgentCoreContext

from neoson_agentcore.runtime.identity import extract_user_profile
from neoson_agentcore.supervisor.system_prompt import NEOSON_SYSTEM_PROMPT

# ---------------------------------------------------------------------------
# App & logging
# ---------------------------------------------------------------------------
app = BedrockAgentCoreApp()
logger = app.logger

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------
MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",  # cross-region inference profile
)
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# ---------------------------------------------------------------------------
# Sub-agent ARNs (Fase 4 — populated via environment variables set by CFn)
# ---------------------------------------------------------------------------
SUB_AGENT_ARNS: dict[str, str] = {
    "governance": os.getenv("SUB_AGENT_ARN_GOVERNANCE", ""),
    "infra":      os.getenv("SUB_AGENT_ARN_INFRA", ""),
    "dev":        os.getenv("SUB_AGENT_ARN_DEV", ""),
    "enduser":    os.getenv("SUB_AGENT_ARN_ENDUSER", ""),
    "rh":         os.getenv("SUB_AGENT_ARN_RH", ""),
}

# ---------------------------------------------------------------------------
# Knowledge Base IDs (Fase 3 — Track A)
# ---------------------------------------------------------------------------
KB_IDS: dict[str, str] = {
    "rh":          os.getenv("KB_ID_RH", ""),
    "governance":  os.getenv("KB_ID_TI_GOVERNANCE", ""),
    "infra":       os.getenv("KB_ID_TI_INFRA", ""),
    "dev":         os.getenv("KB_ID_TI_DEV", ""),
    "enduser":     os.getenv("KB_ID_TI_ENDUSER", ""),
}


# ---------------------------------------------------------------------------
# Bedrock client (shared, reused across invocations)
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _bedrock_agent_runtime() -> Any:
    return boto3.client("bedrock-agent-runtime", region_name=AWS_REGION)


# ---------------------------------------------------------------------------
# Request-scoped context (set in @app.entrypoint before agent.stream_async)
# ---------------------------------------------------------------------------
_request_context: dict = {}


# ---------------------------------------------------------------------------
# Tools — inline in supervisor for low-latency KB search and glossary.
# Integration tools (TI/RH/SAP) live in Lambda Action Groups (Fase 2).
# ---------------------------------------------------------------------------

@tool
def search_knowledge_base(query: str, domain: str) -> str:
    """Search Neoson corporate knowledge base for policies, manuals and procedures.

    Use this tool when the user asks about:
    - IT policies, security rules, compliance (LGPD, ISO 27001, SOX)
    - HR policies, benefits, vacation rules, onboarding procedures
    - Infrastructure standards, architecture guidelines
    - Development standards, coding guidelines, API contracts
    - End-user support procedures and FAQs

    Args:
        query:  Natural language search query in Portuguese.
        domain: Knowledge domain — one of: "rh", "governance", "infra", "dev", "enduser".
                Choose the domain that best matches the user's question.

    Returns:
        Relevant passages from the knowledge base, ranked by semantic similarity.
        Returns a message if no relevant content is found.
    """
    user_profile = _request_context.get("user_profile", {})
    kb_id = KB_IDS.get(domain, "")

    if not kb_id:
        return (
            f"Knowledge Base for domain '{domain}' is not configured yet. "
            "Please contact the Neoson team."
        )

    try:
        client = _bedrock_agent_runtime()
        resp = client.retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {
                    "numberOfResults": 5,
                    "filter": _build_rls_filter(user_profile),
                }
            },
        )
        results = resp.get("retrievalResults", [])
        if not results:
            return "Nenhum documento relevante encontrado para essa consulta."

        passages = []
        for r in results:
            content = r.get("content", {}).get("text", "")
            source = r.get("location", {}).get("s3Location", {}).get("uri", "documento")
            score = round(r.get("score", 0), 3)
            passages.append(f"[Score: {score} | Fonte: {source}]\n{content}")

        return "\n\n---\n\n".join(passages)

    except Exception as exc:
        logger.error("KB search error domain=%s: %s", domain, exc)
        return f"Erro ao consultar a base de conhecimento: {exc}"


@tool
def search_corporate_glossary(term: str) -> str:
    """Look up Straumann Group corporate terms, acronyms and systems.

    Use this tool when the user mentions or asks about:
    - Corporate systems (SAP, TOTVS, Salesforce, ServiceNow, Workday, Confluence, Jira)
    - HR terms (PPR, PDI, Avaliação 360, Onboarding, CLT, PJ)
    - Compliance acronyms (LGPD, GDPR, SOX, ISO 27001, PCI-DSS)
    - Internal processes or project names the user seems unfamiliar with
    - Any abbreviation or acronym from the prompt

    Args:
        term: The term, acronym or system name to look up (case-insensitive).

    Returns:
        Definition and usage context for the term, or a not-found message.
    """
    # Import the existing glossary module from the legacy project
    try:
        from core.glossario_corporativo import get_termo_corporativo  # type: ignore
        result = get_termo_corporativo(term)
        if result:
            return f"**{term}**: {result}"
        return f"Termo '{term}' não encontrado no glossário corporativo."
    except ImportError:
        # Fallback: glossary not available in this container — return gracefully
        return f"Glossário corporativo indisponível. Termo consultado: '{term}'."


@tool
def delegate_to_specialist(specialist: str, message: str) -> str:
    """Delegate a question to a specialist sub-agent via A2A protocol.

    Use this tool when the question clearly falls within a specialist's domain
    AND requires deeper knowledge than a simple KB search can provide, OR when
    the user specifically asks to speak with a named specialist.

    Available specialists and their domains:
    - "governance": Ariel — IT policies, compliance, LGPD, ISO 27001, audit, SOX
    - "infra":      Alice — Servers, networking, cloud AWS/Azure, VPN, backup
    - "dev":        Carlos — APIs, CI/CD, databases, DevOps, debugging
    - "enduser":    Marina — Password reset, hardware, Office 365, e-mail, mobile
    - "rh":         Paula — Vacation, hours bank, benefits, training, performance reviews

    Args:
        specialist: Specialist identifier — one of: governance, infra, dev, enduser, rh.
        message:    The full question to forward to the specialist, in Portuguese.

    Returns:
        The specialist's response as a string.
    """
    arn = SUB_AGENT_ARNS.get(specialist, "")
    if not arn:
        return (
            f"Sub-agente '{specialist}' ainda não foi implantado. "
            "Tentarei responder diretamente com as informações disponíveis."
        )

    try:
        from a2a.client import ClientFactory  # type: ignore
        from a2a.types import Message, Role, TextPart  # type: ignore

        bearer_token = _request_context.get("bearer_token", "")
        session_id = _request_context.get("session_id", "")
        actor_id = _request_context.get("user_profile", {}).get("oid", "unknown")

        client = ClientFactory.create_default(
            agent_arn=arn,
            bearer_token=bearer_token,
        )
        response = asyncio.get_event_loop().run_until_complete(
            client.invoke(
                user_id=actor_id,
                session_id=session_id,
                content=Message(
                    role=Role.USER,
                    parts=[TextPart(text=message)],
                ),
            )
        )
        return str(response)

    except Exception as exc:
        logger.error("A2A delegation to %s failed: %s", specialist, exc)
        return (
            f"Não foi possível contactar o especialista '{specialist}' no momento. "
            f"Erro: {exc}"
        )


# ---------------------------------------------------------------------------
# Agent factory — one Agent instance per request (stateless)
# ---------------------------------------------------------------------------

def _build_agent() -> Agent:
    model = BedrockModel(model_id=MODEL_ID)
    return Agent(
        model=model,
        system_prompt=NEOSON_SYSTEM_PROMPT,
        tools=[
            search_knowledge_base,
            search_corporate_glossary,
            delegate_to_specialist,
        ],
    )


# ---------------------------------------------------------------------------
# Entrypoint — called by BedrockAgentCoreApp per request
# ---------------------------------------------------------------------------

@app.entrypoint
async def invoke(payload: dict, context: Any):
    """
    Main AgentCore entrypoint.

    Expected payload:
        {
            "mensagem": "Qual meu saldo de férias?",
            "session_id": "<optional — falls back to AgentCore managed session>"
        }
    """
    # --- 1. Extract identity from pre-validated JWT -------------------------
    headers = BedrockAgentCoreContext.get_request_headers()
    auth_header = headers.get("Authorization", "")

    try:
        user_profile = extract_user_profile(auth_header)
    except ValueError as exc:
        logger.warning("Identity extraction failed: %s", exc)
        # In dev/test scenarios the gateway may not be present
        user_profile = {
            "upn": "dev@straumann.com", "oid": "dev",
            "full_name": "Dev User", "departamento": "TI",
            "cargo": "Developer", "nivel_hierarquico": 2, "geografia": "BR",
            "tenant_id": "",
        }

    session_id = BedrockAgentCoreContext.get_session_id() or payload.get("session_id", "")
    mensagem = payload.get("mensagem") or payload.get("prompt", "")

    if not mensagem:
        yield "Por favor, envie uma mensagem."
        return

    logger.info(
        "Request received | session=%s | user=%s | dept=%s | message_len=%d",
        session_id,
        user_profile.get("upn"),
        user_profile.get("departamento"),
        len(mensagem),
    )

    # --- 2. Store request-scoped context (used by tools without arg passing) -
    _request_context.clear()
    _request_context.update({
        "user_profile": user_profile,
        "session_id": session_id,
        "bearer_token": auth_header,
    })

    # --- 3. Run ReAct agent -------------------------------------------------
    agent = _build_agent()

    # Enrich message with user context so the agent knows who is asking
    enriched_message = (
        f"[Usuário: {user_profile['full_name']} | "
        f"Departamento: {user_profile['departamento']} | "
        f"Cargo: {user_profile['cargo']}]\n\n"
        f"{mensagem}"
    )

    async for event in agent.stream_async(enriched_message):
        if "data" in event and isinstance(event["data"], str):
            yield event["data"]


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.ping
def ping():
    from bedrock_agentcore.runtime.models import PingStatus
    return PingStatus.HEALTHY


# ---------------------------------------------------------------------------
# Local dev entry
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_rls_filter(user_profile: dict) -> dict:
    """
    Build a Bedrock Knowledge Base metadata filter that enforces Row-Level Security
    matching the existing pgvector schema columns:
    - areas_liberadas: list of departments that can see this chunk
    - nivel_hierarquico_minimo: minimum hierarchy level to access this chunk

    The filter allows chunks where:
      (areas_liberadas contains user_dept  OR  areas_liberadas contains "GERAL")
      AND nivel_hierarquico_minimo <= user_level
    """
    dept = user_profile.get("departamento", "GERAL")
    level = user_profile.get("nivel_hierarquico", 1)

    return {
        "andAll": [
            {
                "orAll": [
                    {
                        "listContains": {
                            "key": "areas_liberadas",
                            "value": dept,
                        }
                    },
                    {
                        "listContains": {
                            "key": "areas_liberadas",
                            "value": "GERAL",
                        }
                    },
                ]
            },
            {
                "lessThanOrEquals": {
                    "key": "nivel_hierarquico_minimo",
                    "value": level,
                }
            },
        ]
    }
