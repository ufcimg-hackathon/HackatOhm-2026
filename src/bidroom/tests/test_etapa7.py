"""Etapa 7 — cultura, metas e reconhecimento."""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from src.governance import reconhecimento
from src.ingestion.db import conectar, criar_schema


def _candidato(
    con,
    nome: str,
    certificacao: str,
    ano: int,
    data_conclusao: str | None = None,
    *,
    evidencia: bool = True,
):
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
        " VALUES (?, ?, ?, ?)",
        (curriculo_id, certificacao, ano, data_conclusao),
    ).lastrowid
    if evidencia:
        con.execute(
            "INSERT INTO evidencias_documentais"
            " (tipo_fato, fato_id, nome_arquivo, caminho, checksum, status,"
            " valido_ate) VALUES ('certificacao', ?, 'prova.docx', 'prova.docx',"
            " ?, 'validado', '2027-12-31')",
            (certificacao_id, f"ev-{certificacao_id}"),
        )
    con.commit()
    return {
        "candidato_id": int(candidato_id),
        "curriculo_id": int(curriculo_id),
        "certificacao_id": int(certificacao_id),
    }


def _programa(con, nome="Cultura 2026", ano=2026, **kwargs):
    return reconhecimento.criar_programa(con, nome, ano, **kwargs)


def test_conquista_idempotente_e_ano_da_conclusao_manda(tmp_path):
    con = conectar(tmp_path / "etapa7.db")
    try:
        criar_schema(con)
        p26 = _programa(con, "Cultura 2026", 2026)
        p25 = _programa(con, "Cultura 2025", 2025)
        a = _candidato(con, "Ana", "PMP", 2026, "2026-03-01")
        b = _candidato(con, "Bia", "CBAP", 2025, "2025-05-01")

        primeiro = reconhecimento.sincronizar_conquistas(con, p26["id"])
        assert primeiro["criadas"] == 1
        segundo = reconhecimento.sincronizar_conquistas(con, p26["id"])
        assert segundo["criadas"] == 0
        ranking26 = reconhecimento.ranking_cultural(con, p26["id"])
        assert [item["nome"] for item in ranking26] == ["Ana"]
        assert ranking26[0]["total"] == 1

        # O comprovante de Bia foi validado em 2026, mas a conclusão é 2025:
        # ela não entra no ranking de 2026.
        assert reconhecimento.ranking_cultural(con, p26["id"])[0]["candidato_id"] == a[
            "candidato_id"
        ]
        reconhecimento.sincronizar_conquistas(con, p25["id"])
        ranking25 = reconhecimento.ranking_cultural(con, p25["id"])
        assert [item["nome"] for item in ranking25] == ["Bia"]
        assert ranking25[0]["candidato_id"] == b["candidato_id"]
    finally:
        con.close()


def test_rejeicao_vencimento_e_revogacao_corrigem_contagem(tmp_path):
    con = conectar(tmp_path / "etapa7_revogacao.db")
    try:
        criar_schema(con)
        programa = _programa(con)
        a = _candidato(con, "Ana", "PMP", 2026, "2026-03-01")
        reconhecimento.sincronizar_conquistas(con, programa["id"])
        assert reconhecimento.ranking_cultural(con, programa["id"])[0]["total"] == 1

        con.execute(
            "UPDATE evidencias_documentais SET status = 'rejeitado'"
            " WHERE fato_id = ?",
            (a["certificacao_id"],),
        )
        con.commit()
        assert reconhecimento.ranking_cultural(con, programa["id"]) == []
        reconhecimento.sincronizar_conquistas(con, programa["id"])
        assert reconhecimento.ranking_cultural(con, programa["id"]) == []

        con.execute(
            "UPDATE evidencias_documentais SET status = 'validado'"
            " WHERE fato_id = ?",
            (a["certificacao_id"],),
        )
        con.commit()
        reconhecimento.sincronizar_conquistas(con, programa["id"])
        assert reconhecimento.ranking_cultural(con, programa["id"])[0]["total"] == 1

        conquista = reconhecimento.listar_conquistas(
            con, candidato_id=a["candidato_id"], programa_id=programa["id"]
        )[0]
        reconhecimento.revogar_conquista(
            con, conquista["id"], motivo="certificado revogado pelo emissor"
        )
        assert reconhecimento.ranking_cultural(con, programa["id"]) == []
        terceiro = reconhecimento.sincronizar_conquistas(con, programa["id"])
        assert terceiro["ignoradas"] == 1
        assert reconhecimento.ranking_cultural(con, programa["id"]) == []
        with pytest.raises(ValueError):
            reconhecimento.validar_conquista(
                con, conquista["id"], revisor="rh"
            )
    finally:
        con.close()


