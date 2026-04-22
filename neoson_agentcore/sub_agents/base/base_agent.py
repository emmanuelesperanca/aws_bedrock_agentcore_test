"""
neoson_agentcore/sub_agents/base/base_agent.py

Shared base for all Neoson specialist sub-agents.

Each sub-agent:
  1. Is a BedrockAgentCoreApp container running on AgentCore
  2. Receives A2A requests from the Supervisor (forwarded Entra ID JWT)
  3. Decodes the JWT → user_profile for RLS
  4. Uses a domain-specific Strands Agent with KB search + action group tools
  5. Streams the response back to the Supervisor

Usage in each sub-agent's main.py:
  from neoson_agentcore.sub_agents.base.base_agent import create_specialist_app

  app, agent_builder = create_specialist_app(
      system_prompt=MY_SYSTEM_PROMPT,
      kb_env_var="KB_ID_TI_INFRA",
  )

  @app.entrypoint
  async def invoke(payload, context): ...
"""

from __future__ import annotations

import logging
import os
from typing import Callable

import boto3
from bedrock_agentcore import BedrockAgentCoreApp
from bedrock_agentcore.types import PingStatus
from strands import Agent, tool
from strands.models import BedrockModel

from neoson_agentcore.runtime.identity import extract_user_profile

logger = logging.getLogger(__name__)

_AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
_DEFAULT_MODEL = os.environ.get(
    "BEDROCK_MODEL_ID",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
)

# Per-request context — populated in @app.entrypoint, read by @tool functions
_request_context: dict = {}


def _build_rls_filter(user_profile: dict) -> dict:
    """Construct Bedrock KB metadata filter for department + hierarchy level RLS."""
    dept = user_profile.get("departamento", "GERAL")
    level = user_profile.get("nivel_hierarquico", 1)
    return {
        "andAll": [
            {
                "orAll": [
                    {"equals": {"key": "area_acesso", "value": dept}},
                    {"equals": {"key": "area_acesso", "value": "GERAL"}},
                ]
            },
            {"lessThanOrEquals": {"key": "nivel_hierarquico_minimo", "value": level}},
        ]
    }


def make_kb_search_tool(kb_env_var: str):
    """Factory: returns a @tool function that searches the KB specified by kb_env_var."""

    @tool
    def search_knowledge_base(query: str) -> str:
        """Search the specialist knowledge base for relevant information.

        Use this tool when you need factual information, policies, procedures,
        or technical documentation relevant to this specialist domain.

        Args:
            query: Search query in natural language (Portuguese or English)

        Returns:
            Relevant text passages from the knowledge base
        """
        kb_id = os.environ.get(kb_env_var, "")
        if not kb_id:
            logger.warning("%s not set — KB search skipped", kb_env_var)
            return "Base de conhecimento não configurada para este agente."

        user_profile = _request_context.get("user_profile", {})
        rls_filter = _build_rls_filter(user_profile)

        bedrock_rt = boto3.client("bedrock-agent-runtime", region_name=_AWS_REGION)
        try:
            resp = bedrock_rt.retrieve(
                knowledgeBaseId=kb_id,
                retrievalQuery={"text": query},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {
                        "numberOfResults": 5,
                        "filter": rls_filter,
                    }
                },
            )
            results = resp.get("retrievalResults", [])
            if not results:
                return "Nenhuma informação encontrada na base de conhecimento."
            passages = [r["content"]["text"] for r in results if r.get("content", {}).get("text")]
            return "\n\n---\n\n".join(passages)
        except Exception as exc:
            logger.exception("KB search failed for kb_id=%s", kb_id)
            return f"Erro ao consultar a base de conhecimento: {exc}"

    return search_knowledge_base


def create_specialist_app(
    *,
    system_prompt: str,
    kb_env_var: str,
    extra_tools: list | None = None,
) -> tuple[BedrockAgentCoreApp, Callable]:
    """Create a BedrockAgentCoreApp configured as a specialist sub-agent.

    Returns (app, agent_builder) where agent_builder() returns a fresh Strands Agent.
    The caller should:
      1. Use `app` as the module-level app instance
      2. Decorate their entrypoint: @app.entrypoint

    The _request_context dict is shared at the module level of base_agent.py.
    Each @app.entrypoint must populate it before calling agent.stream_async().
    """
    app = BedrockAgentCoreApp()

    kb_tool = make_kb_search_tool(kb_env_var)
    tools = [kb_tool] + (extra_tools or [])

    def agent_builder() -> Agent:
        model = BedrockModel(
            model_id=_DEFAULT_MODEL,
            region_name=_AWS_REGION,
        )
        return Agent(
            model=model,
            system_prompt=system_prompt,
            tools=tools,
        )

    @app.ping
    async def ping():
        return PingStatus.HEALTHY

    return app, agent_builder
