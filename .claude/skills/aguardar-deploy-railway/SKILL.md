---
name: aguardar-deploy-railway
description: Use depois de um `git push` neste projeto (AdvogAI, hospedado no Railway) quando precisar confirmar que o redeploy automático realmente pegou o commit novo antes de continuar — ex. rodar um teste real, disparar uma task, ou dizer "terminei" pro usuário. Gatilhos — "espera o deploy", "confirma que subiu no Railway", "aguarda o redeploy", "verifica se pegou o commit novo", ou sempre que for necessário validar em produção logo depois de um push. Evita o falso positivo de "Starting Container" que não significa que o build novo terminou (já aconteceu nesta sessão).
---

# Aguardar deploy no Railway

Depois de um `git push` num serviço linkado ao Railway (`AdvogaAI`, `Worker`,
`Beat` neste projeto), não tem webhook nem callback de "build terminou" no
CLI — só dá pra confirmar por polling do `deployment ID` e do status do
serviço.

**Nunca confie só em "Starting Container" no log** — isso pode aparecer
mesmo quando o build falhou silenciosamente e o container antigo continuou
no ar (aconteceu de verdade nesta sessão: `8ebef2f`, "força novo deploy:
build anterior falhou por problema de snapshot no Railway"). O sinal
confiável é o `deployment ID` mudar E o status voltar pra "Online".

## Uso

```bash
scripts/aguardar_deploy.sh --service Worker
```

Sem `--service`, usa o serviço linkado por padrão. Flags opcionais:
`--max-checks N` (padrão 20) e `--interval SEGUNDOS` (padrão 15).

O script:
1. Lê o `deployment ID` atual (antes do push já ter feito efeito).
2. Faz polling até aparecer um `deployment ID` diferente.
3. Continua o polling até o status sair de "Deploying" e virar "Online".
4. Sai com código 0 quando confirmado, ou 1 se esgotar as tentativas
   (nesse caso, checar manualmente — pode ser falha de build).

Depois de confirmado, é seguro rodar testes reais ou dizer que o deploy foi
concluído.

## Serviços deste projeto

`AdvogaAI` (API/webhooks), `Worker` (Celery), `Beat` (scheduler). Um push na
`main` redeploya os três — geralmente só precisa confirmar o serviço
relevante pro que você vai testar em seguida (ex.: `Worker` se mexeu em
`app/workers/`).
