"""Fase 8 — monta o estado de curadoria da demo a partir de um CSV humano.

A lista dos 24–30 curados é decisão do time; este script só aplica a lista.
Aceita CSV com uma destas colunas: `candidato_id`, `nome_arquivo` ou
`nome_pseudonimo`. Não inventa curadoria e não altera `data/demo.db` por
padrão: o caminho é sempre explícito.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
from pathlib import Path

from src.governance.auditoria import registrar
from src.ingestion.db import conectar, criar_schema


def _resolver_candidato(con: sqlite3.Connection, linha: dict) -> int | None:
    if linha.get("candidato_id"):
        row = con.execute(
            "SELECT id FROM candidatos WHERE id = ?", (int(linha["candidato_id"]),)
        ).fetchone()
        return int(row["id"]) if row else None
    if linha.get("nome_arquivo"):
        row = con.execute(
            "SELECT candidato_id FROM curriculos WHERE nome_arquivo = ?"
            " ORDER BY versao DESC, id DESC LIMIT 1",
            (linha["nome_arquivo"],),
        ).fetchone()
        return int(row["candidato_id"]) if row else None
    if linha.get("nome_pseudonimo"):
        row = con.execute(
            "SELECT id FROM candidatos WHERE nome_pseudonimo = ?",
            (linha["nome_pseudonimo"],),
        ).fetchone()
        return int(row["id"]) if row else None
    return None


def seed(db: str | Path, curadoria: str | Path, ator: str = "seed_demo") -> dict:
    """Marca como `curado` os candidatos listados no CSV."""
    con = conectar(db)
    try:
        criar_schema(con)
        with Path(curadoria).open(encoding="utf-8-sig", newline="") as arquivo:
            linhas = list(csv.DictReader(arquivo))
        if not linhas:
            raise ValueError("CSV de curadoria vazio")
        encontrados: list[int] = []
        ausentes: list[str] = []
        for linha in linhas:
            candidato_id = _resolver_candidato(con, linha)
            if candidato_id is None:
                ausentes.append(str(linha))
                continue
            con.execute(
                "UPDATE candidatos SET status_curacao = 'curado',"
                " atualizado_em = datetime('now') WHERE id = ?",
                (candidato_id,),
            )
            encontrados.append(candidato_id)
        registrar(
            con,
            acao="seed_curadoria",
            ator=ator,
            entidade="candidatos",
            entidade_id=None,
            detalhes={
                "curadoria": str(curadoria),
                "marcados": len(set(encontrados)),
                "ausentes": len(ausentes),
            },
            commit=False,
        )
        con.commit()
        return {
            "marcados": len(set(encontrados)),
            "ausentes": ausentes,
        }
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="banco de destino")
    parser.add_argument("--curadoria", required=True, help="CSV humano de curadoria")
    parser.add_argument("--ator", default="seed_demo")
    args = parser.parse_args(argv)
    try:
        resultado = seed(args.db, args.curadoria, ator=args.ator)
    except Exception as exc:  # noqa: BLE001 - CLI reporta a causa
        print(f"Falha ao aplicar curadoria: {exc}")
        return 2
    print(
        f"Curadoria aplicada: {resultado['marcados']} candidato(s) marcado(s); "
        f"{len(resultado['ausentes'])} linha(s) não encontrada(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
