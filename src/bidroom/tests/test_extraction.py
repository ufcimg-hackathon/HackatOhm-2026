"""Fase 2 — testes do parser .docx e das regras de extração v1."""
from pathlib import Path

import pytest

from src.extraction.parser_docx import (
    CHARS_POR_PAGINA,
    checksum_arquivo,
    pagina_estimada,
    parse_docx,
)
from src.extraction.regras import extrair

TEXTO_DEMO = """João da Silva
joao@exemplo.com | (11) 98765-4321
Rua das Flores 123, CEP 01310-100

Resumo: Analista com 10 anos de experiência em Java, Spring Boot, SQL e Power BI.

Experiência
Analista de Sistemas - EnergiaPaulista (2015 - 2023)
Projetos de smart grid e transmissão de energia. APIs REST com Java e Spring.
Certificação PMP em 2020. Curso de liderança avançada.

Formação
Bacharelado em Ciência da Computação - Universidade de São Paulo (2010 - 2014)
Idiomas: Português fluente, Inglês avançado
"""


def test_extrai_competencias_com_evidencia():
    ext = extrair(TEXTO_DEMO, n_paginas=1)
    nomes = {f.dados["nome"] for f in ext.competencias}
    assert {"Java", "Spring", "SQL", "Power BI", "APIs REST"} <= nomes
    for fato in ext.competencias:
        assert fato.trecho
        assert fato.pagina >= 1
        assert fato.nivel == 1 and fato.confianca == 25


def test_extrai_experiencia_com_dominio_energia():
    ext = extrair(TEXTO_DEMO, n_paginas=1)
    assert ext.experiencias, "deveria achar ao menos uma experiência"
    exp = ext.experiencias[0]
    assert exp.dados["dominio"] == "energia"
    assert "Java" in exp.dados["tecnologias"]
    assert "2015" in exp.dados["periodo"]
    assert exp.dados["papel"] == "Analista de Sistemas"
    assert exp.dados["empresa"] == "EnergiaPaulista"
    # linha de formação (2010 - 2014) não pode virar experiência
    assert len(ext.experiencias) == 1
    assert all("Universidade" not in e.trecho for e in ext.experiencias)


def test_experiencia_com_mes_por_extenso_e_titulo_anterior():
    texto = (
        "Analista Sênior\n"
        "Abril 2019 - Presente\n"
        "Projetos de smart grid com Java e SQL."
    )
    ext = extrair(texto, n_paginas=1)
    assert len(ext.experiencias) == 1
    exp = ext.experiencias[0]
    assert "Abril 2019" in exp.dados["periodo"]
    assert exp.dados["papel"] == "Analista Sênior"
    assert exp.dados["dominio"] == "energia"


def test_experiencia_empresa_no_resto_da_linha():
    texto = "Out'05 - Mai'10\tIBM India Pvt. Ltd.\nJava e SQL."
    ext = extrair(texto, n_paginas=1)
    assert len(ext.experiencias) == 1
    assert ext.experiencias[0].dados["empresa"].startswith("IBM India")


def test_limpa_rotulos_de_projeto_e_nao_duplica():
    texto = (
        "Sr. Java Developer\n"
        "Client: Cigna Healthcare\tLocation: Windsor, CT\tNov 13 - Dec 15\n"
        "Java e Spring.\n"
        "Client: Cigna Healthcare\tLocation: Windsor, CT\tNov 13 - Dec 15\n"
    )
    ext = extrair(texto, n_paginas=1)
    assert len(ext.experiencias) == 1
    exp = ext.experiencias[0]
    assert exp.dados["papel"] == "Sr. Java Developer"
    assert exp.dados["empresa"] == "Cigna Healthcare"


def test_promove_competencia_para_n2_com_projeto_cliente():
    texto = (
        "Sr. Java Developer\n"
        "Client: Cigna Healthcare\n"
        "Jan 2019 - Dez 2020\n"
        "Desenvolvimento em Java, Spring e SQL."
    )
    ext = extrair(texto, n_paginas=1)
    assert ext.experiencias[0].nivel == 2
    java = next(c for c in ext.competencias if c.dados["nome"] == "Java")
    assert java.nivel == 2 and java.confianca == 50


def test_alias_project_manager_promove_gestao_de_projetos_para_n2():
    texto = (
        "Project Manager\n"
        "Client: EDP Energia\n"
        "Jan 2020 - Dez 2022\n"
        "Gestão de cronogramas, riscos e stakeholders."
    )
    ext = extrair(texto, n_paginas=1)
    assert ext.experiencias[0].nivel == 2
    gestao = next(
        c for c in ext.competencias if c.dados["nome"] == "Gestão de projetos"
    )
    assert gestao.nivel == 2 and gestao.confianca == 50


def test_extrai_certificacao_nasce_n0():
    ext = extrair(TEXTO_DEMO, n_paginas=1)
    assert [f.dados["nome"] for f in ext.certificacoes] == ["PMP"]
    fato = ext.certificacoes[0]
    assert fato.nivel == 0 and fato.confianca == 0


def test_extrai_formacao():
    ext = extrair(TEXTO_DEMO, n_paginas=1)
    assert ext.formacoes
    assert "bacharelado" in ext.formacoes[0].dados["grau"].lower()


def test_extrai_idiomas_com_nivel():
    ext = extrair(TEXTO_DEMO, n_paginas=1)
    idiomas = {f.dados["idioma"]: f.dados["nivel"] for f in ext.idiomas}
    assert idiomas.get("Português") == "fluente"
    assert idiomas.get("Inglês") == "avançado"


def test_residual_captura_curso_nao_mapeado():
    ext = extrair(TEXTO_DEMO, n_paginas=1)
    assert any("Curso de liderança" in r for r in ext.residual)


def test_pagina_estimada():
    texto = "x" * (CHARS_POR_PAGINA * 2)
    assert pagina_estimada(texto, 0, 2) == 1
    assert pagina_estimada(texto, CHARS_POR_PAGINA + 10, 2) == 2
    assert pagina_estimada(texto, len(texto) - 1, 2) == 2


def test_parse_docx_real_do_cache():
    cache = Path("data/cache_drive")
    arquivos = sorted(cache.glob("*.docx")) if cache.exists() else []
    if not arquivos:
        pytest.skip("cache de currículos indisponível neste ambiente")
    # procura o primeiro arquivo com texto extraível (alguns podem ser digitalizados)
    for arquivo in arquivos[:10]:
        doc = parse_docx(arquivo)
        if len(doc.texto) >= 200:
            assert doc.n_paginas >= 1
            assert checksum_arquivo(arquivo)
            break
    else:
        pytest.skip("nenhum docx com texto suficientes nos 10 primeiros")
