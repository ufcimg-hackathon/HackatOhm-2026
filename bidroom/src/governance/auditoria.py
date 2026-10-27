"""Fase 1 (4/4) — auditoria: helper único para `registros_auditoria`.

Regra do MVP: qualquer edição de preset/alocação/atualização de currículo e
qualquer leitura de PII gera uma linha. T6 (Fase 6) consome `ultimos()`.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any


def registrar(
    con: sqlite3.Connection,
    acao: str,
    ator: str | None,
    entidade: str,
    entidade_id: int | None = None,
    detalhes: dict[str, Any] | None = None,
    commit: bool = True,
) -> int:
    """Grava uma linha de auditoria e devolve o id."""
    detalhes_json = (
        json.dumps(detalhes, ensure_ascii=False, sort_keys=True)
        if detalhes is not None
        else None
    )
    cur = con.execute(
        "INSERT INTO registros_auditoria (acao, ator, entidade, entidade_id, detalhes_json)"
        " VALUES (?, ?, ?, ?, ?)",
        (acao, ator, entidade, entidade_id, detalhes_json),
    )
    if commit:
        con.commit()
    return cur.lastrowid


def ultimos(con: sqlite3.Connection, limite: int = 20) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM registros_auditoria ORDER BY id DESC LIMIT ?", (limite,)
    ).fetchall()


def por_entidade(
    con: sqlite3.Connection, entidade: str, entidade_id: int, limite: int = 50
) -> list[sqlite3.Row]:
    return con.execute(
        "SELECT * FROM registros_auditoria WHERE entidade = ? AND entidade_id = ?"
        " ORDER BY id DESC LIMIT ?",
        (entidade, entidade_id, limite),
    ).fetchall()
