"""Etapa 6 — composição viável, requisitos coletivos e conflitos."""
from __future__ import annotations

import json

import pytest

from src.ingestion.db import conectar, criar_schema
from src.scoring.composicao import (
    avaliar_composicao,
    recomendar_composicao,
    validar_selecao_demanda,
)
from src.scoring.demanda import carregar_demanda
from src.scoring.time import salvar_alocacao
from streamlit.testing.v1 import AppTest


def _base(con, *, com_coletivo: bool = True):
    edital_id = con.execute(
        "INSERT INTO editais (nome, cliente_ficticio, status)"
        " VALUES ('Edital composição', 'Cliente', 'ativo')"
    ).lastrowid
    versao_id = con.execute(
        "INSERT INTO edital_versoes (edital_id, numero, nome_arquivo, caminho,"
        " checksum, status) VALUES (?, 1, 'edital.docx', 'data/editais/edital.docx',"
        " 'checksum-composicao', 'ativa')",
        (edital_id,),
    ).lastrowid
    requisitos = [
        ("papel", "individual", "Papel A", {"nome": "Papel A", "sigla": "pa", "vagas": 1}),
        ("papel", "individual", "Papel B", {"nome": "Papel B", "sigla": "pb", "vagas": 1}),
    ]
    if com_coletivo:
        requisitos.append(
            (
                "regra_combinacao",
                "equipe",
                "A equipe deve ter pelo menos um PMP e um CBAP",
                {"texto": "a equipe deve ter pelo menos um PMP e um CBAP"},
            )
        )
    for tipo, escopo, descricao, regra in requisitos:
        con.execute(
            "INSERT INTO edital_requisitos (edital_versao_id, tipo, escopo,"
            " chave, descricao, regra_json, trecho, localizacao, pagina,"
            " obrigatorio, ambiguidade, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'teste', 1, 1, 0, 'aprovado')",
            (
                versao_id,
                tipo,
                escopo,
                f"req-{descricao}",
                descricao,
                json.dumps(regra, ensure_ascii=False),
                descricao,
            ),
        )
    disputa_id = con.execute(
        "INSERT INTO disputas (nome_ficticio, cliente_ficticio, edital_versao_id,"
        " status) VALUES ('Demanda composição', 'Cliente', ?, 'aberta')",
        (versao_id,),
    ).lastrowid
    con.commit()
    carregar_demanda(con, versao_id)
    papeis = {
        row["sigla"]: int(row["id"])
        for row in con.execute("SELECT id, sigla FROM papeis")
    }
    return int(versao_id), int(disputa_id), papeis


def _candidato(con, nome: str, *, certs=(), comps=()):
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
    for cert in certs:
        fato_id = con.execute(
            "INSERT INTO certificacoes (curriculo_id, nome) VALUES (?, ?)",
            (curriculo_id, cert),
        ).lastrowid
        con.execute(
            "INSERT INTO evidencias (tipo_fato, fato_id, trecho, nivel, confianca)"
            " VALUES ('certificacao', ?, 'trecho', 3, 100)",
            (fato_id,),
        )
    for comp in comps:
        fato_id = con.execute(
            "INSERT INTO competencias (curriculo_id, nome) VALUES (?, ?)",
            (curriculo_id, comp),
        ).lastrowid
        con.execute(
            "INSERT INTO evidencias (tipo_fato, fato_id, trecho, nivel, confianca)"
            " VALUES ('competencia', ?, 'trecho', 3, 100)",
            (fato_id,),
        )
    con.commit()
    return int(candidato_id), int(curriculo_id)


def _score(con, disputa_id, versao_id, papel_id, candidato, nota, status="elegivel"):
    candidato_id, curriculo_id = candidato
    con.execute(
        "INSERT INTO pontuacoes_demanda (disputa_id, candidato_id, papel_id,"
        " edital_versao_id, curriculo_versao_id, nota_tecnica,"
        " score_interno_apoio, confianca, atualidade, status, detalhe_json)"
        " VALUES (?, ?, ?, ?, ?, ?, 80, 90, 90, ?, ?)",
        (
            disputa_id,
            candidato_id,
            papel_id,
            versao_id,
            curriculo_id,
            nota,
            status,
            json.dumps(
                {
                    "ancoras": [{"descricao": f"Âncora {candidato_id}"}],
                    "contagens": {"atendidos": 1, "pendentes": 0},
                },
                ensure_ascii=False,
            ),
        ),
    )
    con.commit()


