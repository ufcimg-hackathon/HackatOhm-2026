"""Fase 3 — testes do motor de score, gates e preset."""
from __future__ import annotations

import json

import pytest

from scripts.build_db import GATES_ENERGIA_V1, PESOS_ENERGIA_V1, construir
from src.ingestion.db import conectar
from src.scoring.catalogo import CATALOGO
from src.scoring.contexto import (
    CertificacaoFato,
    CompetenciaFato,
    ContextoCandidato,
    ExperienciaFato,
    FormacaoFato,
    IdiomaFato,
    anos_intervalos,
)
from src.scoring.engine import ResultadoScore, pontuar
from src.scoring.gates import avaliar_gates, status_geral
from src.scoring.preset import carregar_versao


def _contexto(
    sigla: str,
    conf_core: int = 50,
    anos: float = 8.0,
    portugues: str | None = "avançado",
) -> ContextoCandidato:
    perfil = CATALOGO[sigla]
    ctx = ContextoCandidato(candidato_id=1, curriculo_id=1)
    ctx.competencias = [CompetenciaFato(nome, conf_core) for nome in perfil.core]
    ctx.experiencias = [
        ExperienciaFato("energia", 50, "2015", "2026")
    ]
    ctx.anos_experiencia = anos
    ctx.certificacoes = [CertificacaoFato("PMP", 0)]
    ctx.formacoes = [FormacaoFato("mba", "MBA em Gestão", 25)]
    ctx.idiomas = [
        IdiomaFato("Português", portugues, 25),
        IdiomaFato("Inglês", "avançado", 25),
    ]
    return ctx


def test_pesos_do_preset_somam_100_por_papel():
    for sigla, categorias in PESOS_ENERGIA_V1["categorias"].items():
        assert abs(sum(categorias.values()) - 100) < 0.01, sigla


def test_preset_v1_publicado_no_banco(tmp_path):
    construir(tmp_path / "d.db")
    con = conectar(tmp_path / "d.db")
    try:
        config = carregar_versao(con)
        assert config.status == "publicada"
        assert config.numero == 1
        assert "categorias" in config.pesos
        assert "G1" in config.gates
    finally:
        con.close()


def test_formula_do_score_final():
    ctx = _contexto("pm_scrum")
    resultado = pontuar(ctx, CATALOGO["pm_scrum"], PESOS_ENERGIA_V1, GATES_ENERGIA_V1)
    esperado = resultado.pontuacao_ponderada * (
        0.6 + 0.4 * resultado.confianca / 100
    )
    assert resultado.pontuacao_final == pytest.approx(esperado, abs=0.02)
    assert resultado.detalhe["piso_confianca"] == 0.6


def test_gate_g1_elimina_sem_evidencia_n2():
    ctx = _contexto("java", conf_core=25)
    gates = avaliar_gates(ctx, CATALOGO["java"], GATES_ENERGIA_V1)
    g1 = next(g for g in gates if g.gate == "G1")
    assert g1.status == "elimina"
    assert "N2+" in g1.motivo


def test_gate_g1_passa_com_tres_core_e_dois_n2():
    ctx = _contexto("java", conf_core=25)
    ctx.competencias = [
        CompetenciaFato(nome, 50 if i < 2 else 25)
        for i, nome in enumerate(CATALOGO["java"].core)
    ]
    gates = avaliar_gates(ctx, CATALOGO["java"], GATES_ENERGIA_V1)
    g1 = next(g for g in gates if g.gate == "G1")
    assert g1.status == "passou"
    assert "2 em N2+" in g1.motivo


def test_gate_g2_elimina_abaixo_dos_anos_minimos():
    ctx = _contexto("pm_scrum", anos=3.0)
    gates = avaliar_gates(ctx, CATALOGO["pm_scrum"], GATES_ENERGIA_V1)
    g2 = next(g for g in gates if g.gate == "G2")
    assert g2.status == "elimina"
    assert status_geral(gates) == "eliminado"


def test_gate_g3_revisar_sem_portugues():
    ctx = _contexto("pm_scrum")
    ctx.idiomas = [IdiomaFato("Inglês", "avançado", 25)]
    gates = avaliar_gates(ctx, CATALOGO["pm_scrum"], GATES_ENERGIA_V1)
    g3 = next(g for g in gates if g.gate == "G3")
    assert g3.status == "revisar"
    assert status_geral(gates) == "revisar"


def test_gate_g4_elimina_apenas_nao_explicito():
    ctx = _contexto("pm_scrum")
    ctx.elegivel_brasil = "nao"
    gates = avaliar_gates(ctx, CATALOGO["pm_scrum"], GATES_ENERGIA_V1)
    g4 = next(g for g in gates if g.gate == "G4")
    assert g4.status == "elimina"

    ctx.elegivel_brasil = "nao_informado"
    gates = avaliar_gates(ctx, CATALOGO["pm_scrum"], GATES_ENERGIA_V1)
    g4 = next(g for g in gates if g.gate == "G4")
    assert g4.status == "passou"


