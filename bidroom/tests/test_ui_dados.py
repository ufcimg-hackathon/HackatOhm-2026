"""Fase 4 — testes da camada de dados, resumo e recálculo da UI."""
from __future__ import annotations

import json

import pytest

from scripts.build_db import construir
from src.governance.vault import CofrePII, salvar
from src.ingestion.db import conectar
from src.scoring.contexto import ContextoCandidato, carregar_contexto
from src.scoring.engine import ResultadoScore
from src.scoring.resumo import gerar_resumo_proposta
from src.scoring.servico import recalcular_candidato
from src.ui import dados


def _detalhe(status="revisar", score_base=50.0) -> dict:
    categorias = []
    for nome, peso in [
        ("aderencia", 30),
        ("dominio_energia", 20),
        ("certificacoes", 15),
        ("experiencia", 20),
        ("idioma", 5),
        ("educacao", 5),
        ("ferramentas", 5),
    ]:
        categorias.append(
            {
                "categoria": nome,
                "score": score_base,
                "peso": peso,
                "confianca": 25,
                "contribuicao": round(score_base * peso / 100, 2),
                "detalhe": {"presentes": ["Java", "SQL"], "faltantes": ["Gestão de projetos"]}
                if nome == "aderencia"
                else (
                    {"experiencias_energia": 1}
                    if nome == "dominio_energia"
                    else {"encontradas": [{"nome": "PMP", "nivel": "N0", "contribuicao": 0}]}
                    if nome == "certificacoes"
                    else {"anos": 8}
                ),
            }
        )
    return {
        "categorias": categorias,
        "gates": [
            {"gate": "G1", "status": "passou", "motivo": "ok"},
            {"gate": "G5", "status": "revisar", "motivo": "disponibilidade"},
        ],
        "status_gates": status,
        "ancoras_pm": [
            {"nome": "PMP", "peso": 10, "declarada": True, "atingida": False, "contribuicao": 0},
            {"nome": "Energia", "peso": 8, "atingida": True, "contribuicao": 4},
        ],
        "anos_experiencia": 8,
    }


@pytest.fixture()
def demo(tmp_path):
    db = tmp_path / "ui.db"
    construir(db)
    con = conectar(db)
    papeis = {
        row["sigla"]: int(row["id"])
        for row in con.execute("SELECT id, sigla FROM papeis")
    }
    candidato = con.execute(
        "INSERT INTO candidatos (nome_pseudonimo, status_curacao, elegivel_brasil)"
        " VALUES ('Candidato 001', 'curado', 'sim')"
    ).lastrowid

    def curriculo(versao: int, checksum: str) -> int:
        return int(
            con.execute(
                "INSERT INTO curriculos (candidato_id, nome_arquivo, caminho_cache,"
                " checksum, n_paginas, versao) VALUES (?, ?, 'x', ?, 3, ?)",
                (candidato, f"v{versao}.docx", checksum, versao),
            ).lastrowid
        )

    c1 = curriculo(1, "checksum-v1")
    c2 = curriculo(2, "checksum-v2")
    exp = con.execute(
        "INSERT INTO experiencias (curriculo_id, empresa, papel, dominio,"
        " data_inicio, data_fim, tecnologias, resumo)"
        " VALUES (?, 'EDP', 'PM', 'energia', '2015', '2026', '[]', 'Atuação em energia.')",
        (c2,),
    ).lastrowid
    con.execute(
        "INSERT INTO evidencias (tipo_fato, fato_id, trecho, pagina, nivel, confianca)"
        " VALUES ('experiencia', ?, 'Projeto Client: EDP com energia.', 2, 3, 75)",
        (exp,),
    )
    comp = con.execute(
        "INSERT INTO competencias (curriculo_id, nome, anos, familia)"
        " VALUES (?, 'Java', 8, 'linguagem')",
        (c2,),
    ).lastrowid
    con.execute(
        "INSERT INTO evidencias (tipo_fato, fato_id, trecho, pagina, nivel, confianca)"
        " VALUES ('competencia', ?, 'Java em projeto.', 1, 2, 50)",
        (comp,),
    )
    con.execute(
        "INSERT INTO disputas (nome_ficticio, preset_versao_id) VALUES ('Demo UI', 1)"
    )
    disputa_id = int(con.execute("SELECT id FROM disputas").fetchone()["id"])
    for papel in ("pm_scrum", "ba_bsa", "java", "bi_data"):
        con.execute(
            "INSERT INTO pontuacoes (disputa_id, candidato_id, papel_id,"
            " versao_preset_id, curriculo_versao_id, pontuacao_ponderada,"
            " confianca, pontuacao_final, detalhe_json)"
            " VALUES (?, ?, ?, 1, ?, 50, 25, 55, ?)",
            (disputa_id, candidato, papeis[papel], c2, json.dumps(_detalhe())),
        )
    cofre = CofrePII(CofrePII.gerar_chave())
    salvar(
        con,
        cofre,
        int(candidato),
        "email",
        "segredo@exemplo.com",
        ator="teste",
    )
    con.commit()
    yield con, db, int(candidato), c1, c2, disputa_id
    con.close()


