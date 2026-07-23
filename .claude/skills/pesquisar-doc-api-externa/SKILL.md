---
name: pesquisar-doc-api-externa
description: Extrai detalhes técnicos (autenticação, endpoints, payloads, webhooks) da doc de um provedor externo do AdvogAI (DataJud, Judit, UAZAPI, Meta Cloud API) quando a doc é um site renderizado em JS e WebFetch não funciona. Use ao integrar ou revisar um ProcessProvider (app/providers/) ou ChannelProvider (app/channels/) novo, ou quando o usuário pedir para pesquisar/consultar a doc de um provedor.
---

# Pesquisar doc de provedor externo (AdvogAI)

Mesma situação de sempre neste projeto: a doc de provedor (UAZAPI, e
provavelmente Judit e Meta Cloud API quando chegar a vez) costuma ser uma SPA
— `WebFetch` retorna só o título ou HTML vazio. Nesse caso, use o browser
(`mcp__Claude_Browser__*`) em vez de insistir com `WebFetch`.

## Técnica

A técnica genérica de navegação (usar `read_page` + clicar por `ref` em vez
de coordenada de screenshot, preencher token fake no painel "Try It" pra
revelar o header de auth real na aba "Code", procurar seção "Schemas" pro
payload de webhook, navegar em endpoints de auth sem token pra distinguir
API real de página de marketing) está detalhada na skill pessoal
`pesquisar-doc-api-externa` (`~/.claude/skills/pesquisar-doc-api-externa/`).
Ela dispara nesta sessão porque é mais específica ao repositório atual —
segue igual.

## Específico do AdvogAI

- **Nunca** peça o token/API key pelo chat — oriente a colocar em `.env`
  (`ADVOGAI_<PROVIDER>_TOKEN`, etc., seguindo o padrão de
  `app/core/config.py`) e leia via `Settings`.
- O que você descobrir (base URL, header de auth, formato de payload) só
  pode ser usado dentro de `app/providers/<provider>.py` (`ProcessProvider`)
  ou `app/channels/<provider>.py` (`ChannelProvider`) — CLAUDE.md §4.2 e §4.7
  proíbem qualquer outro lugar do código conhecer o formato de payload do
  fornecedor.
- Depois de pesquisar, salve os achados numa memória de referência (tipo
  `advogai_<provider>_referencia.md`, seguindo o padrão de
  `advogai_uazapi_referencia.md` já salva) — evita repetir a pesquisa do
  zero na próxima sessão.