def test_formula_do_score_com_piso():
    ctx = _contexto("pm_scrum")
    resultado = pontuar(ctx, CATALOGO["pm_scrum"], PESOS_ENERGIA_V1, GATES_ENERGIA_V1)
    assert resultado.detalhe["piso_confianca"] == 0.6


def test_ancoras_do_pm_somam_28_e_nao_somam_por_cima():
    ctx = _contexto("pm_scrum")
    resultado = pontuar(ctx, CATALOGO["pm_scrum"], PESOS_ENERGIA_V1, GATES_ENERGIA_V1)
    ancoras = resultado.detalhe["ancoras_pm"]
    assert sum(a["peso"] for a in ancoras) == 28
    assert resultado.detalhe["ancoras_nota"].startswith("âncoras são subconjunto")


def test_papeis_nao_pm_nao_tem_ancoras():
    ctx = _contexto("java")
    resultado = pontuar(ctx, CATALOGO["java"], PESOS_ENERGIA_V1, GATES_ENERGIA_V1)
    assert resultado.detalhe["ancoras_pm"] == []


def test_anos_intervalos_funde_periodos_sobrepostos():
    experiencias = [
        ExperienciaFato(None, 25, "2015", "2020"),
        ExperienciaFato(None, 25, "2018", "2022"),
    ]
    assert anos_intervalos(experiencias) == 7.0


@pytest.fixture()
def test_carregar_contexto_le_fatos_e_evidencias(tmp_path):
    destino = tmp_path / "d.db"
    construir(destino)
    con = conectar(destino)
    try:
        candidato_id = con.execute(
            "INSERT INTO candidatos (nome_pseudonimo, status_curacao)"
            " VALUES ('Candidato Teste', 'curado')"
        ).lastrowid
        curriculo_id = con.execute(
            "INSERT INTO curriculos (candidato_id, nome_arquivo, caminho_cache,"
            " checksum, n_paginas, versao) VALUES (?, 'a.docx', 'a.docx', 'x',"
            " 'processado')",
            (candidato_id,),
        ).lastrowid
        con.execute(
            "INSERT INTO competencias (curriculo_id, nome, anos, familia)"
            " VALUES (?, 'Java')",
            (curriculo_id,),
        )
        con.execute(
            "INSERT INTO evidencias (tipo_fato, fato_id, trecho, nivel, confianca)"
            " VALUES ('competencia', ?, 'Java em projeto', 2, 50)",
            (competencia_id,),
        )
        experiencia_id = con.execute(
            "INSERT INTO experiencias (curriculo_id, empresa, papel, dominio,"
            " data_inicio, data_fim, tecnologias, resumo)"
            " VALUES (?, 'EDP', 'PM', 'energia', '2015', '2026', '[]', 'Atuação em energia.')",
            (curriculo_id,),
        ).lastrowid
        con.execute(
            "INSERT INTO evidencias (tipo_fato, fato_id, trecho, pagina, nivel, confianca)"
            " VALUES ('experiencia', ?, 'Projeto Client: EDP com energia.', 2, 3, 75)",
            (experiencia_id,),
        )
        con.execute(
            "INSERT INTO competencias (curriculo_id, nome, anos, familia)"
            " VALUES (?, 'Java', 8, 'linguagem')",
            (curriculo_id,),
        )
        con.execute(
            "INSERT INTO evidencias (tipo_fato, fato_id, trecho, pagina, nivel, confianca)"
            " VALUES ('competencia', ?, 'Java em projeto.', 1, 2, 50)",
            (comp_id,),
        )
        con.commit()
        yield con, destino
    finally:
        con.close()


def test_pesos_vem_do_preset_e_mudam_o_score():
    ctx = _contexto("pm_scrum")
    pesos_alt = json.loads(json.dumps(PESOS_ENERGIA_V1))
    pesos_alt["categorias"]["pm_scrum"]["aderencia"] = 40
    pesos_alt["categorias"]["pm_scrum"]["dominio_energia"] = 10

    base = pontuar(ctx, CATALOGO["pm_scrum"], PESOS_ENERGIA_V1, GATES_ENERGIA_V1)
    alt = pontuar(ctx, CATALOGO["pm_scrum"], pesos_alt, GATES_ENERGIA_V1)
    assert base.pontuacao_ponderada != alt.pontuacao_ponderada