def test_ranking_usa_ultimo_curriculo_e_explica_score(demo):
    con, _db, candidato, _c1, c2, disputa_id = demo
    ranking = dados.carregar_ranking(con, disputa_id, 1)
    assert len(ranking) == 4
    assert set(ranking["curriculo_versao_id"]) == {c2}
    linha = ranking.iloc[0]
    assert linha["candidato_id"] == candidato
    assert linha["score"] == 55.0
    assert linha["evidencia_max"] == 3
    assert linha["gates_passou"] == 1
    assert linha["gates_revisar"] == 1
    assert "PMP" in linha["ancoras"]
    assert "Energia" in linha["ancoras"]


def test_dashboard_conta_banco_real(demo):
    con, _db, _candidato, _c1, _c2, _disputa_id = demo
    dash = dados.carregar_dashboard(con)
    assert dash["total_curriculos"] == 2
    assert dash["total_candidatos"] == 1
    assert dash["status"]["curado"] == 1
    assert dash["curados_ou_alocados"] == 1
    assert dash["faixa"]["max"] == 55.0
    assert dash["por_papel"]
    assert dash["ultima_ingestao"]


def test_perfil_mascara_pii_e_traz_evidencia(demo):
    con, _db, candidato, _c1, c2, _disputa_id = demo
    perfil = dados.carregar_perfil(con, candidato)
    assert perfil is not None
    assert perfil["curriculo"]["id"] == c2
    assert perfil["pii"]["email"] == 1
    assert "segredo@exemplo.com" not in str(perfil)
    assert perfil["experiencias"][0]["evidencia"]["nivel"] == 3
    assert perfil["competencias"][0]["evidencia"]["nivel"] == 2
    assert perfil["contexto"].anos_experiencia >= 0


def test_resumo_proposta_usa_fatos_e_gates():
    from src.scoring.contexto import CompetenciaFato, ExperienciaFato
    from src.scoring.engine import ResultadoScore

    contexto = ContextoCandidato(candidato_id=1, curriculo_id=1)
    contexto.competencias = [CompetenciaFato("Java", 50)]
    contexto.experiencias = [ExperienciaFato("energia", 50, "2015", "2026")]
    contexto.anos_experiencia = 8
    detalhe = _detalhe()
    resultado = ResultadoScore(
        papel="java",
        pontuacao_ponderada=50,
        confianca=25,
        pontuacao_final=55,
        status_gates="revisar",
        gates=[],
        detalhe=detalhe,
    )
    frases = gerar_resumo_proposta("Candidato 001", "Java", resultado, contexto)
    texto = " ".join(frases)
    assert 3 <= len(frases) <= 5
    assert "energia" in texto
    assert "PMP" in texto
    assert "Score final 55.00" in texto
    assert "segredo" not in texto


def test_recalcular_candidato_grava_quatro_papeis(demo):
    con, _db, candidato, _c1, _c2, disputa_id = demo
    con.execute("DELETE FROM pontuacoes WHERE disputa_id = ?", (disputa_id,))
    resultados = recalcular_candidato(con, candidato, ator="teste")
    assert len(resultados) == 4
    total = con.execute(
        "SELECT COUNT(*) c FROM pontuacoes WHERE disputa_id = ?", (disputa_id,)
    ).fetchone()["c"]
    assert total == 4
