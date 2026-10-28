"""Etapa 5 — cálculo manual conhecido, isolamento e versões antigas."""
from __future__ import annotations

import json
from datetime import date

from src.governance.ciclo import recalcular_pendentes
from src.ingestion.db import conectar, criar_schema
from src.scoring.contexto import (
    CertificacaoFato,
    CompetenciaFato,
    ContextoCandidato,
    ExperienciaFato,
)
from src.scoring.demanda import (
    avaliar_candidato_papel,
    listar_resultados_demanda,
    recalcular_candidato_demanda,
    recalcular_disputa_demanda,
)
from src.ui.dados import carregar_ranking_demanda


def _requisito(
    rid: int,
    tipo: str,
    descricao: str,
    regra: dict,
    *,
    obrigatorio: bool = True,
    escopo: str = "individual",
) -> dict:
    return {
        "id": rid,
        "tipo": tipo,
        "escopo": escopo,
        "descricao": descricao,
        "regra": regra,
        "obrigatorio": obrigatorio,
        "status": "aprovado",
    }


def _demanda(pontos_experiencia: float = 30) -> dict:
    requisitos = [
        _requisito(
            1,
            "papel",
            "Papel Gerente de Projetos",
            {"nome": "Gerente de Projetos", "sigla": "pm", "vagas": 1},
        ),
        _requisito(
            2,
            "competencia",
            "Competência obrigatória: Scrum",
            {"itens": ["Scrum"], "operador": "todas"},
        ),
        _requisito(
            3,
            "experiencia_minima",
            "Experiência mínima de 5 anos em energia",
            {"anos": 5, "dominio": "energia"},
        ),
        _requisito(
            4,
            "certificacao",
            "Certificação PMP",
            {"itens": ["PMP"], "comprovante": False},
        ),
        _requisito(
            5,
            "comprovante",
            "Comprovante de PMP",
            {"itens": ["PMP"]},
        ),
        _requisito(
            6,
            "pontuacao",
            f"{pontos_experiencia:g} pontos para experiência de 5 anos em energia",
            {
                "criterio": (
                    f"{pontos_experiencia:g} pontos para experiência de 5 anos "
                    "em energia"
                ),
                "pontos": pontos_experiencia,
            },
            obrigatorio=False,
        ),
        _requisito(
            7,
            "pontuacao",
            "20 pontos para certificação PMP",
            {"criterio": "20 pontos para certificação PMP", "pontos": 20},
            obrigatorio=False,
        ),
        _requisito(
            8,
            "pontuacao",
            "10 pontos para competência Scrum",
            {"criterio": "10 pontos para competência Scrum", "pontos": 10},
            obrigatorio=False,
        ),
    ]
    return {
        "versao_id": 1,
        "versao": {"numero": 1, "edital_nome": "Demanda de teste"},
        "requisitos": requisitos,
        "papeis": [
            {"papel_id": 1, "sigla": "pm", "nome": "Gerente de Projetos", "vagas": 1}
        ],
        "pontuacao_maxima": pontos_experiencia + 30,
    }


def _ctx(
    *,
    anos: int = 6,
    scrum: bool = True,
    pmp: bool = True,
    prova: str = "validado",
    confianca_pmp: int = 100,
    outras_competencias: list[str] | None = None,
) -> ContextoCandidato:
    ctx = ContextoCandidato(
        candidato_id=1,
        curriculo_id=1,
        referencia=date(2026, 10, 27),
    )
    ctx.competencias = [
        CompetenciaFato(nome, 100)
        for nome in (["Scrum"] if scrum else []) + (outras_competencias or [])
    ]
    ctx.experiencias = [
        ExperienciaFato("energia", 100, "2015", str(2015 + anos), "Energia")
    ]
    if pmp:
        ctx.certificacoes = [
            CertificacaoFato(
                "PMP", confianca_pmp, evidencia_estado=prova
            )
        ]
    return ctx


