# Deploy do AdvogAI no Railway — runbook

Runbook passo a passo pra subir o AdvogAI numa plataforma gerenciada (Railway)
e testar de verdade pelo WhatsApp. Substitui o plano antigo de easypanel/VPS,
que saiu de cena.

> **Antes de começar (o que custa dinheiro — só a partir de sexta):**
> 1. **Assinatura do Railway** (a plataforma). Custo por uso, ~US$5–10/mês
>    pra esse porte de teste.
> 2. **Chave da Anthropic** (`console.anthropic.com` → Billing → crie uma API
>    key; ~US$5 de crédito pré-pago cobre muito teste). É separada da
>    assinatura do Claude Code. **Sem ela o teste ponta-a-ponta não roda** — a
>    mensagem chega e retorna 200, mas o worker estoura ao classificar
>    intenção (Haiku).
>
> Tudo abaixo é execução; a parte de código já está pronta e validada.

---

## Visão geral da arquitetura em produção

Uma imagem só (o `Dockerfile` do repo), rodando em **3 serviços** + **2 bancos**:

| Serviço | O que roda | Público? |
|---|---|---|
| `web` | `uvicorn` (recebe o webhook do WhatsApp) | **sim** (URL pública) |
| `worker` | `celery ... worker` (processa mensagens, resumos, envios) | não |
| `beat` | `celery ... beat` (jobs periódicos: sync, healthcheck, filas) | não |
| Postgres 16 | banco (plugin do Railway) | interno |
| Redis | fila/broker (plugin do Railway) | interno |

> Sem o `worker`, o webhook recebe a mensagem mas **nada é processado**. Sem o
> `beat`, some a notificação proativa (Dor 1), o healthcheck da UAZAPI e o
> desenfileiramento das filas.

---

## Etapa 0 — Criar o projeto e os bancos (Railway)

1. Entre em **railway.app** → **New Project** → conecte o **GitHub**
   (`Vertice-AI/AdvogaAI`, branch `main`).
2. Dentro do projeto, **New → Database → Add PostgreSQL**.
3. **New → Database → Add Redis**.

Anote, na aba **Variables** de cada banco, os valores de conexão **interna**
(hostnames `*.railway.internal`):
- Postgres: o Railway expõe `DATABASE_URL` (algo como
  `postgresql://postgres:SENHA@postgres.railway.internal:5432/railway`).
- Redis: o Railway expõe `REDIS_URL`
  (`redis://default:SENHA@redis.railway.internal:6379`).

> Use sempre os hostnames **internos** (`.railway.internal`). São mais rápidos,
> não passam pela internet e não contam como egress.

---

## Etapa 1 — Definir as variáveis de ambiente

O app lê tudo com prefixo **`ADVOGAI_`** (ver `app/core/config.py`). Duas
credenciais de banco distintas, de propósito (CLAUDE.md §4.4 / migration 0001):

- **Runtime** (`ADVOGAI_DATABASE_URL`): role restrita `advogai_app`, sujeita ao
  RLS. É a que o `web`/`worker`/`beat` usam.
- **Migração** (`ADVOGAI_DATABASE_URL_MIGRACAO`): superusuário `postgres`, dona
  das tabelas — cria a role `advogai_app` e as policies de RLS. Só usada no
  comando de migration, nunca no runtime.

Gere uma senha forte pra role de app **antes** e reutilize nos dois lugares
(`ADVOGAI_APP_DB_PASSWORD` = senha embutida em `ADVOGAI_DATABASE_URL`).

### Variáveis compartilhadas pelos 3 serviços (`web`, `worker`, `beat`)

Copie o `DATABASE_URL`/`REDIS_URL` do Railway e adapte (note o driver
`+psycopg` e a troca de usuário/senha na URL de runtime):

