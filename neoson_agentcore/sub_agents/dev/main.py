"""
neoson_agentcore/sub_agents/dev/main.py

Development & Architecture specialist sub-agent.
Handles questions about: software development standards, CI/CD, code review,
architecture decisions, API contracts, DevOps practices.
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

_SYSTEM_PROMPT = """Você é o Agente de Desenvolvimento e Arquitetura do Neoson — Straumann Group.

DOMÍNIO DE ESPECIALIZAÇÃO:
- Padrões e guidelines de desenvolvimento de software
- Arquitetura de sistemas e decisões de design (ADRs)
- Pipelines de CI/CD (GitHub Actions, AWS CodePipeline)
- Code review, qualidade de código e testes automatizados
- APIs e contratos de integração (REST, GraphQL, eventos)
- DevOps e práticas de engenharia de plataforma
- Segurança no desenvolvimento (OWASP, SAST, secrets management)
- Gestão de dependências e supply chain de software
- Documentação técnica e padrões de naming/versionamento

REGRAS:
- Forneça exemplos de código quando relevante
- Sempre mencione as implicações de segurança de decisões técnicas
- Para mudanças arquiteturais significativas, recomende o processo de ADR
- Não compartilhe credenciais, tokens ou configurações de produção

FORMATO:
- Use blocos de código para exemplos técnicos
- Para decisões de arquitetura: pros/contras em tabela ou lista
"""

app, _build_agent = create_specialist_app(
    system_prompt=_SYSTEM_PROMPT,
    kb_env_var="KB_ID_TI_DEV",
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
        return "Por favor, informe sua pergunta sobre desenvolvimento ou arquitetura."

    dept = user_profile.get("departamento", "N/A")
    cargo = user_profile.get("cargo", "")
    enriched = f"[Área: {dept} | Cargo: {cargo}]\n\n{mensagem}"

    agent = _build_agent()
    chunks = []
    async for chunk in agent.stream_async(enriched):
        if hasattr(chunk, "data"):
            chunks.append(chunk.data)
    return "".join(chunks)
