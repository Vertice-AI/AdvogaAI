# CLAUDE.md — Contexto do projeto

> Este arquivo fica na raiz do repositório. O agente de código lê ele automaticamente
> em toda sessão. Ele existe para eu NÃO precisar reexplicar o projeto a cada prompt.

---

## 1. O que estamos construindo

SaaS B2B: agente de IA que atende clientes de escritórios de advocacia via WhatsApp.

Resolve duas dores, e só essas duas na Fase 1:
1. **"Como está meu processo?"** — o agente consulta os andamentos, resume em linguagem
   simples e responde. Também avisa proativamente quando há movimentação relevante.
2. **"Com quem eu falo?"** (modelo Escritório) — o agente identifica qual advogado está
   disponível, na área certa, e faz o roteamento com resumo do caso pronto.

Dois planos: **Solo** (1 advogado) e **Escritório** (equipe + orquestrador).

## 2. Regra inegociável do produto

O agente **informa e organiza. Nunca opina.**

Está PROIBIDO de: prever resultado de processo, estimar valores de condenação,
interpretar mérito de decisão judicial, dar orientação jurídica, estimar prazos de
conclusão, dizer se a chance é boa ou ruim.

Isso não é uma limitação técnica a ser contornada no futuro. É o posicionamento do
produto e a proteção jurídica do negócio. Qualquer código que enfraqueça isso está errado.

## 3. Stack

| Camada | Escolha |
|---|---|
| Linguagem/framework | Python 3.12 + FastAPI |
| Agente | Claude Agent SDK |
| LLM principal | claude-sonnet-5 (resumo, conversa) |
| LLM auxiliar | claude-haiku-4-5 (classificação de intenção e relevância) |
| Banco | PostgreSQL 16 + pgvector (Supabase local via Docker no dev) |
| ORM / migrations | SQLAlchemy 2.0 + Alembic |
| Fila | Redis + Celery |
| Canal | **UAZAPI** (WhatsApp não-oficial) — ver seção 4.7 |
| Painel | Next.js 15 (App Router) + Tailwind + shadcn/ui |
| Observabilidade | Langfuse (traces de LLM) + Sentry |
| Testes | pytest + pytest-asyncio |

**Não adicione** Pinecone, Weaviate, LangChain, Django, MongoDB ou qualquer banco vetorial
externo. pgvector na mesma instância do Postgres é suficiente e é intencional.

**Decisão consciente sobre o canal:** UAZAPI é gateway não-oficial (sessão de WhatsApp
Web). Escolhida pela velocidade de implantação no MVP. O risco de bloqueio do número
pela Meta é real e conhecido — por isso o canal é abstraído (seção 4.7) e a migração
para a API oficial está prevista antes do primeiro escritório de porte médio.

## 4. Arquitetura — decisões já tomadas

### 4.1 Consulta processual roda em background, nunca durante a conversa
Um worker Celery sincroniza andamentos 1x/dia e grava no nosso Postgres. Quando o cliente
pergunta, o agente lê da NOSSA tabela. Nunca chama a API do tribunal dentro do fluxo de
conversa. Motivo: latência do tribunal é imprevisível e custo por chamada é real.

### 4.2 Abstração de fornecedor de dados processuais
Toda consulta passa pelo protocolo `ProcessProvider`. Implementações:
- `DataJudProvider` — API pública do CNJ, gratuita. Base e fallback.
- `JuditProvider` — pago, tempo real. Fonte primária.

Nenhum código fora de `app/providers/` pode importar cliente de fornecedor diretamente.
Trocar de fornecedor tem que ser mudar uma variável de ambiente.

### 4.3 Pipeline de resumo (5 etapas, nesta ordem)
```
movimento bruto
 → [1] normalização (determinística, sem LLM)
 → [2] classificação de relevância (Haiku) — ~60% dos movimentos são internos
       e NÃO viram notificação. Este filtro é o produto.
 → [3] resumo (Sonnet) com system prompt restritivo
 → [4] guardrail determinístico — regex de expressões proibidas + verificação
       de que o resumo cita a data e o texto de origem
 → [5] fila de aprovação humana OU envio direto, conforme o nível de autonomia
       daquele cliente naquela categoria de movimento
```

### 4.4 Multi-tenant com Row Level Security
Isolamento no banco, não na aplicação. Toda tabela de negócio tem `tenant_id` com
política RLS. Um bug de código não pode vazar dado entre escritórios.

### 4.5 Regra do silêncio
Quando um humano assume a conversa, a IA para completamente. Só volta se o advogado
encerrar a conversa ou digitar `/ia`.

### 4.6 Verificação de identidade
Número de WhatsApp não vinculado previamente a um cliente NUNCA recebe informação
processual. Sem exceção.