def test_ancoras_do_pm_nao_somam_por_cima():
    ctx = _contexto("pm_scrum")
    resultado = pontuar(ctx, CATALOGO["pm_scrum"], PESOS_ENERGIA_V1, GATES_ENERGIA_V1)
    ancoras = resultado.detalhe["ancoras_pm"]
    assert sum(a["peso"] for a in ancoras) == 28
    assert resultado.detalhe["ancoras_nota"].startswith("âncoras são subconjunto")


def test_papeis_nao_pm_nao_tem_ancoras():
    ctx = _contexto("java")
    resultado = pontuar(ctx, CATALOGO["java"], PESOS_ENERGIA_V1, GATES_ENERGIA_V1)
    assert resultado.detalhe["ancoras_pm"] == []


def test_anos_intervalos_funde_periodos_sobrepostos():
    experiencias = [
        ExperienciaFato(None, 25, "2015", "2020"),
        ExperienciaFato(None, 25, "2018", "2022"),
    ]
    assert anos_intervalos(experiencias) == 7.0


def test_gate_g1_elimina_sem_evidencia_n2():
    ctx = _contexto("java", conf_core=25)
    gates = avaliar_gates(ctx, CATALOGO["java"], GATES_ENERGIA_V1)
    g1 = next(g for g in gates if g.gate == "G1")
    assert g1.status == "elimina"
    assert "N2+" in g1.motivo


def test_gate_g1_passa_com_tres_core_e_dois_n2():
    perfil = CATALOGO["java"]
    ctx = _contexto("java", conf_core=25)
    ctx.competencias = [
        CompetenciaFato(nome, 50 if i < 2 else 25)
        for i, nome in enumerate(perfil.core)
    ]
    gates = avaliar_gates(ctx, perfil, GATES_ENERGIA_V1)
    g1 = next(g for g in gates if g.gate == "G1")
    assert g1.status == "passou"
    assert "2 em N2+" in g1.motivo


def test_gate_g2_elimina_abaixo_dos_anos_minimos():
    ctx = _contexto("pm_scrum", anos=3.0)
    gates = avaliar_gates(ctx, CATALOGO["pm_scrum"], GATES_ENERGIA_V1)
    g2 = next(g for g in gates if g.gate == "G2")
    assert g2.status == "elimina"
    assert status_geral(gates) == "eliminado"


def test_gate_g3_revisar_sem_portugues():
    ctx = _contexto("pm_scrum")
    ctx.idiomas = [IdiomaFato("Inglês", "avançado", 25)]
    gates = avaliar_gates(ctx, CATALOGO["pm_scrum"], GATES_ENERGIA_V1)
    g3 = next(g for g in gates if g.gate == "G3")
    assert g3.status == "revisar"
    assert status_geral(gates) == "revisar"


def test_gate_g4_elimina_apenas_nao_explicito():
    ctx = _contexto("pm_scrum")
    ctx.elegivel_brasil = "nao"
    gates = avaliar_gates(ctx, CATALOGO["pm_scrum"], GATES_ENERGIA_V1)
    g4 = next(g for g in gates if g.gate == "G4")
    assert g4.status == "elimina"

    ctx.elegivel_brasil = "nao_informado"
    gates = avaliar_gates(ctx, CATALOGO["pm_scrum"], GATES_ENERGIA_V1)
    g4 = next(g for g in gates if g.gate == "G4")
    assert g4.status == "passou"


def test_g5_ativo_revisar_e_desligado_sem_status():
    from src.scoring.gates import avaliar_gates, status_geral
    from scripts.build_db import GATES_ENERGIA_V1, PESOS_ENERGIA_V1
    from src.scoring.catalogo import CATALOGO
    from src.scoring.contexto import ContextoCandidato, ExperienciaFato, IdiomaFato, FormacaoFato, CompetenciaFato, CertificacaoFato

    ctx = ContextoCandidato(
        candidato_id=1, curriculo_id=1,
        competencias=[CompetenciaFato("Gestão de projetos", 80), CompetenciaFato("Scrum", 80), CompetenciaFato("Kanban", 80)],
        experiencias=[ExperienciaFato("energia", 50, "2015", "2026")],
        idiomas=[IdiomaFato("Português", "avançado", 25)],
        formacoes=[FormacaoFato("mba", "MBA em Gestão", 25)],
        certificacoes=[CertificacaoFato("PMP", 0)],
        anos_experiencia=6.0,
    )
    # G5 ativo => revisar
    gates = avaliar_gates(ctx, CATALOGO["pm_scrum"], {"G5": {"ativo": True}})
    g5 = next(g for g in gates if g.gate == "G5")
    assert g5.status == "revisar"
    assert status_geral(gates) == "revisar"

    # G5 desligado => status geral volta a elegivel (se nada mais bloquear)
    gates2 = avaliar_gates(ctx, CATALOGO["pm_scrum"], {"G5": {"ativo": False}})
    assert status_geral(gates2) == "elegivel"