def test_calculo_manual_conhecido_separa_nota_confianca_e_triagem():
    demanda = _demanda()
    papel = demanda["papeis"][0]

    a = avaliar_candidato_papel(_ctx(), demanda, papel)
    assert a.nota_tecnica == 60.0
    assert a.status == "elegivel"
    assert {r.status for r in a.requisitos} == {"atendido"}
    assert a.detalhe["fonte_nota"] == "edital"

    b = avaliar_candidato_papel(_ctx(anos=3, pmp=False), demanda, papel)
    assert b.nota_tecnica == 28.0  # 18 de experiência + 10 de Scrum
    assert b.status == "eliminado"  # experiência mínima não atendida
    assert any(r.tipo == "experiencia_minima" and r.status == "nao_atendido" for r in b.requisitos)

    c = avaliar_candidato_papel(_ctx(prova="enviado"), demanda, papel)
    assert c.nota_tecnica == 60.0
    assert c.status == "pendente"  # comprovante enviado, ainda não validado

    d = avaliar_candidato_papel(
        _ctx(scrum=False, outras_competencias=["Java"]), demanda, papel
    )
    assert d.nota_tecnica == 50.0  # 30 + 20; competência obrigatória pendente
    assert d.status == "pendente"

    e = avaliar_candidato_papel(_ctx(prova="rejeitado"), demanda, papel)
    assert e.nota_tecnica == 60.0
    assert e.status == "eliminado"

    sem_fatos = _ctx(anos=6, pmp=False, scrum=False)
    sem_fatos.experiencias = []
    f = avaliar_candidato_papel(sem_fatos, demanda, papel)
    assert f.nota_tecnica == 0.0
    assert f.status == "pendente"

    g = avaliar_candidato_papel(
        _ctx(confianca_pmp=0, prova="declarado"), demanda, papel
    )
    assert g.nota_tecnica == 60.0
    assert g.status == "pendente"
    assert g.confianca < a.confianca


def test_pontuacao_usa_alvo_textual_sem_taxonomia_fixa():
    papel = {"papel_id": 1, "sigla": "pm", "nome": "PM", "vagas": 1}
    demanda = {
        "versao_id": 1,
        "versao": {"numero": 1, "edital_nome": "Teste"},
        "requisitos": [
            _requisito(
                1,
                "pontuacao",
                "15 pontos para certificação ISO 9001",
                {"criterio": "15 pontos para certificação ISO 9001", "pontos": 15},
                obrigatorio=False,
            )
        ],
        "papeis": [papel],
        "pontuacao_maxima": 15,
    }
    ctx = ContextoCandidato(
        candidato_id=1, curriculo_id=1, referencia=date(2026, 10, 27)
    )
    ctx.certificacoes = [CertificacaoFato("ISO 9001", 100)]
    resultado = avaliar_candidato_papel(ctx, demanda, papel)
    assert resultado.nota_tecnica == 15.0
    assert resultado.requisitos[0].status == "atendido"


def test_decisao_manual_de_criterio_sem_fato_estruturado():
    demanda = _demanda()
    papel = demanda["papeis"][0]
    manual = _requisito(
        9,
        "criterio_eliminatorio",
        "Disponibilidade para viagens",
        {"resultado": "atendido", "motivo": "confirmado pela área"},
    )
    demanda["requisitos"].append(manual)
    atendido = avaliar_candidato_papel(_ctx(), demanda, papel)
    assert atendido.status == "elegivel"
    assert any(
        item.tipo == "criterio_eliminatorio" and item.status == "atendido"
        for item in atendido.requisitos
    )

    manual["regra"] = {"resultado": "nao_atendido", "motivo": "não viaja"}
    eliminado = avaliar_candidato_papel(_ctx(), demanda, papel)
    assert eliminado.status == "eliminado"


def _inserir_edital(con, *, pontos_experiencia: float = 30, numero: int = 1) -> tuple[int, int]:
    edital_id = int(
        con.execute(
            "INSERT INTO editais (nome, cliente_ficticio, status)"
            " VALUES (?, ?, 'ativo')",
            (f"Edital teste {numero}", "Cliente teste"),
        ).lastrowid
    )
    versao_id = int(
        con.execute(
            "INSERT INTO edital_versoes (edital_id, numero, nome_arquivo, caminho,"
            " checksum, status) VALUES (?, ?, ?, ?, ?, 'ativa')",
            (
                edital_id,
                numero,
                f"edital-{numero}.docx",
                f"data/editais/edital-{numero}.docx",
                f"checksum-edital-{numero}",
            ),
        ).lastrowid
    )
    demanda = _demanda(pontos_experiencia)
    for req in demanda["requisitos"]:
        con.execute(
            "INSERT INTO edital_requisitos (edital_versao_id, tipo, escopo,"
            " chave, descricao, regra_json, trecho, localizacao, pagina,"
            " obrigatorio, ambiguidade, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 0, 'aprovado')",
            (
                versao_id,
                req["tipo"],
                req["escopo"],
                f"req-{req['id']}",
                req["descricao"],
                json.dumps(req["regra"], ensure_ascii=False),
                req["descricao"],
                "teste",
                1 if req["obrigatorio"] else 0,
            ),
        )
    disputa_id = int(
        con.execute(
            "INSERT INTO disputas (nome_ficticio, cliente_ficticio,"
            " edital_versao_id, status) VALUES (?, ?, ?, 'aberta')",
            (f"Demanda {numero}", "Cliente teste", versao_id),
        ).lastrowid
    )
    con.commit()
    return versao_id, disputa_id