### 4.7 Canal de mensagens — abstração obrigatória
Usamos **UAZAPI** no MVP: subir mais rápido, sem processo de verificação da Meta,
sem template aprovado e com custo menor por instância.

**Mesmo padrão do ProcessProvider.** Toda comunicação com WhatsApp passa pelo
protocolo `ChannelProvider` em `app/channels/base.py`:

```python
class ChannelProvider(Protocol):
    def send_text(self, to: str, text: str) -> MessageId: ...
    def send_template(self, to: str, template: str, params: dict) -> MessageId: ...
    def parse_webhook(self, payload: dict) -> InboundMessage: ...
    def verify_signature(self, payload: bytes, headers: dict) -> bool: ...
```

Implementações:
- `UazapiProvider` — em uso agora
- `MetaCloudProvider` — stub com a interface correta, ativado quando migrarmos

Nenhum código fora de `app/channels/` pode conhecer o formato de payload da UAZAPI.
Trocar de gateway tem que ser mudar `CHANNEL_PROVIDER` no ambiente.

**Diferenças da UAZAPI que o código precisa tratar:**
- Autenticação por token no header, não HMAC como a Meta. `verify_signature` da
  UAZAPI valida o token configurado — não invente assinatura que não existe.
- Não há janela de 24h nem template aprovado. `send_template` na UAZAPI renderiza
  o template localmente e envia como texto comum. **Mantenha a chamada mesmo assim** —
  é o que permite migrar para a Meta sem reescrever a lógica de notificação.
- A instância pode cair (desconexão da sessão do WhatsApp). Implemente:
  healthcheck da instância a cada 5 min, alerta quando cair, e fila de mensagens
  que segura o envio em vez de descartar.
- Rate limit próprio: não dispare notificação em massa. Intervalo mínimo de 3 a 5s
  entre envios para números diferentes, com jitter.

**Regra de dados:** nenhum conteúdo de conversa pode ser retido pelo gateway além do
necessário para entrega. Isso vai ser perguntado por escritório de porte médio.
Documente o que a UAZAPI retém antes de vender para escritório.

## 5. Estrutura de diretórios

```
app/
  api/            rotas FastAPI (webhooks, painel)
  agents/         definições dos agentes e system prompts
    atendimento.py
    processual.py
    orquestrador.py
    prompts/      system prompts em arquivos .md separados, versionados
  providers/      ProcessProvider e implementações (única fronteira com APIs externas)
  channels/       ChannelProvider + UazapiProvider (única fronteira com o gateway)
  core/           config, segurança, guardrails, exceções
  db/             models SQLAlchemy, migrations Alembic, RLS
  workers/        tasks Celery (sync processual, notificações)
  services/       lógica de negócio (resumo, roteamento, agendamento)
tests/
web/              painel Next.js
```

## 6. Convenções de código

- Type hints obrigatórios. `mypy --strict` tem que passar.
- `ruff` para lint e format. Sem exceções silenciadas sem comentário justificando.
- Nada de `except Exception: pass`.
- Toda chamada a serviço externo tem timeout explícito e retry com backoff.
- Segredos só via variável de ambiente. Nenhuma chave hardcoded, nem em teste.
- Logs estruturados (structlog), sempre com `tenant_id`. **Nunca logue CPF, conteúdo
  de relato do cliente ou texto integral de movimento processual.**
- Migrations sempre via Alembic. Nunca `create_all()` fora de teste.
- Commits em português, no imperativo: `adiciona classificador de relevância`.

## 7. Testes

- Toda regra de negócio precisa de teste. Especialmente os guardrails.
- Chamadas de LLM e de API externa sempre mockadas em teste unitário.
- Fixtures com movimentos processuais reais anonimizados em `tests/fixtures/`.
- Os guardrails têm suíte própria com casos adversariais: mensagens que tentam
  fazer o agente opinar sobre o mérito precisam ser bloqueadas.

## 8. Como trabalhar comigo neste repositório

- Trabalhe em **fatias verticais**. Cada entrega tem que rodar de ponta a ponta.
- Antes de implementar algo com mais de 3 arquivos, me apresente o plano e espere aprovação.
- Não crie arquivo de configuração, README, Dockerfile ou script que eu não pedi.
- Se uma decisão de arquitetura não estiver neste documento, **pergunte** em vez de assumir.
- Ao terminar, rode os testes e o mypy. Não me diga que terminou se não passou.
- Prefira menos código. Este projeto vai ser mantido por uma equipe pequena.

## 9. Fora de escopo na Fase 1 — não implemente

Geração de petições · análise de mérito · cálculo de prazos processuais · leitura de PDF ·
OCR · integração com CRM jurídico (Astrea/Projuris/ADVBOX) · app mobile · dashboard
analítico · billing self-service · múltiplos canais além do WhatsApp · peticionamento
eletrônico · scraper próprio de tribunal.