def _coletivo(con, versao_id: int, texto: str, *, valor: int | None = None):
    regra = {"texto": texto}
    if valor is not None:
        regra["valor"] = valor
    con.execute(
        "INSERT INTO edital_requisitos (edital_versao_id, tipo, escopo,"
        " chave, descricao, regra_json, trecho, localizacao, pagina,"
        " obrigatorio, ambiguidade, status)"
        " VALUES (?, ?, 'equipe', ?, ?, ?, ?, 'teste', 1, 1, 0, 'aprovado')",
        (
            versao_id,
            "limite" if valor is not None else "regra_combinacao",
            f"req-{texto}",
            texto,
            json.dumps(regra, ensure_ascii=False),
            texto,
        ),
    )
    con.commit()


def _selecoes(rec):
    return [
        {
            "papel_id": slot["papel_id"],
            "vaga": slot["vaga"],
            "candidato_id": slot["candidato"]["candidato_id"],
        }
        for slot in rec.slots
        if slot["candidato"]
    ]


def test_composicao_encontra_otimo_com_requisito_coletivo(tmp_path):
    con = conectar(tmp_path / "etapa6.db")
    try:
        criar_schema(con)
        versao_id, disputa_id, papeis = _base(con)
        x = _candidato(con, "X", certs=["PMP"])
        y = _candidato(con, "Y", certs=["CBAP"])
        z = _candidato(con, "Z")
        _score(con, disputa_id, versao_id, papeis["pa"], x, 100)
        _score(con, disputa_id, versao_id, papeis["pa"], y, 80)
        _score(con, disputa_id, versao_id, papeis["pb"], x, 90)
        _score(con, disputa_id, versao_id, papeis["pb"], z, 60)

        rec = recomendar_composicao(
            con, disputa_id, limite_candidatos_papel=100
        )
        assert rec.objetivo == 190.0
        # A gulosa coloca X em pa e sobra Z em pb: 160 + 10 (PMP) - 10 (CBAP).
        assert rec.objetivo_guloso == 160.0
        assert rec.diferenca_guloso == 30.0
        assert rec.status == "viavel"
        assert rec.cobertura == 1.0
        escolhidos = {
            slot["papel"]: slot["candidato"]["nome"]
            for slot in rec.slots
            if slot["candidato"]
        }
        assert escolhidos == {"pa": "Y", "pb": "X"}
        assert {item["status"] for item in rec.requisitos_coletivos} == {
            "atendido"
        }
    finally:
        con.close()


def test_composicao_sem_candidato_e_sem_coletivo_atendido(tmp_path):
    con = conectar(tmp_path / "etapa6_vazio.db")
    try:
        criar_schema(con)
        versao_id, disputa_id, papeis = _base(con)
        x = _candidato(con, "X")
        _score(con, disputa_id, versao_id, papeis["pa"], x, 100, status="eliminado")
        rec = recomendar_composicao(con, disputa_id)
        assert rec.status == "incompleta"
        assert rec.cobertura == 0.0
        assert any("descoberta" in item for item in rec.pendencias)
        assert all(slot["candidato"] is None for slot in rec.slots)
    finally:
        con.close()


def test_coletivo_nao_atendido_gera_penalidade_e_pendencia(tmp_path):
    con = conectar(tmp_path / "etapa6_coletivo.db")
    try:
        criar_schema(con)
        versao_id, disputa_id, papeis = _base(con)
        x = _candidato(con, "X")
        y = _candidato(con, "Y")
        _score(con, disputa_id, versao_id, papeis["pa"], x, 100)
        _score(con, disputa_id, versao_id, papeis["pb"], y, 80)
        rec = recomendar_composicao(con, disputa_id)
        assert rec.nota_total == 180.0
        assert rec.penalidade_coletiva == 20.0
        assert rec.objetivo == 160.0
        assert rec.status == "pendente"
        assert any(
            item["status"] == "nao_atendido"
            for item in rec.requisitos_coletivos
        )
        assert any(risco["tipo"] == "coletivo_nao_atendido" for risco in rec.riscos)
    finally:
        con.close()


