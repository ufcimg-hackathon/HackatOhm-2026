"""Etapa 1 — atualidade, validade e pendências."""
from __future__ import annotations

from datetime import date, timedelta

from scripts.build_db import construir
from src.governance.atualidade import (
    classificar,
    confirmar_fato,
    garantir_atualidade,
    resumo_candidato,
)
from src.ingestion.db import conectar

HOJE = date(2026, 10, 27)


def _candidato_com_fatos(con, nome: str, checksum: str) -> tuple[int, int, int]:
    candidato_id = int(
        con.execute(
            "INSERT INTO candidatos (nome_pseudonimo) VALUES (?)", (nome,)
        ).lastrowid
    )
    curriculo_id = int(
        con.execute(
            "INSERT INTO curriculos (candidato_id, nome_arquivo, caminho_cache,"
            " checksum, n_paginas, versao) VALUES (?, ?, 'x', ?, 1, 1)",
            (candidato_id, f"{nome}.docx", checksum),
        ).lastrowid
    )
    competencia_id = int(
        con.execute(
            "INSERT INTO competencias (curriculo_id, nome) VALUES (?, 'Java')",
            (curriculo_id,),
        ).lastrowid
    )
    con.execute(
        "INSERT INTO certificacoes (curriculo_id, nome, ano) VALUES (?, 'PMP', 2020)",
        (curriculo_id,),
    )
    con.commit()
    return candidato_id, curriculo_id, competencia_id


def test_dois_perfis_com_confirmacoes_diferentes_tem_situacoes_diferentes(tmp_path):
    db = tmp_path / "atualidade.db"
    construir(db)
    con = conectar(db)
    try:
        recente, _, _ = _candidato_com_fatos(con, "Recente", "recente")
        antigo, _, _ = _candidato_com_fatos(con, "Antigo", "antigo")
        garantir_atualidade(con, recente)
        garantir_atualidade(con, antigo)
        con.execute(
            "UPDATE atualidade_fatos SET ultima_confirmacao = ?"
            " WHERE tipo_fato = 'competencia' AND fato_id = ("
            "   SELECT id FROM competencias WHERE curriculo_id = ("
            "     SELECT id FROM curriculos WHERE candidato_id = ?))",
            ((HOJE - timedelta(days=10)).isoformat(), recente),
        )
        con.execute(
            "UPDATE atualidade_fatos SET ultima_confirmacao = ?"
            " WHERE tipo_fato = 'competencia' AND fato_id = ("
            "   SELECT id FROM competencias WHERE curriculo_id = ("
            "     SELECT id FROM curriculos WHERE candidato_id = ?))",
            ((HOJE - timedelta(days=400)).isoformat(), antigo),
        )
        con.commit()

        resumo_recente = resumo_candidato(con, recente, hoje=HOJE)
        resumo_antigo = resumo_candidato(con, antigo, hoje=HOJE)
        assert resumo_recente["por_status"]["atualizado"] >= 1
        assert resumo_antigo["por_status"]["desatualizado"] >= 1
        assert resumo_recente["por_status"] != resumo_antigo["por_status"]
    finally:
        con.close()


def test_certificacao_vencida_e_disponibilidade_desconhecida_geram_pendencia(tmp_path):
    db = tmp_path / "pendencias.db"
    construir(db)
    con = conectar(db)
    try:
        candidato_id, curriculo_id, _ = _candidato_com_fatos(
            con, "Pendencias", "pendencias"
        )
        garantir_atualidade(con, candidato_id)
        cert = con.execute(
            "SELECT id FROM certificacoes WHERE curriculo_id = ?", (curriculo_id,)
        ).fetchone()
        con.execute(
            "UPDATE atualidade_fatos SET valido_ate = ?,"
            " ultima_confirmacao = ? WHERE tipo_fato = 'certificacao'"
            " AND fato_id = ?",
            (
                (HOJE - timedelta(days=1)).isoformat(),
                (HOJE - timedelta(days=30)).isoformat(),
                cert["id"],
            ),
        )
        con.commit()
        resumo = resumo_candidato(con, candidato_id, hoje=HOJE)
        tipos = {(p["tipo_fato"], p["frescura"]) for p in resumo["pendencias"]}
        assert ("certificacao", "vencido") in tipos
        assert any(
            p["tipo_fato"] == "disponibilidade"
            and p["frescura"] == "sem_confirmacao"
            for p in resumo["pendencias"]
        )
        assert resumo["disponibilidade"]["estado"] == "desconhecido"
    finally:
        con.close()


def test_confirmacao_explicita_atualiza_sem_criar_fato_novo(tmp_path):
    db = tmp_path / "confirmar.db"
    construir(db)
    con = conectar(db)
    try:
        candidato_id, curriculo_id, competencia_id = _candidato_com_fatos(
            con, "Confirmar", "confirmar"
        )
        garantir_atualidade(con, candidato_id)
        antes = con.execute("SELECT COUNT(*) c FROM competencias").fetchone()["c"]
        confirmar_fato(con, "competencia", competencia_id, ator="teste")
        depois = con.execute("SELECT COUNT(*) c FROM competencias").fetchone()["c"]
        assert depois == antes
        resumo = resumo_candidato(con, candidato_id, hoje=date.today())
        item = next(
            i
            for i in resumo["itens"]
            if i["tipo_fato"] == "competencia" and i["fato_id"] == competencia_id
        )
        assert item["ultima_confirmacao"] is not None
        assert item["frescura"] == "atualizado"
        assert con.execute(
            "SELECT COUNT(*) c FROM registros_auditoria"
            " WHERE acao = 'confirmar_atualidade'"
        ).fetchone()["c"] == 1
    finally:
        con.close()


def test_classificar_limiares_e_validade():
    assert classificar(None, None, HOJE) == "sem_confirmacao"
    assert classificar((HOJE - timedelta(days=10)).isoformat(), None, HOJE) == "atualizado"
    assert classificar((HOJE - timedelta(days=200)).isoformat(), None, HOJE) == "atencao"
    assert (
        classificar((HOJE - timedelta(days=400)).isoformat(), None, HOJE)
        == "desatualizado"
    )
    assert (
        classificar(
            (HOJE - timedelta(days=10)).isoformat(),
            (HOJE - timedelta(days=1)).isoformat(),
            HOJE,
        )
        == "vencido"
    )
