#!/usr/bin/env python3
"""Espera o Railway concluir o redeploy de um commit depois de um git push.

Não existe webhook de "build terminou" no CLI da Railway — só polling. O sinal
usado aqui é `railway status --json`, que expõe, por serviço, o
`latestDeployment` com `meta.commitHash` e `status`. Comparar o commit é o
único critério que não dá falso positivo: "Starting Container" no log aparece
mesmo quando o build falhou e o container antigo continuou no ar, e o
`deployment ID` muda também em redeploy do commit velho.

Uso:
    aguardar_deploy.py [--service NOME ...] [--commit SHA]
                       [--max-checks N] [--interval SEGUNDOS]

Sem --service, espera os três serviços de aplicação (AdvogaAI, Worker, Beat).
Sem --commit, usa o HEAD local.

Sai com 0 quando todos os serviços pedidos estão no commit alvo com status
SUCCESS; 1 se algum falhar (FAILED/CRASHED) ou se esgotar as tentativas.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

SERVICOS_PADRAO = ("AdvogaAI", "Worker", "Beat")
_STATUS_FINAL_RUIM = {"FAILED", "CRASHED", "REMOVED", "SKIPPED"}


def _rodar(comando: list[str]) -> str:
    resultado = subprocess.run(comando, capture_output=True, text=True)
    if resultado.returncode != 0:
        raise RuntimeError(f"{' '.join(comando)} falhou: {resultado.stderr.strip()}")
    return resultado.stdout


def _deployments(ambiente: str) -> dict[str, tuple[str, str]]:
    """Mapeia serviço -> (commit do último deployment, status)."""
    dados = json.loads(_rodar(["railway", "status", "--json"]))
    for aresta in dados["environments"]["edges"]:
        ambiente_node = aresta["node"]
        if ambiente_node["name"] != ambiente:
            continue
        resultado: dict[str, tuple[str, str]] = {}
        for instancia in ambiente_node["serviceInstances"]["edges"]:
            node = instancia["node"]
            ultimo = node.get("latestDeployment") or {}
            commit = (ultimo.get("meta") or {}).get("commitHash") or ""
            resultado[node["serviceName"]] = (commit, ultimo.get("status") or "DESCONHECIDO")
        return resultado
    raise RuntimeError(f"ambiente {ambiente!r} não encontrado no railway status")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", action="append", dest="servicos")
    parser.add_argument("--commit")
    parser.add_argument("--environment", default="production")
    parser.add_argument("--max-checks", type=int, default=20)
    parser.add_argument("--interval", type=int, default=15)
    args = parser.parse_args()

    servicos = args.servicos or list(SERVICOS_PADRAO)
    alvo = args.commit or _rodar(["git", "rev-parse", "HEAD"]).strip()
    print(f"esperando {', '.join(servicos)} chegarem no commit {alvo[:8]}")

    pendentes = set(servicos)
    for check in range(1, args.max_checks + 1):
        atual = _deployments(args.environment)
        desconhecidos = pendentes - atual.keys()
        if desconhecidos:
            print(f"serviço(s) inexistente(s) no Railway: {', '.join(sorted(desconhecidos))}")
            return 1

        for servico in sorted(pendentes):
            commit, status = atual[servico]
            if not commit.startswith(alvo[:8]):
                print(f"check {check}: {servico} ainda no commit {commit[:8] or '<nenhum>'}")
                continue
            if status == "SUCCESS":
                print(f"check {check}: {servico} online no commit alvo")
                pendentes.discard(servico)
            elif status in _STATUS_FINAL_RUIM:
                print(f"check {check}: {servico} falhou o deploy ({status})", file=sys.stderr)
                return 1
            else:
                print(f"check {check}: {servico} em {status}")

        if not pendentes:
            print("deploy concluído em todos os serviços.")
            return 0
        time.sleep(args.interval)

    print(
        f"esgotou {args.max_checks} tentativas; ainda pendente(s): "
        f"{', '.join(sorted(pendentes))}",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