def test_conflito_entre_disputas_exclui_candidato_e_rejeita_salvamento(tmp_path):
    con = conectar(tmp_path / "etapa6_conflito.db")
    try:
        criar_schema(con)
        versao_id, disputa_id, papeis = _base(con)
        x = _candidato(con, "X", certs=["PMP"])
        y = _candidato(con, "Y", certs=["CBAP"])
        _score(con, disputa_id, versao_id, papeis["pa"], x, 100)
        _score(con, disputa_id, versao_id, papeis["pb"], y, 80)
        outra = con.execute(
            "INSERT INTO disputas (nome_ficticio, cliente_ficticio, status)"
            " VALUES ('Outra', 'Cliente', 'aberta')"
        ).lastrowid
        con.execute(
            "INSERT INTO alocacoes (disputa_id, papel_id, candidato_id, status)"
            " VALUES (?, ?, ?, 'alocado')",
            (outra, papeis["pa"], x[0]),
        )
        con.commit()

        rec = recomendar_composicao(con, disputa_id)
        assert rec.slots[0]["candidato"] is None  # X está em conflito
        assert any(risco["tipo"] == "cobertura_incompleta" for risco in rec.riscos)

        with pytest.raises(ValueError, match="já está alocado"):
            validar_selecao_demanda(
                con,
                disputa_id,
                [
                    {
                        "papel_id": papeis["pa"],
                        "candidato_id": x[0],
                    }
                ],
            )
        with pytest.raises(ValueError, match="já está alocado"):
            salvar_alocacao(
                con,
                disputa_id,
                [
                    {"papel_id": papeis["pa"], "candidato_id": x[0]},
                    {"papel_id": papeis["pb"], "candidato_id": y[0]},
                ],
            )
    finally:
        con.close()


def test_avaliacao_manual_e_salvamento_de_equipe_viavel(tmp_path):
    con = conectar(tmp_path / "etapa6_salvar.db")
    try:
        criar_schema(con)
        versao_id, disputa_id, papeis = _base(con)
        x = _candidato(con, "X", certs=["PMP"])
        y = _candidato(con, "Y", certs=["CBAP"])
        _score(con, disputa_id, versao_id, papeis["pa"], x, 100)
        _score(con, disputa_id, versao_id, papeis["pb"], y, 80)
        rec = recomendar_composicao(con, disputa_id)
        selecoes = _selecoes(rec)
        avaliada = avaliar_composicao(con, disputa_id, selecoes)
        assert avaliada.objetivo == rec.objetivo
        assert avaliada.status == "viavel"

        resultado = salvar_alocacao(con, disputa_id, selecoes, ator="teste")
        assert resultado["alocados"] == 2
        alocados = con.execute(
            "SELECT candidato_id FROM alocacoes WHERE disputa_id = ?"
            " AND status = 'alocado'",
            (disputa_id,),
        ).fetchall()
        assert {row["candidato_id"] for row in alocados} == {x[0], y[0]}
    finally:
        con.close()


