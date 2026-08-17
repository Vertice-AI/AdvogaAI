---
name: aguardar-deploy-railway
description: Use depois de um `git push` neste projeto (AdvogAI, hospedado no Railway) quando precisar confirmar que o redeploy automático realmente pegou o commit novo antes de continuar — ex. rodar um teste real, disparar uma task, ou dizer "terminei" pro usuário. Gatilhos — "espera o deploy", "confirma que subiu no Railway", "aguarda o redeploy", "verifica se pegou o commit novo", ou sempre que for necessário validar em produção logo depois de um push. Evita o falso positivo de "Starting Container" que não significa que o build novo terminou (já aconteceu nesta sessão).
---

# Aguardar deploy no Railway

Depois de um `git push` num serviço linkado ao Railway (`AdvogaAI`, `Worker`,
`Beat` neste projeto), não tem webhook nem callback de "build terminou" no
CLI — só dá pra confirmar por polling.

**O único sinal confiável é o commit.** `railway status --json` expõe, por
serviço, `latestDeployment.meta.commitHash` e `latestDeployment.status`:
comparar o hash com o `HEAD` local responde exatamente a pergunta "o que está
no ar é o meu código?". As alternativas erram: "Starting Container" no log
aparece mesmo quando o build falhou e o container antigo continuou no ar
(`8ebef2f`, "força novo deploy: build anterior falhou por problema de snapshot
no Railway"), e o `deployment ID` muda também num redeploy do commit velho.

## Uso

```bash
.claude/skills/aguardar-deploy-railway/scripts/aguardar_deploy.py
```

Sem argumentos, espera os três serviços de aplicação chegarem no `HEAD` local.
Flags: `--service NOME` (repetível, restringe a lista), `--commit SHA`,
`--environment` (padrão `production`), `--max-checks N` (padrão 20) e
`--interval SEGUNDOS` (padrão 15).

Sai com 0 quando todos os serviços pedidos estão no commit alvo com status
`SUCCESS`; 1 se algum deploy falhar (`FAILED`/`CRASHED`), se o serviço não
existir, ou se esgotar as tentativas. Depois do 0, é seguro rodar teste real
ou dizer que o deploy foi concluído.

Só usa `json`/`subprocess` da biblioteca padrão — roda no Python do sistema,
sem venv.

## Serviços deste projeto

`AdvogaAI` (API/webhooks), `Worker` (Celery), `Beat` (scheduler). Um push na
`main` redeploya os três, e é isso que o script espera por padrão. Use
`--service Worker` quando só o worker importar pro teste seguinte — os três
buildam a mesma imagem separadamente e um pode terminar bem depois do outro.

`Postgres` e `Redis` também aparecem no `railway status --json`, mas não têm
commit (`meta.commitHash` vazio) — não passe eles em `--service`, o script
nunca os daria como prontos.
