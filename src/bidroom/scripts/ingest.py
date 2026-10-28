"""Fase 2 — ingestão do inventário de currículos para o banco demo.

Pré-requisito: banco criado com `scripts/build_db.py` (schema + configuração).

Uso:
  python scripts/ingest.py --db data/demo.db --csv data/inventario_curriculos.csv
  python scripts/ingest.py --db data/demo.db --csv data/inventario_curriculos.csv --limite 5
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# bootstrap: rodar via `python scripts/ingest.py` ou importado nos testes
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.governance.vault import CofrePII
from src.ingestion.db import conectar
from src.ingestion.pipeline import ingerir_inventario


def _contagens(con) -> list[tuple[str, int]]:
    tabelas = [
        "candidatos",
        "curriculos",
        "experiencias",
        "competencias",
        "certificacoes",
        "formacoes",
        "idiomas",
        "evidencias",
        "residual",
        "cofre_dados_pessoais",
        "registros_auditoria",
    ]
    return [
        (t, con.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"])
        for t in tabelas
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/demo.db")
    parser.add_argument("--csv", default="data/inventario_curriculos.csv")
    parser.add_argument("--limite", type=int, default=None)
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"erro: banco nao encontrado em {args.db}")
        print("rode antes: python scripts/build_db.py --db " + args.db)
        return 2

    con = conectar(args.db)
    try:
        cofre = CofrePII.carregar_ou_criar()
        resumo = ingerir_inventario(con, cofre, args.csv, limite=args.limite)
        print("ingestao concluida")
        print(f"  total: {resumo.total}")
        print(f"  processados: {resumo.processados}")
        print(f"  duplicados: {resumo.duplicados}")
        print(f"  erros: {resumo.erros}")
        print(f"  candidatos criados: {resumo.candidatos_criados}")
        print(f"  fatos extraidos: {resumo.fatos}")
        print(f"  pii no cofre: {resumo.pii_gravada}")
        print("contagens no banco:")
        for tabela, total in _contagens(con):
            print(f"  {tabela}: {total}")
        if resumo.detalhes_erros:
            print("falhas:")
            for detalhe in resumo.detalhes_erros:
                print(f"  - {detalhe}")
            return 1
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
