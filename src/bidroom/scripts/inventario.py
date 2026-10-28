"""Fase 0 — converte o dump de links do Drive no CSV de controle.

Colunas do CSV: id, nome_arquivo, nome_candidato,
link_individual, status_download, caminho_cache.

Uso:
  python scripts/inventario.py <caminho_do_dump> --out data/inventario_curriculos.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

# bootstrap: rodar tanto via `python scripts/inventario.py` quanto como pacote
# em testes (`scripts.*` funciona como namespace).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.nome_arquivo import sanitizar

PADRAO_LINK = re.compile(
    r"https://drive\.google\.com/file/d/([A-Za-z0-9_-]+)/view"
)
LINK_DIRETO = "https://drive.google.com/uc?export=download&confirm=t&id={fid}"


def carregar_pares(texto: str) -> list[tuple[str, str]]:
    """Extrai pares (file_id, nome_arquivo) na ordem em que aparecem."""
    pares: list[tuple[str, str]] = []
    id_atual: str | None = None
    for linha in (l.strip() for l in texto.splitlines()):
        achou = PADRAO_LINK.search(linha)
        if achou:
            id_atual = achou.group(1)
            continue
        if id_atual and linha.lower().endswith(".docx"):
            nome = linha.replace("\\_", "_")
            pares.append((id_atual, nome))
            id_atual = None
    return pares


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump", help="caminho do dump de links do Drive")
    parser.add_argument(
        "--out", default="data/inventario_curriculos.csv", help="CSV de controle"
    )
    args = parser.parse_args()

    texto = Path(args.dump).read_text(encoding="utf-8")
    pares = carregar_pares(texto)
    if not pares:
        raise SystemExit("nenhum par id/nome encontrado no dump")

    saida = Path(args.out)
    saida.parent.mkdir(parents=True, exist_ok=True)
    with saida.open("w", newline="", encoding="utf-8-sig") as f:
        gravador = csv.writer(f)
        gravador.writerow(
            [
                "id",
                "nome_arquivo",
                "nome_candidato",
                "link_individual",
                "status_download",
                "caminho_cache",
            ]
        )
        for i, (fid, nome) in enumerate(pares, 1):
            candidato = Path(nome).stem
            caminho = f"data/cache_drive/{sanitizar(nome)}"
            gravador.writerow(
                [
                    i,
                    nome,
                    candidato,
                    LINK_DIRETO.format(fid=fid),
                    "pendente",
                    caminho,
                ]
            )
    print(f"{len(pares)} curriculos escritos em {args.out}")
    print(f"ids unicos: {len({fid for fid, _ in pares})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