def test_g6_core_n0_elimina_e_n2_minimo():
    from src.scoring.gates import avaliar_gates
    from scripts.build_db import GATES_ENERGIA_V1
    from src.scoring.catalogo import CATALOGO
    from src.scoring.contexto import ContextoCandidato, CompetenciaFato, ExperienciaFato, IdiomaFato, FormacaoFato, CertificacaoFato

    ctx = ContextoCandidato(
        candidato_id=1, curriculo_id=1,
        competencias=[CompetenciaFato("Java", 0), CompetenciaFato("SQL", 80)],
        experiencias=[ExperienciaFato("energia", 50, "2015", "2026")],
        idiomas=[IdiomaFato("Português", "avançado", 25)],
        formacoes=[FormacaoFato("mba", "MBA em Gestão", 25)],
        certificacoes=[CertificacaoFato("PMP", 0)],
        anos_experiencia=8.0,
    )
    # G6 ativo, core em N0 => elimina
    gates = avaliar_gates(ctx, CATALOGO["java"], GATES_ENERGIA_V1)
    g6 = next(g for g in gates if g.gate == "G6")
    assert g6.status == "elimina"
    assert "core em N0" in g6.motivo

    # G6 desligado => ausente
    gates_off = avaliar_gates(ctx, CATALOGO["java"], {"G6": {"ativo": False}})
    assert all(g.gate != "G6" for g in gates_off)


def test_g2_por_preset():
    from src.scoring.gates import avaliar_gates
    from scripts.build_db import GATES_ENERGIA_V1, PESOS_ENERGIA_V1
    from src.scoring.catalogo import CATALOGO
    from src.scoring.contexto import ContextoCandidato, ExperienciaFato, IdiomaFato, FormacaoFato, CompetenciaFato, CertificacaoFato

    # PM com 4 anos e mínimo 5 => elimina
    ctx_pm = ContextoCandidato(
        candidato_id=1, curriculo_id=1,
        competencias=[CompetenciaFato("Gestão de projetos", 80), CompetenciaFato("Scrum", 80), CompetenciaFato("Kanban", 80)],
        experiencias=[ExperienciaFato("energia", 50, "2015", "2026")],
        idiomas=[IdiomaFato("Português", "avançado", 25)],
        formacoes=[FormacaoFato("mba", "MBA em Gestão", 25)],
        certificacoes=[CertificacaoFato("PMP", 0)],
        anos_experiencia=4.0,
    )
    gates_pm = avaliar_gates(ctx_pm, CATALOGO["pm_scrum"], GATES_ENERGIA_V1)
    g2_pm = next(g for g in gates_pm if g.gate == "G2")
    assert g2_pm.status == "elimina"

    # Java com 6 anos e mínimo 5 => passa
    ctx_java = ContextoCandidato(
        candidato_id=1, curriculo_id=1,
        competencias=[CompetenciaFato("Gestão de projetos", 80), CompetenciaFato("Scrum", 80), CompetenciaFato("Kanban", 80)],
        experiencias=[ExperienciaFato("energia", 50, "2015", "2026")],
        idiomas=[IdiomaFato("Português", "avançado", 25)],
        formacoes=[FormacaoFato("mba", "MBA em Gestão", 25)],
        certificacoes=[CertificacaoFato("PMP", 0)],
        anos_experiencia=6.0,
    )
    gates_java = avaliar_gates(ctx_java, CATALOGO["java"], GATES_ENERGIA_V1)
    g2_java = next(g for g in gates_java if g.gate == "G2")
    assert g2_java.status == "passou"


def test_consistencia_papeis_catalogo_gates():
    """PAPEIS do build_db devem cobrir todos do CATALOGO e GATES_ENERGIA_V1."""
    from scripts.build_db import PAPEIS, GATES_ENERGIA_V1
    from src.scoring.catalogo import CATALOGO
    papel_set = {p[1] for p in PAPEIS}
    cat_set = set(CATALOGO.keys())
    assert papel_set == cat_set, f"diferença: {papel_set - cat_set} / {cat_set - papel_set}"
    # GATES_ENERGIA_V1 deve ter G1..G6
    assert set(GATES_ENERGIA_V1.keys()) == {"G1", "G2", "G3", "G4", "G5", "G6"}


def test_anos_intervalos_funde_periodos_sobrepostos():
    experiencias = [
        ExperienciaFato(None, 25, "2015", "2020"),
        ExperienciaFato(None, 25, "2018", "2022"),
    ]
    assert anos_intervalos(experiencias) == 7.0