"""Etapa 3 — comprovação, reenvio idempotente e vencimento."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from scripts.build_db import construir
from src.governance import ciclo
from src.governance.comprovacao import (
    anexar_evidencia,
    estado_comprovacao,
    listar_pendencias_comprovacao,
    rejeitar_evidencia,
    requisito_satisfeito,
    verificar_vencimento_evidencias,
    validar_evidencia,
)
from src.ingestion.db import conectar


def _base(tmp_path):
    db = tmp_path / "prova.db"
    construir(db)
    con = conectar(db)
    candidato_id = int(
        con.execute(
            "INSERT INTO candidatos (nome_pseudonimo) VALUES ('Candidato Prova')"
        ).lastrowid
    )
    curriculo_id = int(
        con.execute(
            "INSERT INTO curriculos (candidato_id, nome_arquivo, caminho_cache,"
            " checksum, n_paginas, versao) VALUES (?, 'prova.docx', 'x', 'prova', 1, 1)",
            (candidato_id,),
        ).lastrowid
    )
    cert_id = int(
        con.execute(
            "INSERT INTO certificacoes (curriculo_id, nome, ano)"
            " VALUES (?, 'PMP', 2020)",
            (curriculo_id,),
        ).lastrowid
    )
    a = int(
        con.execute(
            "INSERT INTO disputas (nome_ficticio, preset_versao_id, status)"
            " VALUES ('Aberta A', 1, 'aberta')"
        ).lastrowid
    )
    b = int(
        con.execute(
            "INSERT INTO disputas (nome_ficticio, preset_versao_id, status)"
            " VALUES ('Aberta B', 1, 'aberta')"
        ).lastrowid
    )
    con.commit()
    return con, candidato_id, cert_id, a, b


def _doc(conteudo: bytes = b"conteudo do comprovante", nome: str = "cert.docx"):
    return {"nome_arquivo": nome, "conteudo": conteudo}


def test_certificacao_percorre_declarado_enviado_validado(tmp_path, monkeypatch):
    monkeypatch.setenv("BIDROOM_EVIDENCIAS", str(tmp_path / "evidencias"))
    con, candidato_id, cert_id, aberta_a, aberta_b = _base(tmp_path)
    try:
        assert estado_comprovacao(con, "certificacao", cert_id) == "declarado"
        assert not requisito_satisfeito(con, "certificacao", cert_id)

        envio = anexar_evidencia(
            con,
            "certificacao",
            cert_id,
            ator="teste",
            **_doc(),
        )
        assert envio["status"] == "enviado"
        assert estado_comprovacao(con, "certificacao", cert_id) == "enviado"
        assert con.execute(
            "SELECT estado FROM atualidade_fatos WHERE tipo_fato = 'certificacao'"
            " AND fato_id = ?",
            (cert_id,),
        ).fetchone()["estado"] == "enviado"
        assert any(
            item["tipo"] == "analise_comprovante"
            for item in ciclo.listar_fila(con, candidato_id=candidato_id)
        )

        validar_evidencia(
            con,
            envio["evidencia_id"],
            revisor="governanca",
            ator="teste",
            valido_ate=(date.today() + timedelta(days=30)).isoformat(),
        )
        assert estado_comprovacao(con, "certificacao", cert_id) == "validado"
        assert requisito_satisfeito(con, "certificacao", cert_id)
        assert con.execute(
            "SELECT estado, ultima_confirmacao FROM atualidade_fatos"
            " WHERE tipo_fato = 'certificacao' AND fato_id = ?",
            (cert_id,),
        ).fetchone()["estado"] == "validado"
        assert ciclo.classificar_disputa(con, aberta_a)["status"] == "pendente"
        assert ciclo.classificar_disputa(con, aberta_b)["status"] == "pendente"
    finally:
        con.close()


def test_reenvio_do_mesmo_documento_nao_cria_duplicata(tmp_path, monkeypatch):
    monkeypatch.setenv("BIDROOM_EVIDENCIAS", str(tmp_path / "evidencias"))
    con, _, cert_id, _, _ = _base(tmp_path)
    try:
        primeiro = anexar_evidencia(
            con, "certificacao", cert_id, ator="teste", **_doc()
        )
        segundo = anexar_evidencia(
            con, "certificacao", cert_id, ator="teste", **_doc()
        )
        assert segundo["evidencia_id"] == primeiro["evidencia_id"]
        assert segundo["duplicado"] is True
        assert con.execute(
            "SELECT COUNT(*) c FROM evidencias_documentais"
        ).fetchone()["c"] == 1
    finally:
        con.close()


def test_rejeicao_e_vencimento_nao_satisfazem_requisito(tmp_path, monkeypatch):
    monkeypatch.setenv("BIDROOM_EVIDENCIAS", str(tmp_path / "evidencias"))
    con, _, cert_id, aberta_a, _ = _base(tmp_path)
    try:
        envio = anexar_evidencia(
            con, "certificacao", cert_id, ator="teste", **_doc(b"rejeitar")
        )
        rejeitar_evidencia(
            con,
            envio["evidencia_id"],
            revisor="governanca",
            motivo="documento ilegível",
            ator="teste",
        )
        assert estado_comprovacao(con, "certificacao", cert_id) == "rejeitado"
        assert not requisito_satisfeito(con, "certificacao", cert_id)
        assert any(
            item["tipo"] == "comprovacao_rejeitada"
            for item in ciclo.listar_fila(con)
        )
        assert ciclo.classificar_disputa(con, aberta_a)["status"] == "pendente"

        novo = anexar_evidencia(
            con, "certificacao", cert_id, ator="teste", **_doc(b"novo")
        )
        validar_evidencia(
            con,
            novo["evidencia_id"],
            revisor="governanca",
            ator="teste",
            valido_ate=(date.today() + timedelta(days=30)).isoformat(),
        )
        con.execute(
            "UPDATE evidencias_documentais SET valido_ate = ? WHERE id = ?",
            ((date.today() - timedelta(days=1)).isoformat(), novo["evidencia_id"]),
        )
        con.commit()
        assert estado_comprovacao(con, "certificacao", cert_id) == "vencido"
        assert not requisito_satisfeito(con, "certificacao", cert_id)
        vencidas = verificar_vencimento_evidencias(con, ator="teste")
        assert any(
            item["evidencia_id"] == novo["evidencia_id"] for item in vencidas
        )
        assert con.execute(
            "SELECT status FROM evidencias_documentais WHERE id = ?",
            (novo["evidencia_id"],),
        ).fetchone()["status"] == "vencido"
    finally:
        con.close()


def test_painel_de_pendencias_lista_certificacao_sem_prova(tmp_path, monkeypatch):
    monkeypatch.setenv("BIDROOM_EVIDENCIAS", str(tmp_path / "evidencias"))
    con, candidato_id, cert_id, _, _ = _base(tmp_path)
    try:
        pendencias = listar_pendencias_comprovacao(con, candidato_id)
        assert any(
            item["tipo_fato"] == "certificacao"
            and item["fato_id"] == cert_id
            and item["estado"] == "declarado"
            for item in pendencias
        )
        with pytest.raises(ValueError):
            anexar_evidencia(
                con,
                "certificacao",
                cert_id,
                ator="teste",
                nome_arquivo="arquivo.pdf",
                conteudo=b"x",
            )
    finally:
        con.close()