def _inserir_candidato(
    con,
    *,
    nome: str,
    anos: int,
    scrum: bool,
    pmp: bool,
    prova: str = "validado",
) -> tuple[int, int, int | None]:
    candidato_id = int(
        con.execute(
            "INSERT INTO candidatos (nome_pseudonimo, status_curacao)"
            " VALUES (?, 'curado')",
            (nome,),
        ).lastrowid
    )
    curriculo_id = int(
        con.execute(
            "INSERT INTO curriculos (candidato_id, nome_arquivo, caminho_cache,"
            " checksum, versao, status_processamento)"
            " VALUES (?, ?, ?, ?, 1, 'processado')",
            (candidato_id, f"{nome}.docx", f"cache/{nome}.docx", f"cur-{nome}"),
        ).lastrowid
    )
    exp_id = int(
        con.execute(
            "INSERT INTO experiencias (curriculo_id, dominio, data_inicio, data_fim,"
            " resumo) VALUES (?, 'energia', '2015', ?, 'Energia')",
            (curriculo_id, str(2015 + anos)),
        ).lastrowid
    )
    con.execute(
        "INSERT INTO evidencias (tipo_fato, fato_id, trecho, nivel, confianca)"
        " VALUES ('experiencia', ?, 'trecho', 3, 100)",
        (exp_id,),
    )
    if scrum:
        comp_id = int(
            con.execute(
                "INSERT INTO competencias (curriculo_id, nome) VALUES (?, 'Scrum')",
                (curriculo_id,),
            ).lastrowid
        )
        con.execute(
            "INSERT INTO evidencias (tipo_fato, fato_id, trecho, nivel, confianca)"
            " VALUES ('competencia', ?, 'trecho', 3, 100)",
            (comp_id,),
        )
    cert_id = None
    if pmp:
        cert_id = int(
            con.execute(
                "INSERT INTO certificacoes (curriculo_id, nome) VALUES (?, 'PMP')",
                (curriculo_id,),
            ).lastrowid
        )
        con.execute(
            "INSERT INTO evidencias (tipo_fato, fato_id, trecho, nivel, confianca)"
            " VALUES ('certificacao', ?, 'trecho', 3, 100)",
            (cert_id,),
        )
        con.execute(
            "INSERT INTO evidencias_documentais (tipo_fato, fato_id, nome_arquivo,"
            " caminho, checksum, status, valido_ate)"
            " VALUES ('certificacao', ?, 'pmp.docx', 'data/provas/pmp.docx', ?, ?,"
            " '2099-12-31')",
            (cert_id, f"proof-{nome}", prova),
        )
    con.commit()
    return candidato_id, curriculo_id, cert_id


def test_mudanca_de_documento_altera_somente_o_candidato_afetado(tmp_path):
    con = conectar(tmp_path / "etapa5.db")
    try:
        criar_schema(con)
        versao_id, disputa_id = _inserir_edital(con)
        a, _, cert_a = _inserir_candidato(
            con, nome="A", anos=6, scrum=True, pmp=True
        )
        b, _, _ = _inserir_candidato(con, nome="B", anos=3, scrum=True, pmp=False)
        resumo = recalcular_disputa_demanda(con, disputa_id, edital_versao_id=versao_id)
        assert resumo["pontuacoes"] == 2

        antes = {
            int(row["candidato_id"]): row
            for row in listar_resultados_demanda(con, disputa_id, versao_id)
        }
        assert antes[a]["nota_tecnica"] == 60.0
        assert antes[a]["status"] == "elegivel"
        assert antes[b]["nota_tecnica"] == 28.0
        assert antes[b]["status"] == "eliminado"
        detalhe_b = antes[b]["detalhe_json"]

        con.execute(
            "UPDATE evidencias_documentais SET status = 'rejeitado'"
            " WHERE tipo_fato = 'certificacao' AND fato_id = ?",
            (cert_a,),
        )
        con.commit()
        recalcular_candidato_demanda(con, a, disputa_id)

        depois = {
            int(row["candidato_id"]): row
            for row in listar_resultados_demanda(con, disputa_id, versao_id)
        }
        assert depois[a]["status"] == "eliminado"
        assert depois[b]["nota_tecnica"] == antes[b]["nota_tecnica"]
        assert depois[b]["status"] == antes[b]["status"]
        assert depois[b]["detalhe_json"] == detalhe_b
    finally:
        con.close()


