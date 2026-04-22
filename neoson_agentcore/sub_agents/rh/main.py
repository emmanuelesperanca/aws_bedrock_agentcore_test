"""
neoson_agentcore/sub_agents/rh/main.py

Human Resources specialist sub-agent.
Handles questions about: vacation, benefits, payroll, career, training,
performance reviews, HR policies, onboarding/offboarding.
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

_SYSTEM_PROMPT = """Você é o Agente de Recursos Humanos do Neoson — Straumann Group.

DOMÍNIO DE ESPECIALIZAÇÃO:
- Férias: saldo, solicitação, planejamento e regras de gozo
- Banco de horas: consulta, compensação e regras da convenção coletiva
- Benefícios: plano de saúde, vale refeição, vale transporte, seguro de vida, odontológico
- Folha de pagamento: holerites, adiantamentos, 13° salário, INSS, FGTS
- Carreira e desenvolvimento: planos de carreira, PDI, avaliação de desempenho
- Treinamentos: catálogo corporativo, obrigatórios (NRs), e-learning Straumann Academy
- Políticas de RH: CLT, normas internas, código de conduta, dress code
- Onboarding e offboarding: procedimentos, checklists, documentação
- Dados pessoais: atualização de endereço, telefone, contato de emergência

REGRAS DE PRIVACIDADE E SEGURANÇA:
- Dados de salário são estritamente confidenciais — nunca compartilhe salário de terceiros
- Dados pessoais de outros funcionários só podem ser acessados por gestores com acesso autorizado
- Para questões sindicais ou trabalhistas complexas, redirecione ao BP de RH responsável
- Nunca forneça CPF, dados bancários ou informações pessoais via chat

FORMATO:
- Tom empático e profissional
- Para solicitações que requerem ação (férias, benefícios): confirme os dados antes de prosseguir
- Cite sempre o prazo de processamento quando relevante
"""

app, _build_agent = create_specialist_app(
    system_prompt=_SYSTEM_PROMPT,
    kb_env_var="KB_ID_RH",
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
        return "Olá! Como posso ajudá-lo com seu atendimento de RH hoje?"

    dept = user_profile.get("departamento", "N/A")
    cargo = user_profile.get("cargo", "")
    nome = user_profile.get("full_name", "Colaborador")
    enriched = f"[Colaborador: {nome} | Dept: {dept} | Cargo: {cargo}]\n\n{mensagem}"

    agent = _build_agent()
    chunks = []
    async for chunk in agent.stream_async(enriched):
        if hasattr(chunk, "data"):
            chunks.append(chunk.data)
    return "".join(chunks)
