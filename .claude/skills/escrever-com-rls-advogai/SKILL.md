---
name: escrever-com-rls-advogai
description: Checklist pra escrever ou revisar código/teste no AdvogAI que toca tabela protegida por RLS (cliente, advogado, processo, movimento, conversa_estado). Use antes de escrever uma nova função de serviço, task Celery, rota, ou fixture de teste que faz select/insert/update nessas tabelas — e ao debugar ObjectDeletedError, DetachedInstanceError, ou uma query que "não encontra nada" mesmo com dado no banco.
---

# Escrever com RLS no AdvogAI

O isolamento multi-tenant é feito no banco (RLS), não na aplicação —
CLAUDE.md §4.4. Toda tabela de negócio (`cliente`, `advogado`, `processo`,
`movimento`, `conversa_estado`) só mostra linhas se `app.tenant_id` estiver
setado na sessão via `definir_tenant(session, tenant_id)`
(`app/db/rls.py`). `tenant` em si não tem RLS (ver comentário na migration
0001).

## A armadilha: `definir_tenant` é `SET LOCAL`

`set_config(..., true)` só vale **para a transação atual**. Isso significa:

- Todo `session.commit()` ou `session.rollback()` encerra a transação — e
  junto, o `app.tenant_id` setado nela. A próxima query já roda sem tenant
  definido, e a RLS simplesmente não retorna nada (nem erro, nem exceção —
  silêncio total).
- Um objeto ORM carregado numa transação e usado **depois** de um
  commit/rollback fica expirado. Acessar qualquer atributo dele
  (`objeto.campo`) dispara um refresh que também exige `app.tenant_id`
  setado — se não estiver, o erro que aparece é `ObjectDeletedError` (linha
  "sumiu", mas na real é RLS bloqueando o refresh) ou, se a sessão já foi
  fechada, `DetachedInstanceError`. Isso já aconteceu várias vezes neste
  projeto (`sync_processual.py`, `enviar_notificacoes.py`,
  `test_services_aprovacoes.py`) e o sintoma engana — parece um bug de
  dado sumido, mas é sempre isso.

## Checklist antes de escrever a função

1. **Toda função "ponto de entrada"** (corpo de task Celery, service
   chamado direto por uma rota) chama `definir_tenant(session, tenant_id)`
   como a **primeira coisa que faz** — antes de qualquer select/insert.
2. **Se a função itera em loop com commit/rollback por item** (padrão dos
   workers: `sync_processual.py`, `enviar_notificacoes.py`,
   `solicitar_aprovacoes.py`), chame `definir_tenant` de novo **a cada
   iteração**, não só uma vez no começo do loop.
3. **Antes de rolar back ou comitar**, se você ainda vai precisar de algum
   dado do objeto ORM depois (num log de erro, num assert de teste, etc),
   capture esse valor como primitivo (`str`, `uuid.UUID`, etc) **antes** —
   não confie no objeto depois do commit/rollback.
4. **Escrevendo uma fixture de teste**: se o helper cria dados e termina
   com `session.commit()`, e o teste depois chama uma função de baixo
   nível diretamente (não um "ponto de entrada" que já redefine o tenant
   sozinho, tipo `processar_mensagem`), chame `definir_tenant(session,
   tenant_id)` de novo logo após o commit, antes de chamar a função sob
   teste. Ver `_criar_cenario` em `tests/test_services_aprovacoes.py` como
   exemplo do padrão certo.

## Se o sintoma já apareceu

- Query RLS-protegida devolvendo lista vazia sem motivo aparente → primeira
  suspeita: `app.tenant_id` não está setado nesta transação. Confirme se
  houve um commit/rollback entre o `definir_tenant` mais recente e essa
  query.
- `ObjectDeletedError` ou `DetachedInstanceError` num teste ou numa task →
  mesma causa: acesso a atributo de objeto ORM expirado sem tenant setado
  na transação atual.
