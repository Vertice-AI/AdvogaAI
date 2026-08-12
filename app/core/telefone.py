import re

# Celular brasileiro circula em duas grafias: com e sem o nono dígito. O
# WhatsApp entrega usando a forma SEM o 9 em boa parte dos DDDs — confirmado em
# produção (2026-08-12): enviamos "5581994065983" e a UAZAPI respondeu
# chatid "558194065983". Já o cadastro feito à mão quase sempre usa a forma com
# o 9. Sem uma forma canônica única, o mesmo cliente vira dois registros e a
# verificação de identidade (CLAUDE.md §4.6) nunca casa — o cliente cai pra
# sempre no fluxo de "número não vinculado".
#
# Esta regra é de domínio (numeração brasileira), não formato de payload da
# UAZAPI — por isso mora em core/ e não em channels/ (CLAUDE.md §4.7).
_DDI_BRASIL = "55"
_TAMANHO_COM_NONO_DIGITO = 13
# O WhatsApp só dispensa o nono dígito no JID a partir do DDD 31; de 11 a 30 ele
# mantém. Manter o 9 é o lado seguro da dúvida: a UAZAPI aceita a forma com 9 e
# resolve sozinha (confirmado em produção — enviamos "5581994065983" e ela
# entregou), enquanto remover onde não devia geraria número inexistente.
_PRIMEIRO_DDD_SEM_NONO_DIGITO = 31


def normalizar_numero(bruto: str) -> str:
    """Reduz um telefone/JID à forma canônica usada pelo WhatsApp.

    Aceita as duas grafias do mesmo número na entrada ("5581994065983" e
    "558194065983" devolvem o mesmo valor), além de JID completo
    ("5581994065983@s.whatsapp.net") e máscara com separadores.

    Números fora do padrão de celular brasileiro (outro DDI, tamanho
    diferente) só têm os separadores removidos — não inventamos regra de
    numeração de país que não conhecemos.
    """
    digitos = re.sub(r"\D", "", bruto)
    if not digitos.startswith(_DDI_BRASIL) or len(digitos) != _TAMANHO_COM_NONO_DIGITO:
        return digitos
    ddd, assinante = digitos[2:4], digitos[4:]
    if not assinante.startswith("9") or int(ddd) < _PRIMEIRO_DDD_SEM_NONO_DIGITO:
        return digitos
    return f"{_DDI_BRASIL}{ddd}{assinante[1:]}"
