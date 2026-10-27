"""Fase 1 (3–4/4) — testes do PII vault e da auditoria."""
import json

import pytest
from cryptography.fernet import InvalidToken

from src.governance.auditoria import por_entidade, registrar, ultimos
from src.governance.vault import (
    CofrePII,
    buscar_pii,
    mascarar,
    redigir,
    revelar,
    salvar,
)
from src.ingestion.db import conectar, criar_schema


@pytest.fixture()
def con():
    con = conectar(":memory:")
    criar_schema(con)
    yield con
    con.close()


@pytest.fixture()
def candidato_id(con):
    return con.execute(
        "INSERT INTO candidatos (nome_pseudonimo) VALUES ('Teste')"
    ).lastrowid


def test_vault_roundtrip_e_sigilo(con, candidato_id):
    cofre = CofrePII(CofrePII.gerar_chave())
    rid = salvar(con, cofre, candidato_id, "email", "joao@exemplo.com")
    assert revelar(con, cofre, rid, ator="teste") == "joao@exemplo.com"
    row = con.execute("SELECT valor_criptografado FROM cofre_dados_pessoais").fetchone()
    assert "joao@exemplo.com" not in row["valor_criptografado"]


def test_vault_chave_errada_falha(con, candidato_id):
    cofre = CofrePII(CofrePII.gerar_chave())
    rid = salvar(con, cofre, candidato_id, "telefone", "(11) 98765-4321")
    with pytest.raises(InvalidToken):
        revelar(con, CofrePII(CofrePII.gerar_chave()), rid, ator="teste")


def test_mascarar_contrato_ui():
    assert mascarar("joao@exemplo.com") == "***"
    assert mascarar("(11) 98765-4321", "telefone") == "***"


def test_buscar_pii_tres_tipos():
    texto = (
        "Contato: joao@exemplo.com; tel (11) 98765-4321; "
        "Rua das Flores 123; CEP 01310-100."
    )
    achados = buscar_pii(texto)
    tipos = {a["tipo"] for a in achados}
    assert {"email", "telefone", "endereco"} <= tipos


def test_redigir_remove_pii_e_preserva_resto():
    texto = "Fale com joao@exemplo.com hoje; mora na Rua das Flores 123."
    saida = redigir(texto)
    assert "joao@exemplo.com" not in saida
    assert "***" in saida
    assert "hoje" in saida


def test_auditoria_registra_e_le(con):
    rid = registrar(
        con,
        acao="editar_preset",
        ator="analista",
        entidade="versoes_preset",
        entidade_id=7,
        detalhes={"campo": "peso_energia", "de": 8, "para": 12},
    )
    linhas = ultimos(con)
    assert linhas[0]["id"] == rid
    assert linhas[0]["acao"] == "editar_preset"
    detalhes = json.loads(linhas[0]["detalhes_json"])
    assert detalhes["para"] == 12
    assert por_entidade(con, "versoes_preset", 7)


def test_vault_audita_criacao_e_leitura(con, candidato_id):
    cofre = CofrePII(CofrePII.gerar_chave())
    salvar(con, cofre, candidato_id, "email", "a@b.com", ator="curador")
    revelar(con, cofre, 1, ator="curador")
    acoes = {row["acao"] for row in ultimos(con)}
    assert {"criar_pii", "ler_pii"} <= acoes
