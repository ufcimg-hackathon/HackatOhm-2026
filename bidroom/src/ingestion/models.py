"""Fase 1 (2/4) — Pydantic models para o domínio (handoff §4).

Convenções:
- Classes refletem as tabelas do SQLite (`src/ingestion/db.py`).
- Enums Python espelham os CHECKs do DDL.
- Tipos de `datetime` sempre em UTC com `timezone.utc`.
- IDs são opcionais na criação (auto-incremento no banco).
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field, conint, conlist, constr


# --- Enums (espelham o DDL) ---

class ElegivelBrasil(str, Enum):
    SIM = "sim"
    NAO = "nao"
    NAO_INFORMADO = "nao_informado"


class StatusCuracao(str, Enum):
    NAO_CURADO = "nao_curado"
    CURADO = "curado"
    ALOCADO = "alocado"


class StatusProcessamentoCurriculo(str, Enum):
    PENDENTE = "pendente"
    PROCESSADO = "processado"
    ERRO = "erro"


class NivelEvidencia(int, Enum):
    N0 = 0
    N1 = 1
    N2 = 2
    N3 = 3
    N4 = 4


class Confianca(int, Enum):
    C0 = 0
    C25 = 25
    C50 = 50
    C75 = 75
    C100 = 100


class StatusVersaoPreset(str, Enum):
    RASCUNHO = "rascunho"
    PUBLICADA = "publicada"
    DEPRECADA = "deprecada"


class StatusDisputa(str, Enum):
    ABERTA = "aberta"
    FECHADA = "fechada"


class StatusAlocacao(str, Enum):
    ALOCADO = "alocado"
    RESERVA = "reserva"


class TipoDadosPessoais(str, Enum):
    EMAIL = "email"
    TELEFONE = "telefone"
    ENDERECO = "endereco"


# --- Pydantic Models (para cada tabela do DDL) ---

class Candidato(BaseModel):
    id: Optional[int] = None
    nome_pseudonimo: constr(min_length=1)
    origem: Optional[str] = None
    idiomas: Optional[str] = None  # JSON string ou lista de strings
    elegivel_brasil: ElegivelBrasil = ElegivelBrasil.NAO_INFORMADO
    status_curacao: StatusCuracao = StatusCuracao.NAO_CURADO
    dono: Optional[str] = None
    criado_em: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    atualizado_em: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Curriculo(BaseModel):
    id: Optional[int] = None
    candidato_id: int
    nome_arquivo: constr(min_length=1)
    caminho_cache: constr(min_length=1)
    checksum: constr(min_length=1)
    data_ingestao: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    n_paginas: Optional[int] = None
    versao: int = 1
    status_processamento: StatusProcessamentoCurriculo = StatusProcessamentoCurriculo.PENDENTE
    raw_texto: Optional[str] = None


class Evidencia(BaseModel):
    id: Optional[int] = None
    tipo_fato: constr(min_length=1)
    fato_id: int
    trecho: constr(min_length=1)
    pagina: Optional[int] = None
    nivel: NivelEvidencia
    confianca: Confianca


class Experiencia(BaseModel):
    id: Optional[int] = None
    curriculo_id: int
    empresa: Optional[str] = None
    papel: Optional[str] = None
    dominio: Optional[str] = None
    data_inicio: Optional[str] = None  # Formato YYYY-MM-DD
    data_fim: Optional[str] = None  # Formato YYYY-MM-DD
    tecnologias: Optional[str] = None  # JSON string ou lista de strings
    resumo: Optional[str] = None


class Competencia(BaseModel):
    id: Optional[int] = None
    curriculo_id: int
    nome: constr(min_length=1)
    anos: Optional[float] = None
    ultimo_uso: Optional[str] = None  # Formato YYYY-MM-DD
    familia: Optional[str] = None


class Certificacao(BaseModel):
    id: Optional[int] = None
    curriculo_id: int
    nome: constr(min_length=1)
    emissor: Optional[str] = None
    ano: Optional[int] = None


class Formacao(BaseModel):
    id: Optional[int] = None
    curriculo_id: int
    grau: Optional[str] = None
    curso: Optional[str] = None
    instituicao: Optional[str] = None
    ano_inicio: Optional[int] = None
    ano_fim: Optional[int] = None


class Idioma(BaseModel):
    id: Optional[int] = None
    curriculo_id: int
    idioma: constr(min_length=1)
    nivel: Optional[str] = None


class Residual(BaseModel):
    id: Optional[int] = None
    curriculo_id: int
    termo: Optional[str] = None
    trecho: constr(min_length=1)


class Papel(BaseModel):
    id: Optional[int] = None
    nome: constr(min_length=1)
    sigla: constr(min_length=1)
    requisitos_json: Optional[Any] = None  # JSON string


class Preset(BaseModel):
    id: Optional[int] = None
    nome: constr(min_length=1)
    descricao: Optional[str] = None
    dominio: Optional[str] = None
    status: StatusVersaoPreset = StatusVersaoPreset.RASCUNHO
    dono: Optional[str] = None


class VersaoPreset(BaseModel):
    id: Optional[int] = None
    preset_id: int
    numero: int
    pesos_json: Optional[Any] = None  # JSON string
    gates_json: Optional[Any] = None  # JSON string
    status: StatusVersaoPreset = StatusVersaoPreset.RASCUNHO
    autor: Optional[str] = None
    criado_em: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Disputa(BaseModel):
    id: Optional[int] = None
    nome_ficticio: constr(min_length=1)
    cliente_ficticio: Optional[str] = None
    preset_versao_id: Optional[int] = None
    status: StatusDisputa = StatusDisputa.ABERTA


class Pontuacao(BaseModel):
    id: Optional[int] = None
    disputa_id: int
    candidato_id: int
    papel_id: int
    versao_preset_id: int
    curriculo_versao_id: int
    pontuacao_ponderada: Optional[float] = None
    confianca: Optional[float] = Field(default=None, ge=0, le=100)
    pontuacao_final: Optional[float] = None
    detalhe_json: Optional[Any] = None  # JSON string
    criado_em: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Alocacao(BaseModel):
    id: Optional[int] = None
    disputa_id: int
    papel_id: int
    candidato_id: int
    status: StatusAlocacao = StatusAlocacao.ALOCADO


class CofreDadosPessoais(BaseModel):
    id: Optional[int] = None
    candidato_id: int
    tipo: TipoDadosPessoais
    valor_criptografado: constr(min_length=1)
    criptografado: bool = True
    criado_em: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RegistroAuditoria(BaseModel):
    id: Optional[int] = None
    acao: constr(min_length=1)
    ator: Optional[str] = None
    entidade: constr(min_length=1)
    entidade_id: Optional[int] = None
    detalhes_json: Optional[Any] = None  # JSON string
    data_hora: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
