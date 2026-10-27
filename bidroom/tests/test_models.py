"""Fase 1 (2/4) — testes dos Pydantic models (handoff §4)."""
import pytest
from datetime import datetime, timezone

from src.ingestion.models import (
    Alocacao,
    Candidato,
    Confianca,
    Curriculo,
    Disputa,
    ElegivelBrasil,
    NivelEvidencia,
    Papel,
    Pontuacao,
    Preset,
    RegistroAuditoria,
    StatusAlocacao,
    StatusCuracao,
    StatusDisputa,
    StatusProcessamentoCurriculo,
    StatusVersaoPreset,
    TipoDadosPessoais,
    VersaoPreset,
    CofreDadosPessoais,
)


def test_candidato_model():
    now = datetime.now(timezone.utc)
    c = Candidato(nome_pseudonimo="John Doe", criado_em=now, atualizado_em=now)
    assert c.nome_pseudonimo == "John Doe"
    assert c.elegivel_brasil == ElegivelBrasil.NAO_INFORMADO
    assert c.status_curacao == StatusCuracao.NAO_CURADO


def test_curriculo_model():
    c = Curriculo(
        candidato_id=1,
        nome_arquivo="resume.docx",
        caminho_cache="data/cache_drive/resume.docx",
        checksum="abc123def",
    )
    assert c.candidato_id == 1
    assert c.versao == 1
    assert c.status_processamento == StatusProcessamentoCurriculo.PENDENTE


def test_enums():
    assert ElegivelBrasil.SIM == "sim"
    assert StatusCuracao.ALOCADO == "alocado"
    assert NivelEvidencia.N3 == 3
    assert Confianca.C75 == 75
    assert StatusVersaoPreset.PUBLICADA == "publicada"


def test_datetime_default_utc():
    c = Candidato(nome_pseudonimo="Test")
    assert c.criado_em.tzinfo == timezone.utc


def test_pontuacao_model():
    p = Pontuacao(
        disputa_id=1,
        candidato_id=1,
        papel_id=1,
        versao_preset_id=1,
        curriculo_versao_id=1,
        pontuacao_final=75.5,
        confianca=37.5,
    )
    assert p.pontuacao_final == 75.5
    assert p.confianca == 37.5


def test_alocacao_model():
    a = Alocacao(disputa_id=1, papel_id=1, candidato_id=1)
    assert a.status == StatusAlocacao.ALOCADO
    a_reserva = Alocacao(disputa_id=2, papel_id=2, candidato_id=2, status=StatusAlocacao.RESERVA)
    assert a_reserva.status == StatusAlocacao.RESERVA


def test_cofre_dados_pessoais_model():
    c = CofreDadosPessoais(
        candidato_id=1,
        tipo=TipoDadosPessoais.EMAIL,
        valor_criptografado="encrypted_email",
    )
    assert c.criptografado is True
    assert c.tipo == TipoDadosPessoais.EMAIL


def test_registro_auditoria_model():
    r = RegistroAuditoria(acao="CREATE", entidade="candidato", entidade_id=1, ator="admin")
    assert r.acao == "CREATE"
    assert r.entidade == "candidato"
    assert r.ator == "admin"


def test_preset_e_versao_preset_model():
    p = Preset(nome="TI para o setor de energia", dono="time")
    assert p.status == StatusVersaoPreset.RASCUNHO

    vp = VersaoPreset(preset_id=1, numero=1, status=StatusVersaoPreset.PUBLICADA, autor="Build")
    assert vp.numero == 1
    assert vp.status == StatusVersaoPreset.PUBLICADA


def test_residual_model():
    from src.ingestion.models import Residual

    r = Residual(curriculo_id=1, trecho="Curso de lideranca avancada")
    assert r.id is None and r.termo is None

