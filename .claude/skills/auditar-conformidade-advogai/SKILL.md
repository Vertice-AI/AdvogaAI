---
name: auditar-conformidade-advogai
description: Varredura do código real do AdvogAI contra as promessas do CLAUDE.md (guardrails do §2, decisões de arquitetura do §4, convenções do §6, fora-de-escopo do §9) — não confia em memória nem em "os testes passam". Use quando o usuário perguntar "falta algo?", "estamos seguindo o CLAUDE.md?", "deixamos passar alguma coisa?", antes de um piloto/entrega maior, ou depois de qualquer fatia que mexa em envio de mensagem, LLM ou dado sensível.
---

# Auditar conformidade com o CLAUDE.md

Teste passando não é a mesma coisa que "seguimos o que o CLAUDE.md promete".
Testes cobrem o que alguém pensou em testar; esta skill releem o documento
seção por seção e confere contra o código de verdade — foi assim que se
achou um bug real (roteamento marcando mensagem como "notificada" antes de
confirmar o envio) numa sessão em que os 84 testes já passavam.

Releia o `CLAUDE.md` do repo antes de rodar o checklist — seções e números
podem ter mudado desde a última vez.

## Checklist

**§2 — regra de nunca opinar.** `app/core/guardrails.py` ainda cobre as
expressões proibidas relevantes? Se alguma fatia nova gera texto livre por
LLM (resumo, mensagem pro cliente), esse texto passa pelo guardrail antes de
sair? `tests/test_guardrails.py` tem caso adversarial pra qualquer padrão
novo de "opinião"?

**§4 — decisões de arquitetura, uma por uma:**
- §4.1/4.2 — nenhum código fora de `app/providers/` importa cliente de
  fornecedor processual direto; consulta ao tribunal nunca acontece durante
  uma conversa.
- §4.3 — pipeline de resumo continua nas 5 etapas na ordem certa; guardrail
  roda antes de qualquer decisão de envio.
- §4.4 — toda função que é "ponto de entrada" (task Celery, service chamado
  por rota) chama `definir_tenant` antes de tocar tabela de negócio (ver
  skill `escrever-com-rls-advogai` pra armadilha do `SET LOCAL`).
- §4.5 — regra do silêncio: nenhuma fatia nova manda mensagem pro cliente
  sem checar `atendimento_humano_desde`.
- §4.6 — nenhum caminho novo revela dado processual pra número não
  vinculado.
- §4.7 — **este é o que mais escapa**: pra toda chamada nova que manda
  mensagem (`channel.send_text`/`send_template`), confira se o código só
  marca "enviado"/"notificado" **depois** do envio confirmar sucesso, nunca
  antes. Se o envio falhar, o dado tem que continuar em estado pendente
  (fila que segura, não descarta) — não só logar e seguir em frente como se
  tivesse dado certo. Rate limit de 3-5s com jitter entre números diferentes
  continua respeitado.

**§6 — convenções:**
- Toda chamada a serviço externo (HTTP, LLM) tem timeout explícito e retry
  com backoff escritos no código — não só o default da lib/SDK.
- Nenhum log novo grava CPF, relato do cliente ou texto integral de
  movimento (`grep -rn "logger\.” app/` e olhar os argumentos passados).
- Nenhum `except Exception: pass` silencioso.
- Migration nova segue o padrão RLS exato de uma migration recente (GRANT +
  ENABLE ROW LEVEL SECURITY + CREATE POLICY tenant_isolation), não só
  `create_table`.

**§9 — fora de escopo.** Nada do que foi construído nesta sessão é petição,
análise de mérito, cálculo de prazo, OCR, integração com CRM jurídico,
mobile, dashboard analítico, billing self-service, canal além do WhatsApp,
ou scraper próprio.

**Divergências de stack conhecidas** (não bloqueiam, mas vale mencionar se
perguntado): o projeto usa o SDK cru da Anthropic (`AsyncAnthropic`), não o
"Claude Agent SDK" listado no §3 — decisão pragmática pra tarefas de
classificação/resumo single-turn, não uma correção a fazer sem pedido
explícito.

## Como reportar

Separe os achados em **bloqueante** (quebra uma regra inegociável do §2/§4.6,
ou perde dado/mensagem de verdade) vs. **conhecido, não bloqueante pro
estágio atual** (ex: sem alerta de verdade quando a UAZAPI cai, sem
observabilidade Langfuse/Sentry — aceitável pra piloto Solo com pouca
gente, vira prioridade quando escalar). Nem toda divergência do CLAUDE.md
precisa virar tarefa imediata — mas precisa ser dita, não escondida.
