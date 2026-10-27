"""Fase 4 — testes do score do time e da persistência da alocação."""
from __future__ import annotations

import pytest

from scripts.build_db import construir
from src.ingestion.db import conectar
from src.scoring.time import (
    SlotAlocado,
    calcular_score_time,
    carregar_alocacoes,
    listar_vagas,
    salvar_alocacao,
)

VAGAS = {"pm_scrum": 1, "ba_bsa": 1, "java": 2, "bi_data": 1}


def _slot(
    papel: str,
    score: float,
    *,
    confianca: float = 60.0,
    dominio: float = 50.0,
    dias: int = 10,
    status: str = "revisar",
) -> SlotAlocado:
    return SlotAlocado(
        papel=papel,
        papel_nome=papel,
        vaga=1,
        candidato_id=1,
        candidato_nome="Candidato Teste",
        score=score,
        confianca=confianca,
        dominio_energia=dominio,
        dias_ingestao=dias,
        status_gates=status,
    )


def test_score_time_exemplo_do_doc_78_mais_5_menos_5():
    slots = [
        _slot("pm_scrum", 75),
        _slot("ba_bsa", 80),
        _slot("java", 78),
        _slot("java", 72),
        _slot("bi_data", 85),
    ]
    resultado = calcular_score_time(slots, VAGAS, {"pm_scrum": 4, "ba_bsa": 4, "java": 8, "bi_data": 1})
    assert resultado.media == 78.0
    assert resultado.bonus_cobertura == 5.0
    assert resultado.penalidades["fonte_unica"] == 5.0
    assert resultado.score_final == 78.0
    assert any(r["tipo"] == "fonte_unica" for r in resultado.riscos)


def test_score_time_sem_penalidades_quando_time_completo_e_saudavel():
    slots = [
        _slot("pm_scrum", 75),
        _slot("ba_bsa", 80),
        _slot("java", 78),
        _slot("java", 72),
        _slot("bi_data", 85),
    ]
    resultado = calcular_score_time(slots, VAGAS, {p: 5 for p in VAGAS})
    assert resultado.score_final == 83.0
    assert sum(resultado.penalidades.values()) == 0


def test_score_time_penaliza_confianca_dominio_e_curriculo_antigo():
    slots = [
        _slot("pm_scrum", 50, confianca=20, dominio=0, dias=400),
        _slot("ba_bsa", 50, confianca=20, dominio=0, dias=400),
        _slot("java", 50, confianca=20),
        _slot("java", 50, confianca=20),
        _slot("bi_data", 50, confianca=20),
    ]
    resultado = calcular_score_time(slots, VAGAS, {p: 5 for p in VAGAS})
    assert resultado.penalidades["confianca"] == 5.0
    assert resultado.penalidades["lacuna_dominio"] == 5.0
    assert resultado.penalidades["curriculo_antigo"] == 5.0
    assert resultado.score_final == pytest.approx(40.0)


def test_score_time_cobertura_parcial():
    slots = [_slot("pm_scrum", 50)]
    resultado = calcular_score_time(slots, VAGAS, {p: 5 for p in VAGAS})
    assert resultado.preenchidas == 1
    assert resultado.bonus_cobertura == 1.0
    assert any(r["tipo"] == "cobertura_incompleta" for r in resultado.riscos)


def test_listar_vagas_le_quantidade_do_preset(tmp_path):
    db = tmp_path / "demo.db"
    construir(db)
    con = conectar(db)
    try:
        vagas = listar_vagas(con)
        assert len(vagas) == 5
        assert [v["papel"] for v in vagas].count("java") == 2
    finally:
        con.close()


def test_salvar_alocacao_persiste_e_remove_anterior(tmp_path):
    db = tmp_path / "demo.db"
    construir(db)
    con = conectar(db)
    try:
        papeis = {
            row["sigla"]: row["id"]
            for row in con.execute("SELECT id, sigla FROM papeis")
        }
        candidatos = []
        for indice in range(1, 7):
            cur = con.execute(
                "INSERT INTO candidatos (nome_pseudonimo) VALUES (?)",
                (f"Candidato {indice:03d}",),
            )
            candidatos.append(int(cur.lastrowid))
        con.execute(
            "INSERT INTO disputas (nome_ficticio, preset_versao_id)"
            " VALUES ('Demo', 1)"
        )
        disputa_id = con.execute("SELECT id FROM disputas").fetchone()["id"]

        selecoes = [
            {"papel_id": papeis["pm_scrum"], "candidato_id": candidatos[0]},
            {"papel_id": papeis["ba_bsa"], "candidato_id": candidatos[1]},
            {"papel_id": papeis["java"], "candidato_id": candidatos[2]},
            {"papel_id": papeis["java"], "candidato_id": candidatos[3]},
            {"papel_id": papeis["bi_data"], "candidato_id": candidatos[4]},
        ]
        resumo = salvar_alocacao(con, disputa_id, selecoes)
        assert resumo["alocados"] == 5
        assert len(carregar_alocacoes(con, disputa_id)) == 5
        assert con.execute(
            "SELECT COUNT(*) c FROM registros_auditoria"
            " WHERE acao = 'salvar_alocacao'"
        ).fetchone()["c"] == 1
        status = con.execute(
            "SELECT status_curacao FROM candidatos WHERE id = ?",
            (candidatos[0],),
        ).fetchone()["status_curacao"]
        assert status == "alocado"

        selecoes[0]["candidato_id"] = candidatos[5]
        salvar_alocacao(con, disputa_id, selecoes)
        status_antigo = con.execute(
            "SELECT status_curacao FROM candidatos WHERE id = ?",
            (candidatos[0],),
        ).fetchone()["status_curacao"]
        assert status_antigo == "curado"
        assert len(carregar_alocacoes(con, disputa_id)) == 5

        with pytest.raises(ValueError):
            salvar_alocacao(
                con,
                disputa_id,
                [
                    {"papel_id": papeis["pm_scrum"], "candidato_id": candidatos[5]},
                    {"papel_id": papeis["ba_bsa"], "candidato_id": candidatos[5]},
                    {"papel_id": papeis["java"], "candidato_id": candidatos[2]},
                    {"papel_id": papeis["java"], "candidato_id": candidatos[3]},
                    {"papel_id": papeis["bi_data"], "candidato_id": candidatos[4]},
                ],
            )
    finally:
        con.close()