```
# Banco — RUNTIME (role restrita advogai_app + a senha forte que você gerou)
ADVOGAI_DATABASE_URL=postgresql+psycopg://advogai_app:<APP_DB_PASSWORD>@postgres.railway.internal:5432/railway

# Banco — MIGRAÇÃO (superusuário postgres, copiado do plugin, com +psycopg)
ADVOGAI_DATABASE_URL_MIGRACAO=postgresql+psycopg://postgres:<SENHA_POSTGRES>@postgres.railway.internal:5432/railway
ADVOGAI_APP_DB_PASSWORD=<APP_DB_PASSWORD forte — a MESMA da URL de runtime>

# Redis (copiado do plugin)
ADVOGAI_REDIS_URL=redis://default:<SENHA_REDIS>@redis.railway.internal:6379

# LLM (Anthropic — sexta)
ADVOGAI_ANTHROPIC_API_KEY=<chave do console.anthropic.com>

# Canal WhatsApp (UAZAPI)
ADVOGAI_CHANNEL_PROVIDER=uazapi
ADVOGAI_UAZAPI_BASE_URL=https://vrtice.uazapi.com
ADVOGAI_UAZAPI_TOKEN=<token ATUAL da instância — pegar no painel na hora>
ADVOGAI_UAZAPI_WEBHOOK_SECRET=<gere um segredo forte; vai no ?secret= do webhook>

# Fonte de dados processuais (DataJud público, gratuito)
ADVOGAI_PROCESS_PROVIDER=datajud
# ADVOGAI_DATAJUD_API_KEY=  # opcional agora; só necessário quando testar o sync real da Dor 1

# Alerta fora-de-banda quando a instância UAZAPI cai (Slack/Discord/Mattermost
# incoming webhook, formato {"text": ...}). Vazio = sem alerta (só log).
# Cole aqui a URL do Incoming Webhook do Slack. Só precisa no serviço `beat`.
ADVOGAI_ALERT_WEBHOOK_URL=
```

> ⚠️ **O token da UAZAPI rotaciona** quando você mexe/reconecta a sessão da
> instância. Pegue o valor atual no painel da UAZAPI na hora de configurar, e
> confirme `GET /instance/status = connected` antes de qualquer envio. Se der
> 401 depois, é quase sempre token velho.

> Dica Railway: crie as variáveis uma vez e use **Shared Variables** (nível do
> projeto) ou o botão de referência pra não digitar as 3 vezes.

---

## Etapa 2 — Criar os 3 serviços da aplicação

Para cada um: **New → GitHub Repo → `Vertice-AI/AdvogaAI`**. Todos buildam o
mesmo `Dockerfile` automaticamente. Só muda o **Start Command** e se tem
domínio público.

### Serviço `web`
- **Start Command:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
  (sobrescreve o CMD do Dockerfile pra honrar a porta que o Railway injeta).
- **Networking → Generate Domain** (é o único com URL pública).
- **Pre-deploy Command:** `alembic upgrade head`
  (roda a migration a cada deploy; é **idempotente**, seguro repetir — resolve
  a Etapa 3 sem passo manual). Precisa das vars `..._MIGRACAO` e
  `ADVOGAI_APP_DB_PASSWORD` presentes neste serviço.

### Serviço `worker`
- **Start Command:** `celery -A app.workers.celery_app worker --loglevel=info`
- Sem domínio público.

### Serviço `beat`
- **Start Command:** `celery -A app.workers.celery_app beat --loglevel=info`
- Sem domínio público.

Aplique as variáveis da Etapa 1 nos três.

---

## Etapa 3 — Migrations (automática) + confirmar

Se você configurou o **Pre-deploy Command** `alembic upgrade head` no `web`
(Etapa 2), a migration roda sozinha no primeiro deploy. Confirme nos **logs**
do `web` que terminou em `head` sem erro (deve criar a role `advogai_app`, as
tabelas e as policies de RLS).

> Alternativa sem pre-deploy: rode a migration uma vez via CLI
> (`railway run --service web alembic upgrade head`) ou trocando
> temporariamente o start command de um serviço.

