"""
neoson_agentcore/sub_agents/infra/main.py

Infrastructure & Operations specialist sub-agent.
Handles questions about: network, servers, cloud, monitoring,
capacity planning, on-call procedures, SLAs/SLOs.
"""

import logging
import os

from neoson_agentcore.sub_agents.base.base_agent import (
    create_specialist_app,
    _request_context,
)
from neoson_agentcore.runtime.identity import extract_user_profile

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """Você é o Agente de Infraestrutura e Operações do Neoson — Straumann Group.

DOMÍNIO DE ESPECIALIZAÇÃO:
- Infraestrutura de rede (LAN, WAN, SD-WAN, firewall, VPN)
- Servidores on-premises e nuvem (AWS, Azure)
- Monitoramento e alertas (Zabbix, Datadog, CloudWatch)
- Gestão de capacidade e planejamento de crescimento
- Procedimentos de on-call e resposta a incidentes
- SLAs, SLOs e métricas de performance (uptime, latência)
- Backup, recuperação de desastres (DR) e RTO/RPO
- Segurança de infraestrutura (patch management, hardening)

REGRAS:
- Para problemas críticos em produção, oriente imediatamente a abertura de chamado P1
- Sempre questione o impacto no negócio antes de recomendar ações de manutenção
- Não forneça credenciais ou informações de acesso — redirecione ao processo de PAM
- Responda de forma técnica mas objetiva

FORMATO:
- Para procedimentos: use passos numerados
- Para diagnósticos: liste possíveis causas por ordem de probabilidade
"""

app, _build_agent = create_specialist_app(
    system_prompt=_SYSTEM_PROMPT,
    kb_env_var="KB_ID_TI_INFRA",
)


@app.entrypoint
async def invoke(payload: dict, context) -> str:
    auth_header = context.get_request_headers().get("Authorization", "")
    user_profile = extract_user_profile(auth_header) if auth_header else {}
    session_id = context.get_session_id() or "unknown"

    _request_context.update({
        "user_profile": user_profile,
        "session_id": session_id,
    })

    mensagem: str = payload.get("mensagem", payload.get("message", ""))
    if not mensagem:
        return "Por favor, informe sua pergunta sobre infraestrutura ou operações."

    dept = user_profile.get("departamento", "N/A")
    cargo = user_profile.get("cargo", "")
    enriched = f"[Área: {dept} | Cargo: {cargo}]\n\n{mensagem}"

    agent = _build_agent()
    chunks = []
    async for chunk in agent.stream_async(enriched):
        if hasattr(chunk, "data"):
            chunks.append(chunk.data)
    return "".join(chunks)
