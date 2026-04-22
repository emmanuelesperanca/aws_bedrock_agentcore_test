"""
neoson_agentcore/sub_agents/governance/main.py

Governance & Compliance specialist sub-agent.
Handles questions about: corporate policies, SOX/ISO compliance,
audit procedures, data governance, LGPD/GDPR, internal controls.
"""

import asyncio
import logging
import os

from bedrock_agentcore import BedrockAgentCoreApp
from bedrock_agentcore.types import PingStatus

from neoson_agentcore.sub_agents.base.base_agent import (
    create_specialist_app,
    _request_context,
)
from neoson_agentcore.runtime.identity import extract_user_profile

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Você é o Agente de Governança e Compliance do Neoson — Straumann Group.

DOMÍNIO DE ESPECIALIZAÇÃO:
- Políticas corporativas e procedimentos internos
- Compliance regulatório: SOX, ISO 9001/13485, LGPD, GDPR
- Processos de auditoria interna e controles internos
- Governança de dados e gestão de riscos
- Código de ética e conduta corporativa
- Procedimentos de aprovação e alçadas

REGRAS DE RESPOSTA:
- Responda apenas sobre temas de governança e compliance
- Cite sempre a política ou procedimento de referência quando disponível
- Se não encontrar informação precisa, informe que a análise requer consulta ao departamento jurídico ou de compliance
- Nunca forneça orientação jurídica definitiva — indique sempre validação com a área competente
- Mantenha a confidencialidade das informações de auditoria

FORMATO:
- Use linguagem formal e precisa
- Estruture respostas longas com tópicos numerados
- Inclua referências às políticas quando aplicável
"""

app, _build_agent = create_specialist_app(
    system_prompt=_SYSTEM_PROMPT,
    kb_env_var="KB_ID_TI_GOVERNANCE",
)


@app.entrypoint
async def invoke(payload: dict, context) -> str:
    from bedrock_agentcore import BedrockAgentCoreContext

    auth_header = context.get_request_headers().get("Authorization", "")
    user_profile = extract_user_profile(auth_header) if auth_header else {}
    session_id = context.get_session_id() or "unknown"

    _request_context.update({
        "user_profile": user_profile,
        "session_id": session_id,
    })

    mensagem: str = payload.get("mensagem", payload.get("message", ""))
    if not mensagem:
        return "Por favor, informe sua pergunta sobre governança ou compliance."

    dept = user_profile.get("departamento", "N/A")
    cargo = user_profile.get("cargo", "")
    enriched = f"[Área: {dept} | Cargo: {cargo}]\n\n{mensagem}"

    agent = _build_agent()
    chunks = []
    async for chunk in agent.stream_async(enriched):
        if hasattr(chunk, "data"):
            chunks.append(chunk.data)
    return "".join(chunks)
