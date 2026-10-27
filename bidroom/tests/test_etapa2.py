"""Etapa 2 — ciclo contínuo, fila de revisão e propagação."""
from __future__ import annotations

import json
from datetime import date, timedelta

from scripts.build_db import construir
from src.governance import ciclo
from src.governance.atualidade import garantir_atualidade
from src.ingestion.db import conectar

HOJE = date(2026, 10, 27)


def _base(tmp_path):
    db = tmp_path / "ciclo.db"
    construir(db)
    con = conectar(db)
    papeis = {
        row["sigla"]: int(row["id"])
        for row in con.execute("SELECT id, sigla FROM papeis")
    }
    candidato_id = int(
        con.execute(
            "INSERT INTO candidatos (nome_pseudonimo) VALUES ('Candidato Ciclo')"
        ).lastrowid
    )
    curriculo_id = int(
        con.execute(
            "INSERT INTO curriculos (candidato_id, nome_arquivo, caminho_cache,"
            " checksum, n_paginas, versao) VALUES (?, 'ciclo.docx', 'x', 'ciclo', 1, 1)",
            (candidato_id,),
        ).lastrowid
    )
    con.execute(
        "INSERT INTO competencias (curriculo_id, nome) VALUES (?, 'Java')",
        (curriculo_id,),
    )
    cert_id = int(
        con.execute(
            "INSERT INTO certificacoes (curriculo_id, nome, ano)"
            " VALUES (?, 'PMP', 2020)",
            (curriculo_id,),
        ).lastrowid
    )
    con.execute(
        "INSERT INTO experiencias (curriculo_id, papel, empresa, data_inicio, data_fim)"
        " VALUES (?, 'Analista', 'Empresa', '2020', '2025')",
        (curriculo_id,),
    )
    con.commit()
    garantir_atualidade(con, candidato_id)

    aberta_a = int(
        con.execute(
            "INSERT INTO disputas (nome_ficticio, preset_versao_id, status)"
            " VALUES ('Aberta A', 1, 'aberta')"
        ).lastrowid
    )
    aberta_b = int(
        con.execute(
            "INSERT INTO disputas (nome_ficticio, preset_versao_id, status)"
            " VALUES ('Aberta B', 1, 'aberta')"
        ).lastrowid
    )
    fechada = int(
        con.execute(
            "INSERT INTO disputas (nome_ficticio, preset_versao_id, status)"
            " VALUES ('Fechada', 1, 'fechada')"
        ).lastrowid
    )
    con.execute(
        "INSERT INTO alocacoes (disputa_id, papel_id, candidato_id)"
        " VALUES (?, ?, ?)",
        (aberta_a, papeis["pm_scrum"], candidato_id),
    )
    con.execute(
        "INSERT INTO pontuacoes (disputa_id, candidato_id, papel_id,"
        " versao_preset_id, curriculo_versao_id, pontuacao_ponderada,"
        " confianca, pontuacao_final, detalhe_json)"
        " VALUES (?, ?, ?, 1, ?, 50, 50, 50, '{}')",
        (aberta_a, candidato_id, papeis["pm_scrum"], curriculo_id),
    )
    con.commit()
    return con, candidato_id, curriculo_id, cert_id, aberta_a, aberta_b, fechada


def test_disponibilidade_propaga_para_todas_abertas_e_preserva_decisao(tmp_path):
    con, candidato_id, _, _, aberta_a, aberta_b, fechada = _base(tmp_path)
    try:
        resultado = ciclo.atualizar_disponibilidade(
            con,
            candidato_id,
            inicio="2026-11-01",
            fim="2026-12-01",
            percentual=50,
            fonte="teste",
            ator="teste",
        )
        assert set(resultado["disputas"]) == {aberta_a, aberta_b}
        assert ciclo.classificar_disputa(con, aberta_a)["status"] == "pendente"
        assert ciclo.classificar_disputa(con, aberta_b)["status"] == "pendente"
        assert ciclo.classificar_disputa(con, fechada)["status"] == "atualizada"

        pendentes = ciclo.listar_atualizacoes_pendentes(con, disputa_id=aberta_a)
        assert len(pendentes) == 1
        detalhes = json.loads(pendentes[0]["detalhes_json"])
        assert detalhes["snapshot_decisao"]["alocacoes"]
        assert detalhes["snapshot_decisao"]["pontuacoes"]

        # Decisão anterior continua no banco enquanto o recálculo está pendente.
        assert con.execute(
            "SELECT COUNT(*) c FROM alocacoes WHERE disputa_id = ?", (aberta_a,)
        ).fetchone()["c"] == 1
        assert con.execute(
            "SELECT COUNT(*) c FROM pontuacoes WHERE disputa_id = ?", (aberta_a,)
        ).fetchone()["c"] == 1
    finally:
        con.close()