def test_mudanca_de_fato_e_validade_altera_somente_o_candidato_afetado(tmp_path):
    con = conectar(tmp_path / "etapa5_fatos.db")
    try:
        criar_schema(con)
        versao_id, disputa_id = _inserir_edital(con)
        a, _, cert_a = _inserir_candidato(
            con, nome="A", anos=6, scrum=True, pmp=True
        )
        b, cur_b, _ = _inserir_candidato(
            con, nome="B", anos=3, scrum=True, pmp=False
        )
        recalcular_disputa_demanda(con, disputa_id, edital_versao_id=versao_id)
        antes = {
            int(row["candidato_id"]): row
            for row in listar_resultados_demanda(con, disputa_id, versao_id)
        }
        assert antes[b]["status"] == "eliminado"
        assert antes[b]["nota_tecnica"] == 28.0

        con.execute(
            "UPDATE experiencias SET data_fim = '2021' WHERE curriculo_id = ?",
            (cur_b,),
        )
        con.commit()
        recalcular_candidato_demanda(con, b, disputa_id)
        depois_b = {
            int(row["candidato_id"]): row
            for row in listar_resultados_demanda(con, disputa_id, versao_id)
        }
        assert depois_b[b]["status"] == "pendente"
        assert depois_b[b]["nota_tecnica"] == 40.0
        assert depois_b[a]["detalhe_json"] == antes[a]["detalhe_json"]

        con.execute(
            "INSERT INTO atualidade_fatos (tipo_fato, fato_id, ultima_confirmacao,"
            " valido_ate, estado) VALUES ('certificacao', ?, '2025-01-01',"
            " '2025-06-01', 'vencido')",
            (cert_a,),
        )
        con.commit()
        recalcular_candidato_demanda(con, a, disputa_id)
        depois_a = {
            int(row["candidato_id"]): row
            for row in listar_resultados_demanda(con, disputa_id, versao_id)
        }
        assert depois_a[a]["status"] == "elegivel"
        assert depois_a[a]["atualidade"] < antes[a]["atualidade"]
        assert depois_a[b]["detalhe_json"] == depois_b[b]["detalhe_json"]
    finally:
        con.close()


def test_versao_antiga_continua_consultavel(tmp_path):
    con = conectar(tmp_path / "etapa5_versoes.db")
    try:
        criar_schema(con)
        v1, disputa_id = _inserir_edital(con, pontos_experiencia=30, numero=1)
        _inserir_candidato(con, nome="A", anos=6, scrum=True, pmp=True)
        _inserir_candidato(con, nome="B", anos=3, scrum=True, pmp=False)
        recalcular_disputa_demanda(con, disputa_id, edital_versao_id=v1)
        antigos = listar_resultados_demanda(con, disputa_id, v1)
        assert {row["nota_tecnica"] for row in antigos} == {60.0, 28.0}
        ranking = carregar_ranking_demanda(con, disputa_id, v1)
        assert ranking["nota_tecnica"].max() == 60.0
        assert set(ranking["status_gates"]) == {"elegivel", "eliminado"}

        v2, _ = _inserir_edital(con, pontos_experiencia=40, numero=2)
        con.execute("UPDATE edital_versoes SET status = 'substituida' WHERE id = ?", (v1,))
        con.execute("UPDATE disputas SET edital_versao_id = ? WHERE id = ?", (v2, disputa_id))
        con.commit()
        recalcular_disputa_demanda(con, disputa_id, edital_versao_id=v2)

        novos = listar_resultados_demanda(con, disputa_id, v2)
        assert {row["nota_tecnica"] for row in novos} == {70.0, 34.0}
        ainda_antigos = listar_resultados_demanda(con, disputa_id, v1)
        assert {row["nota_tecnica"] for row in ainda_antigos} == {60.0, 28.0}
        assert ainda_antigos != novos

        con.execute(
            "INSERT INTO atualizacoes_pendentes (disputa_id, candidato_id, tipo,"
            " motivo) VALUES (?, NULL, 'edital_retificado', 'teste')",
            (disputa_id,),
        )
        con.commit()
        resultado = recalcular_pendentes(con, disputa_id)
        assert resultado["recalculadas"] == [disputa_id]
        assert resultado["falhas"] == []
        pendente = con.execute(
            "SELECT status FROM atualizacoes_pendentes WHERE disputa_id = ?",
            (disputa_id,),
        ).fetchone()
        assert pendente["status"] == "resolvida"
    finally:
        con.close()
