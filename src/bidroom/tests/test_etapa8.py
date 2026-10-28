"""Etapa 8 — jornada integrada: perfil, evidência, edital, equipe e mudança."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from docx import Document

from src.governance import comprovacao, editais
from src.governance.ciclo import atualizar_disponibilidade, recalcular_pendentes
from src.ingestion.db import conectar, criar_schema
from src.scoring.composicao import recomendar_composicao
from src.scoring.contexto import carregar_contexto, carregar_contextos
from src.scoring.demanda import (
    listar_resultados_demanda,
    recalcular_disputa_demanda,
)

LINHAS_EDITAL = [
    "EDITAL DE TESTE — JORNADA COMPLETA",
    "Papel: PM/Scrum Sênior — 1 vaga",
    "Certificação obrigatória: PMP",
    "Período de trabalho: 12 meses",
    "Regra de combinação: a equipe deve ter pelo menos um PMP",
]


def _docx(tmp_path: Path) -> Path:
    caminho = tmp_path / "edital-jornada.docx"
    doc = Document()
    for linha in LINHAS_EDITAL:
        doc.add_paragraph(linha)
    doc.save(caminho)
    return caminho


def _candidato(
    con,
    nome: str,
    *,
    percentual: int = 100,
    evidencia: bool = True,
    tmp_path: Path,
) -> dict:
    candidato_id = con.execute(
        "INSERT INTO candidatos (nome_pseudonimo, status_curacao)"
        " VALUES (?, 'curado')",
        (nome,),
    ).lastrowid
    curriculo_id = con.execute(
        "INSERT INTO curriculos (candidato_id, nome_arquivo, caminho_cache,"
        " checksum, versao, status_processamento)"
        " VALUES (?, ?, ?, ?, 1, 'processado')",
        (candidato_id, f"{nome}.docx", f"cache/{nome}.docx", f"cur-{nome}"),
    ).lastrowid
    certificacao_id = con.execute(
        "INSERT INTO certificacoes (curriculo_id, nome, ano, data_conclusao)"
        " VALUES (?, 'PMP', 2026, '2026-01-15')",
        (curriculo_id,),
    ).lastrowid
    con.execute(
        "INSERT INTO disponibilidades (candidato_id, inicio, fim, percentual,"
        " fonte, responsavel, confirmado_em, valido_ate, estado)"
        " VALUES (?, '2026-01-01', '2027-01-01', ?, 'teste', 'rh',"
        " '2026-01-01', '2027-01-01', 'confirmado')",
        (candidato_id, percentual),
    )
    con.commit()
    evidencia_id = None
    if evidencia:
        anexada = comprovacao.anexar_evidencia(
            con,
            "certificacao",
            int(certificacao_id),
            nome_arquivo=f"pmp-{nome}.docx",
            conteudo=f"comprovante {nome}".encode(),
            ator="candidato",
            valido_ate="2030-12-31",
        )
        comprovacao.validar_evidencia(
            con,
            int(anexada["evidencia_id"]),
            revisor="rh",
            ator="rh",
            valido_ate="2030-12-31",
        )
        evidencia_id = int(anexada["evidencia_id"])
    return {
        "candidato_id": int(candidato_id),
        "curriculo_id": int(curriculo_id),
        "certificacao_id": int(certificacao_id),
        "evidencia_id": evidencia_id,
    }


def _processar_edital(con, tmp_path: Path) -> tuple[int, int]:
    edital_id = editais.criar_edital(
        con, "Edital Jornada", cliente_ficticio="Cliente Fictício"
    )
    caminho = _docx(tmp_path)
    recebido = editais.receber_versao(
        con,
        edital_id,
        nome_arquivo=caminho.name,
        conteudo=caminho.read_bytes(),
        ator="analista",
    )
    versao_id = int(recebido["versao_id"])
    for requisito in editais.listar_requisitos(con, versao_id):
        editais.revisar_requisito(
            con,
            int(requisito["id"]),
            revisor="analista",
            decisao="aprovar",
            observacao="revisão da jornada",
        )
    editais.aprovar_versao(con, versao_id, revisor="analista")
    ativado = editais.ativar_versao(con, versao_id, ator="analista")
    assert ativado["disputas"]
    return edital_id, int(ativado["disputas"][0])


def test_jornada_completa_recomendacao_muda_com_disponibilidade(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BIDROOM_EDITAIS", str(tmp_path / "editais"))
    monkeypatch.setenv("BIDROOM_EVIDENCIAS", str(tmp_path / "evidencias"))
    db = tmp_path / "jornada.db"
    con = conectar(db)
    try:
        criar_schema(con)
        ana = _candidato(con, "Ana", tmp_path=tmp_path)
        bia = _candidato(con, "Bia", percentual=100, tmp_path=tmp_path)
        _, disputa_id = _processar_edital(con, tmp_path)

        recalculado = recalcular_disputa_demanda(
            con, disputa_id, ator="sistema"
        )
        assert recalculado["pontuacoes"] >= 2
        resultados = listar_resultados_demanda(con, disputa_id)
        assert {row["nome_pseudonimo"] for row in resultados} == {"Ana", "Bia"}
        assert all(row["status"] == "elegivel" for row in resultados)

        primeira = recomendar_composicao(con, disputa_id)
        assert primeira.status == "viavel"
        assert primeira.slots[0]["candidato"]["nome"] == "Ana"
        assert primeira.slots[0]["candidato"]["nota_tecnica"] >= 0

        # Mudança posterior no perfil: Ana deixa de ter disponibilidade.
        mudanca = atualizar_disponibilidade(
            con,
            ana["candidato_id"],
            inicio="2026-01-01",
            fim="2027-01-01",
            percentual=0,
            fonte="manual",
            responsavel="rh",
            valido_ate="2027-01-01",
            ator="rh",
        )
        assert mudanca["disputas"]
        pendentes = recalcular_pendentes(con, ator="sistema")
        assert pendentes["falhas"] == []
        assert pendentes["recalculadas"]

        segunda = recomendar_composicao(con, disputa_id)
        assert segunda.status == "viavel"
        assert segunda.slots[0]["candidato"]["nome"] == "Bia"
        assert segunda.slots[0]["candidato"]["nome"] != "Ana"
        resultados_depois = listar_resultados_demanda(con, disputa_id)
        ana_depois = next(
            row for row in resultados_depois if row["nome_pseudonimo"] == "Ana"
        )
        assert ana_depois["status"] != "elegivel"

        acoes = {
            row["acao"]
            for row in con.execute(
                "SELECT acao FROM registros_auditoria ORDER BY id"
            )
        }
        assert "anexar_evidencia" in acoes
        assert "validar_evidencia" in acoes
        assert "receber_edital_versao" in acoes
        assert "ativar_edital_versao" in acoes
        assert "atualizar_disponibilidade" in acoes
        assert "calcular_pontuacoes_demanda" in acoes
    finally:
        con.close()


def test_carga_em_lote_preserva_o_contexto_individual(tmp_path):
    con = conectar(tmp_path / "contexto.db")
    try:
        criar_schema(con)
        candidato_id = con.execute(
            "INSERT INTO candidatos (nome_pseudonimo) VALUES ('Ana')"
        ).lastrowid
        curriculo_id = con.execute(
            "INSERT INTO curriculos (candidato_id, nome_arquivo, caminho_cache,"
            " checksum, versao, status_processamento)"
            " VALUES (?, 'ana.docx', 'cache/ana.docx', 'cur-ana', 1, 'processado')",
            (candidato_id,),
        ).lastrowid
        cert_id = con.execute(
            "INSERT INTO certificacoes (curriculo_id, nome, ano)"
            " VALUES (?, 'PMP', 2026)",
            (curriculo_id,),
        ).lastrowid
        con.execute(
            "INSERT INTO evidencias (tipo_fato, fato_id, trecho, pagina, nivel,"
            " confianca) VALUES ('certificacao', ?, 'PMP', 1, 4, 100)",
            (cert_id,),
        )
        con.execute(
            "INSERT INTO atualidade_fatos (tipo_fato, fato_id, fonte,"
            " ultima_confirmacao, valido_ate, estado)"
            " VALUES ('certificacao', ?, 'teste', '2026-01-01',"
            " '2030-12-31', 'validado')",
            (cert_id,),
        )
        con.execute(
            "INSERT INTO evidencias_documentais (tipo_fato, fato_id,"
            " nome_arquivo, caminho, checksum, status, valido_ate)"
            " VALUES ('certificacao', ?, 'pmp.docx', 'pmp.docx', 'ev-pmp',"
            " 'validado', '2030-12-31')",
            (cert_id,),
        )
        con.commit()
        individual = carregar_contexto(con, int(curriculo_id))
        lote = carregar_contextos(con, [int(curriculo_id)])[int(curriculo_id)]
        assert individual.candidato_id == lote.candidato_id
        assert individual.evidencia_max == lote.evidencia_max
        assert [f.nome for f in individual.certificacoes] == [
            f.nome for f in lote.certificacoes
        ]
        assert individual.certificacoes[0].confianca == 100
        assert lote.certificacoes[0].evidencia_estado == "validado"
    finally:
        con.close()


def test_duas_conexoes_respeitam_unicidade_da_alocacao_ativa(tmp_path):
    db = tmp_path / "concorrencia.db"
    con1 = conectar(db)
    con2 = conectar(db)
    try:
        criar_schema(con1)
        candidato_id = con1.execute(
            "INSERT INTO candidatos (nome_pseudonimo) VALUES ('Ana')"
        ).lastrowid
        papel_id = con1.execute(
            "INSERT INTO papeis (sigla, nome) VALUES ('pm', 'PM')"
        ).lastrowid
        disputa_id = con1.execute(
            "INSERT INTO disputas (nome_ficticio) VALUES ('Disputa')"
        ).lastrowid
        con1.execute(
            "INSERT INTO alocacoes (disputa_id, papel_id, candidato_id, status)"
            " VALUES (?, ?, ?, 'alocado')",
            (disputa_id, papel_id, candidato_id),
        )
        con1.commit()
        with pytest.raises(sqlite3.IntegrityError):
            con2.execute(
                "INSERT INTO alocacoes (disputa_id, papel_id, candidato_id, status)"
                " VALUES (?, ?, ?, 'alocado')",
                (disputa_id, papel_id, candidato_id),
            )
            con2.commit()
    finally:
        con1.close()
        con2.close()