def test_limite_por_papel_reduz_cobertura_e_rejeita_excesso(tmp_path):
    con = conectar(tmp_path / "etapa6_limite.db")
    try:
        criar_schema(con)
        versao_id, disputa_id, papeis = _base(con, com_coletivo=False)
        con.execute(
            "UPDATE edital_requisitos SET regra_json = ?"
            " WHERE edital_versao_id = ? AND tipo = 'papel'"
            " AND descricao = 'Papel A'",
            (
                json.dumps(
                    {"nome": "Papel A", "sigla": "pa", "vagas": 2},
                    ensure_ascii=False,
                ),
                versao_id,
            ),
        )
        _coletivo(con, versao_id, "no máximo 1 candidato por papel", valor=1)
        x = _candidato(con, "X")
        y = _candidato(con, "Y")
        z = _candidato(con, "Z")
        _score(con, disputa_id, versao_id, papeis["pa"], x, 100)
        _score(con, disputa_id, versao_id, papeis["pa"], y, 90)
        _score(con, disputa_id, versao_id, papeis["pb"], z, 80)

        rec = recomendar_composicao(con, disputa_id)
        assert rec.status == "incompleta"
        assert rec.cobertura < 1.0
        assert sum(
            1 for slot in rec.slots if slot["papel"] == "pa" and slot["candidato"]
        ) == 1

        selecoes = [
            {"papel_id": papeis["pa"], "vaga": 1, "candidato_id": x[0]},
            {"papel_id": papeis["pa"], "vaga": 2, "candidato_id": y[0]},
            {"papel_id": papeis["pb"], "vaga": 1, "candidato_id": z[0]},
        ]
        avaliada = avaliar_composicao(con, disputa_id, selecoes)
        assert any(
            item.startswith("Limite de 1 por pa excedido")
            for item in avaliada.pendencias
        )
        with pytest.raises(ValueError, match="Limite de 1 por pa excedido"):
            salvar_alocacao(con, disputa_id, selecoes, ator="teste")
    finally:
        con.close()


def test_coletivo_generico_iso_e_recomendacao_muda_com_status(tmp_path):
    con = conectar(tmp_path / "etapa6_generico.db")
    try:
        criar_schema(con)
        versao_id, disputa_id, papeis = _base(con, com_coletivo=False)
        _coletivo(
            con,
            versao_id,
            "pelo menos um profissional com certificação ISO 9001",
        )
        x = _candidato(con, "X", certs=["ISO 9001"])
        y = _candidato(con, "Y")
        _score(con, disputa_id, versao_id, papeis["pa"], x, 100)
        _score(con, disputa_id, versao_id, papeis["pa"], y, 50)
        _score(con, disputa_id, versao_id, papeis["pb"], x, 90)
        _score(con, disputa_id, versao_id, papeis["pb"], y, 80)

        rec = recomendar_composicao(con, disputa_id)
        assert rec.status == "viavel"
        assert any(
            item["alvo"] == "iso 9001" and item["status"] == "atendido"
            for item in rec.requisitos_coletivos
        )
        assert next(
            slot["candidato"]["nome"]
            for slot in rec.slots
            if slot["papel"] == "pa"
        ) == "X"

        con.execute(
            "UPDATE pontuacoes_demanda SET status = 'eliminado'"
            " WHERE disputa_id = ? AND papel_id = ? AND candidato_id = ?",
            (disputa_id, papeis["pa"], x[0]),
        )
        con.commit()
        rec2 = recomendar_composicao(con, disputa_id)
        assert next(
            slot["candidato"]["nome"]
            for slot in rec2.slots
            if slot["papel"] == "pa"
        ) == "Y"
        assert next(
            slot["candidato"]["nome"]
            for slot in rec2.slots
            if slot["papel"] == "pb"
        ) == "X"
    finally:
        con.close()


def test_t4_renderiza_composicao_demanda(tmp_path, monkeypatch):
    from pathlib import Path

    db = tmp_path / "etapa6_ui.db"
    con = conectar(db)
    try:
        criar_schema(con)
        versao_id, disputa_id, papeis = _base(con)
        x = _candidato(con, "X", certs=["PMP"])
        y = _candidato(con, "Y", certs=["CBAP"])
        _score(con, disputa_id, versao_id, papeis["pa"], x, 100)
        _score(con, disputa_id, versao_id, papeis["pb"], y, 80)
    finally:
        con.close()
    monkeypatch.setenv("BIDROOM_DB", str(db))
    raiz = Path(__file__).resolve().parents[1]
    at = AppTest.from_file(str(raiz / "app.py"), default_timeout=40)
    at.session_state["pagina"] = "Alocação"
    at.run()
    assert not at.exception, at.exception
    assert at.title[0].value == "Alocação do time"
    assert any("Recomendação automática" in item.value for item in at.subheader)