def test_recalculo_resolve_pendencia_sem_apagar_alocacao(tmp_path):
    con, candidato_id, _, _, aberta_a, _, _ = _base(tmp_path)
    try:
        ciclo.atualizar_disponibilidade(
            con,
            candidato_id,
            inicio="2026-11-01",
            fim="2026-12-01",
            percentual=50,
            ator="teste",
        )
        resultado = ciclo.recalcular_pendentes(con, ator="teste")
        assert aberta_a in resultado["recalculadas"]
        assert not resultado["falhas"]
        assert ciclo.classificar_disputa(con, aberta_a)["status"] == "atualizada"
        assert con.execute(
            "SELECT COUNT(*) c FROM alocacoes WHERE disputa_id = ?", (aberta_a,)
        ).fetchone()["c"] == 1
    finally:
        con.close()


def test_vencimento_por_tempo_cria_pendencia_idempotente(tmp_path):
    con, _, _, cert_id, aberta_a, _, _ = _base(tmp_path)
    try:
        con.execute(
            "UPDATE atualidade_fatos SET valido_ate = ?"
            " WHERE tipo_fato = 'certificacao' AND fato_id = ?",
            ((HOJE - timedelta(days=1)).isoformat(), cert_id),
        )
        con.commit()
        primeiro = ciclo.verificar_vencimentos(con, hoje=HOJE, ator="teste")
        assert any(
            item["fato_id"] == cert_id for item in primeiro["vencidos_fatos"]
        )
        assert con.execute(
            "SELECT estado FROM atualidade_fatos WHERE tipo_fato = 'certificacao'"
            " AND fato_id = ?",
            (cert_id,),
        ).fetchone()["estado"] == "vencido"
        assert ciclo.classificar_disputa(con, aberta_a)["status"] == "pendente"

        segundo = ciclo.verificar_vencimentos(con, hoje=HOJE, ator="teste")
        assert segundo["vencidos_fatos"] == []
        pendentes = ciclo.listar_atualizacoes_pendentes(con, disputa_id=aberta_a)
        assert len([p for p in pendentes if p["tipo"] == "vencimento"]) == 1
    finally:
        con.close()


def test_nova_certificacao_entra_como_declarada_e_gera_comprovacao(tmp_path):
    con, candidato_id, _, _, aberta_a, _, _ = _base(tmp_path)
    try:
        antes = con.execute("SELECT COUNT(*) c FROM certificacoes").fetchone()["c"]
        resultado = ciclo.adicionar_certificacao(
            con,
            candidato_id,
            nome="Certificação Nova",
            emissor="Instituição",
            ano=2026,
            ator="teste",
        )
        depois = con.execute("SELECT COUNT(*) c FROM certificacoes").fetchone()["c"]
        assert depois == antes + 1
        assert ciclo.classificar_disputa(con, aberta_a)["status"] == "pendente"
        fila = ciclo.listar_fila(con, candidato_id=candidato_id)
        assert any(item["tipo"] == "comprovacao_certificacao" for item in fila)
        atualidade = con.execute(
            "SELECT ultima_confirmacao, estado FROM atualidade_fatos"
            " WHERE tipo_fato = 'certificacao' AND fato_id = ?",
            (resultado["certificacao_id"],),
        ).fetchone()
        assert atualidade["ultima_confirmacao"] is None
        assert atualidade["estado"] == "declarado"
    finally:
        con.close()


def test_confirmar_sem_mudancas_nao_inventa_qualificacao(tmp_path):
    con, candidato_id, _, _, _, _, _ = _base(tmp_path)
    try:
        ciclo.criar_item_fila(
            con,
            candidato_id,
            "revisao_periodica",
            "Revisão periódica de teste",
            referencia_tipo="competencia",
            referencia_id=1,
        )
        antes = con.execute("SELECT COUNT(*) c FROM certificacoes").fetchone()["c"]
        resultado = ciclo.confirmar_sem_mudancas(con, candidato_id, ator="teste")
        depois = con.execute("SELECT COUNT(*) c FROM certificacoes").fetchone()["c"]
        assert depois == antes
        assert resultado["fatos_confirmados"] >= 2
        assert con.execute(
            "SELECT COUNT(*) c FROM registros_auditoria"
            " WHERE acao = 'confirmar_sem_mudancas'"
        ).fetchone()["c"] == 1
        assert con.execute(
            "SELECT COUNT(*) c FROM fila_revisao WHERE tipo = 'revisao_periodica'"
            " AND status = 'resolvida'"
        ).fetchone()["c"] == 1
    finally:
        con.close()


def test_falha_de_recalculo_fica_visivel_e_pendente(tmp_path, monkeypatch):
    con, candidato_id, _, _, aberta_a, _, _ = _base(tmp_path)
    try:
        ciclo.atualizar_disponibilidade(
            con,
            candidato_id,
            inicio="2026-11-01",
            fim="2026-12-01",
            percentual=50,
            ator="teste",
        )
        import src.scoring.servico as servico

        def falhar(*args, **kwargs):
            raise RuntimeError("falha simulada de recálculo")

        monkeypatch.setattr(servico, "recalcular_disputa", falhar)
        resultado = ciclo.recalcular_pendentes(con, ator="teste")
        assert resultado["falhas"]
        assert ciclo.classificar_disputa(con, aberta_a)["status"] == "pendente"
        pendente = ciclo.listar_atualizacoes_pendentes(con, disputa_id=aberta_a)[0]
        assert "falha simulada" in (pendente["erro"] or "")
    finally:
        con.close()