---

## Etapa 4 — Seed do tenant de produção (uma vez)

O seed **não é idempotente** (cria um tenant novo a cada execução), então é um
comando pontual — **não** coloque como pre-deploy.

Rode uma vez (precisa da Railway CLI local: `npm i -g @railway/cli`,
`railway login`, `railway link`):

```bash
railway run --service worker \
  env PYTHONPATH=. python scripts/seed_teste.py \
    --advogado-nome "Seu Nome" \
    --advogado-whatsapp 55DDDNUMERO \
    --area-atuacao "Cível"
```

> Sem CLI (100% painel): troque o **Start Command** do `beat` pro comando de
> seed acima, faça um deploy, leia o `tenant_id` nos logs, e **devolva** o
> start command pra `celery ... beat`.

**Guarde o `tenant_id` que ele imprime** — é o de produção, NOVO. O
`8dc01be4...` de sessões anteriores era do seed **local**, não reutilize.

O seed imprime também o caminho do webhook: `/webhooks/uazapi/<tenant_id>`.

---

## Etapa 5 — Montar e registrar o webhook na UAZAPI

1. Pegue a URL pública do serviço `web` (Etapa 2) — ex.:
   `https://advogai-web-production.up.railway.app`.
2. Monte o webhook completo (rota em `app/api/webhooks.py`):

   ```
   https://<host-do-web>/webhooks/uazapi/<tenant_id_prod>?secret=<ADVOGAI_UAZAPI_WEBHOOK_SECRET>
   ```

   O `?secret=` **tem que ser igual** ao `ADVOGAI_UAZAPI_WEBHOOK_SECRET` das
   vars — é como o `verify_signature` da UAZAPI autentica (CLAUDE.md §4.7: token
   no header/segredo, não HMAC). Segredo errado = 401.

3. Registre na UAZAPI (`POST /webhook` com essa URL) — pelo painel da instância
   ou pela API. Aponte pros eventos de **mensagem**.

---

## Etapa 6 — Fumaça e go-live

1. `GET https://<host-do-web>/health` → deve responder 200.
2. Confirme `GET /instance/status = connected` na UAZAPI.
3. Do WhatsApp **do advogado cadastrado no seed**, mande uma mensagem pro
   número da instância. Acompanhe os logs do `worker`: deve classificar
   intenção e responder. (Número não vinculado NÃO recebe dado processual —
   CLAUDE.md §4.6 — isso é comportamento esperado, não bug.)
4. Antes de liberar pra cliente real, rode a bateria `eval-conversa` (depende
   da chave da Anthropic).

---

## Notas / pegadinhas

- **Porta:** o `web` precisa escutar `$PORT` do Railway — por isso o start
  command sobrescreve a porta 8000 fixa do Dockerfile.
- **Driver do banco:** as duas URLs de Postgres usam `postgresql+psycopg://`
  (SQLAlchemy 2 + psycopg 3). O `DATABASE_URL` que o Railway dá vem como
  `postgresql://` puro — **adicione o `+psycopg`**.
- **Duas credenciais de banco** não é redundância: runtime restrito (RLS) vs.
  migração admin. Nunca aponte o runtime pro superusuário.
- **Rebuild triplo:** os 3 serviços buildam a mesma imagem separadamente. É um
  pouco de desperdício de build, mas mantém tudo no painel e sem CLI.
- **Alerta de healthcheck:** o `beat` checa a instância a cada 5 min e dispara
  um alerta fora-de-banda **só na transição** (caiu / reconectou), pra não
  virar ruído. Configure `ADVOGAI_ALERT_WEBHOOK_URL` com um Incoming Webhook do
  Slack (Slack → Apps → "Incoming Webhooks" → escolha o canal → copie a URL).
  Sem a var, o healthcheck só loga (não quebra nada). Fora-de-banda de
  propósito: o alerta não sai pelo WhatsApp que ele monitora (CLAUDE.md §4.7).