def test_desempate_publicado_e_explicavel(tmp_path):
    con = conectar(tmp_path / "etapa7_desempate.db")
    try:
        criar_schema(con)
        recente = _programa(
            con,
            "Recente",
            2026,
            regra={"desempate": "data_conclusao_mais_recente"},
        )
        nome = _programa(
            con, "Nome", 2026, regra={"desempate": "nome"}
        )
        a = _candidato(con, "Ana", "PMP", 2026, "2026-01-01")
        b = _candidato(con, "Bia", "CBAP", 2026, "2026-06-01")

        reconhecimento.sincronizar_conquistas(con, recente["id"])
        ranking = reconhecimento.ranking_cultural(con, recente["id"])
        assert [item["nome"] for item in ranking] == ["Bia", "Ana"]
        assert all(
            item["regra_desempate"] == "data_conclusao_mais_recente"
            for item in ranking
        )

        reconhecimento.sincronizar_conquistas(con, nome["id"])
        ranking_nome = reconhecimento.ranking_cultural(con, nome["id"])
        assert [item["nome"] for item in ranking_nome] == ["Ana", "Bia"]
        assert [item["posicao"] for item in ranking_nome] == [1, 2]
    finally:
        con.close()


def test_metas_e_proximos_passos_do_colaborador(tmp_path):
    con = conectar(tmp_path / "etapa7_metas.db")
    try:
        criar_schema(con)
        programa = _programa(con)
        a = _candidato(
            con, "Ana", "PMP", 2026, "2026-03-01", evidencia=False
        )
        meta_id = reconhecimento.criar_meta(
            con,
            a["candidato_id"],
            descricao="Concluir certificação PMP",
            programa_id=programa["id"],
            certificacao_alvo="PMP",
            prazo="2026-12-31",
        )
        metas = reconhecimento.listar_metas(
            con, candidato_id=a["candidato_id"]
        )
        assert metas[0]["status"] == "pendente"

        resumo = reconhecimento.resumo_participacao(
            con, a["candidato_id"], programa_id=programa["id"]
        )
        assert resumo["total_conquistas_validadas"] == 0
        assert any(
            "Enviar comprovação de PMP" in item
            for item in resumo["proximos_passos"]
        )
        assert any(
            "Concluir meta" in item for item in resumo["proximos_passos"]
        )

        atualizada = reconhecimento.atualizar_meta(
            con, meta_id, "concluida", ator="rh"
        )
        assert atualizada["status"] == "concluida"
        assert atualizada["concluido_em"]
        assert reconhecimento.listar_metas(
            con, candidato_id=a["candidato_id"]
        )[0]["status"] == "concluida"
    finally:
        con.close()


def test_t6_renderiza_aba_reconhecimento(tmp_path, monkeypatch):
    from pathlib import Path

    db = tmp_path / "etapa7_ui.db"
    con = conectar(db)
    try:
        criar_schema(con)
        programa = _programa(con)
        _candidato(con, "Ana", "PMP", 2026, "2026-03-01")
        reconhecimento.sincronizar_conquistas(con, programa["id"])
    finally:
        con.close()
    monkeypatch.setenv("BIDROOM_DB", str(db))
    raiz = Path(__file__).resolve().parents[1]
    at = AppTest.from_file(str(raiz / "app.py"), default_timeout=40)
    at.session_state["pagina"] = "Governança"
    at.run()
    assert not at.exception, at.exception
    assert any(
        "Cultura, metas e reconhecimento" in item.value for item in at.subheader
    )
    assert any("Ranking cultural" in item.value for item in at.markdown)
