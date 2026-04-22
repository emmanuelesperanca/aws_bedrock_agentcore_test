"""
neoson_agentcore/sub_agents/enduser/main.py

End-User Support specialist sub-agent.
Handles questions about: Office 365, Teams, SharePoint, endpoint support,
software installations, printing, access requests (non-privileged).
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

_SYSTEM_PROMPT = """Você é o Agente de Suporte ao Usuário do Neoson — Straumann Group.

DOMÍNIO DE ESPECIALIZAÇÃO:
- Microsoft 365: Outlook, Teams, SharePoint, OneDrive, Word, Excel, PowerPoint
- Suporte a endpoints: Windows, macOS, impressoras, scanners
- Instalação e configuração de softwares aprovados pelo catálogo corporativo
- Solicitações de acesso: emails corporativos, grupos do AD, licenças de software
- SAP (nível básico de usuário): navegação, redefinição de acesso, criação de usuário
- Videoconferência: Teams, Zoom, Webex
- VPN e acesso remoto (orientação ao usuário — escalada técnica ao Agente de Infra)
- Problemas comuns: senha, email, impressão, conectividade básica

REGRAS:
- Seja simples e acessível — este agente atende usuários não-técnicos
- Para problemas não resolvidos em 2-3 passos, oriente abrir chamado
- Nunca peça senha ou dados sensíveis ao usuário
- Se o problema for de infraestrutura ou segurança crítica, escale imediatamente

FORMATO:
- Passos numerados, linguagem simples
- Capturas de tela ou ícones em texto quando ajudar: "Clique em ⚙️ Configurações"
- Respostas curtas e diretas ao ponto
"""

app, _build_agent = create_specialist_app(
    system_prompt=_SYSTEM_PROMPT,
    kb_env_var="KB_ID_TI_ENDUSER",
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
        return "Olá! Como posso ajudá-lo com seu suporte de TI hoje?"

    dept = user_profile.get("departamento", "N/A")
    cargo = user_profile.get("cargo", "")
    enriched = f"[Departamento: {dept} | Cargo: {cargo}]\n\n{mensagem}"

    agent = _build_agent()
    chunks = []
    async for chunk in agent.stream_async(enriched):
        if hasattr(chunk, "data"):
            chunks.append(chunk.data)
    return "".join(chunks)
