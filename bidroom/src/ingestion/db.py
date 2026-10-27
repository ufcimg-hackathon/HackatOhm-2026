"""Fase 1 — schema SQLite completo (handoff §4).

Convenções vigentes:
- domínio em português, sem acento (`candidatos`, `evidencias`, `pontuacoes`);
- versionamento imutável: editar publicado gera nova versão, nunca UPDATE;
- toda pontuação amarrada a `versao_preset_id` e `curriculo_versao_id`
  (reprodutibilidade: mesma entrada -> mesma nota);
- `evidencias` é polimórfica (`tipo_fato` + `fato_id`) e intencionalmente não
  tem FK — aponta para experiencias/competencias/certificacoes/etc.
- PII não tem atributo protegido em nenhuma outra tabela (minimização); tudo
  que é pessoal vive no `cofre_dados_pessoais`.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DDL = """
-- ---------- candidatos e curriculos ----------

CREATE TABLE candidatos (
  id INTEGER PRIMARY KEY,
  nome_pseudonimo TEXT NOT NULL,
  origem TEXT,
  idiomas TEXT,
  elegivel_brasil TEXT NOT NULL DEFAULT 'nao_informado'
      CHECK (elegivel_brasil IN ('sim','nao','nao_informado')),
  status_curacao TEXT NOT NULL DEFAULT 'nao_curado'
      CHECK (status_curacao IN ('nao_curado','curado','alocado')),
  dono TEXT,
  criado_em TEXT NOT NULL DEFAULT (datetime('now')),
  atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE curriculos (
  id INTEGER PRIMARY KEY,
  candidato_id INTEGER NOT NULL REFERENCES candidatos(id),
  nome_arquivo TEXT NOT NULL,
  caminho_cache TEXT NOT NULL,
  checksum TEXT NOT NULL,
  data_ingestao TEXT NOT NULL DEFAULT (datetime('now')),
  n_paginas INTEGER,
  versao INTEGER NOT NULL DEFAULT 1,
  status_processamento TEXT NOT NULL DEFAULT 'pendente'
      CHECK (status_processamento IN ('pendente','processado','erro')),
  raw_texto TEXT,
  UNIQUE (candidato_id, checksum)
);

-- ---------- fatos extraidos ----------

CREATE TABLE experiencias (
  id INTEGER PRIMARY KEY,
  curriculo_id INTEGER NOT NULL REFERENCES curriculos(id),
  empresa TEXT,
  papel TEXT,
  dominio TEXT,
  data_inicio TEXT,
  data_fim TEXT,
  tecnologias TEXT,
  resumo TEXT
);

CREATE TABLE competencias (
  id INTEGER PRIMARY KEY,
  curriculo_id INTEGER NOT NULL REFERENCES curriculos(id),
  nome TEXT NOT NULL,
  anos REAL,
  ultimo_uso TEXT,
  familia TEXT
);

-- certificacoes nascem N0: nenhuma da base tem anexo (handoff §4)
CREATE TABLE certificacoes (
  id INTEGER PRIMARY KEY,
  curriculo_id INTEGER NOT NULL REFERENCES curriculos(id),
  nome TEXT NOT NULL,
  emissor TEXT,
  ano INTEGER
);

CREATE TABLE formacoes (
  id INTEGER PRIMARY KEY,
  curriculo_id INTEGER NOT NULL REFERENCES curriculos(id),
  grau TEXT,
  curso TEXT,
  instituicao TEXT,
  ano_inicio INTEGER,
  ano_fim INTEGER
);

CREATE TABLE idiomas (
  id INTEGER PRIMARY KEY,
  curriculo_id INTEGER NOT NULL REFERENCES curriculos(id),
  idioma TEXT NOT NULL,
  nivel TEXT
);

-- evidencia polimorfica: (tipo_fato, fato_id) aponta para o fato
CREATE TABLE evidencias (
  id INTEGER PRIMARY KEY,
  tipo_fato TEXT NOT NULL,
  fato_id INTEGER NOT NULL,
  trecho TEXT NOT NULL,
  pagina INTEGER,
  nivel INTEGER NOT NULL CHECK (nivel IN (0,1,2,3,4)),
  confianca INTEGER NOT NULL CHECK (confianca IN (0,25,50,75,100))
);

-- mencoes nao mapeadas pela taxonomia (nao pontuam; evolucao futura — §5.4)
CREATE TABLE residual (
  id INTEGER PRIMARY KEY,
  curriculo_id INTEGER NOT NULL REFERENCES curriculos(id),
  termo TEXT,
  trecho TEXT NOT NULL
);

-- ---------- configuracao de edital ----------

CREATE TABLE papeis (
  id INTEGER PRIMARY KEY,
  nome TEXT NOT NULL,
  sigla TEXT NOT NULL UNIQUE,
  requisitos_json TEXT
);

CREATE TABLE presets (
  id INTEGER PRIMARY KEY,
  nome TEXT NOT NULL UNIQUE,
  descricao TEXT,
  dominio TEXT,
  status TEXT NOT NULL DEFAULT 'rascunho',
  dono TEXT
);

CREATE TABLE versoes_preset (
  id INTEGER PRIMARY KEY,
  preset_id INTEGER NOT NULL REFERENCES presets(id),
  numero INTEGER NOT NULL,
  pesos_json TEXT,
  gates_json TEXT,
  status TEXT NOT NULL DEFAULT 'rascunho'
      CHECK (status IN ('rascunho','publicada','deprecada')),
  autor TEXT,
  criado_em TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (preset_id, numero)
);

CREATE TABLE disputas (
  id INTEGER PRIMARY KEY,
  nome_ficticio TEXT NOT NULL,
  cliente_ficticio TEXT,
  preset_versao_id INTEGER REFERENCES versoes_preset(id),
  status TEXT NOT NULL DEFAULT 'aberta'
);

-- ---------- decisao ----------

CREATE TABLE pontuacoes (
  id INTEGER PRIMARY KEY,
  disputa_id INTEGER NOT NULL REFERENCES disputas(id),
  candidato_id INTEGER NOT NULL REFERENCES candidatos(id),
  papel_id INTEGER NOT NULL REFERENCES papeis(id),
  versao_preset_id INTEGER NOT NULL REFERENCES versoes_preset(id),
  curriculo_versao_id INTEGER NOT NULL REFERENCES curriculos(id),
  pontuacao_ponderada REAL,
  confianca REAL CHECK (confianca BETWEEN 0 AND 100),
  pontuacao_final REAL,
  detalhe_json TEXT,
  criado_em TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (disputa_id, candidato_id, papel_id, versao_preset_id, curriculo_versao_id)
);

CREATE TABLE alocacoes (
  id INTEGER PRIMARY KEY,
  disputa_id INTEGER NOT NULL REFERENCES disputas(id),
  papel_id INTEGER NOT NULL REFERENCES papeis(id),
  candidato_id INTEGER NOT NULL REFERENCES candidatos(id),
  status TEXT NOT NULL DEFAULT 'alocado'
      CHECK (status IN ('alocado','reserva'))
);

-- um candidato ocupa no maximo uma alocacao ativa por disputa
-- (pode concorrer a varios papeis, mas so aloca em um; reservas nao contam)
CREATE UNIQUE INDEX idx_alocacao_ativa_unica
  ON alocacoes(disputa_id, candidato_id) WHERE status = 'alocado';

-- ---------- governanca ----------

CREATE TABLE cofre_dados_pessoais (
  id INTEGER PRIMARY KEY,
  candidato_id INTEGER NOT NULL REFERENCES candidatos(id),
  tipo TEXT NOT NULL CHECK (tipo IN ('email','telefone','endereco')),
  valor_criptografado TEXT NOT NULL,
  criptografado INTEGER NOT NULL DEFAULT 1 CHECK (criptografado IN (0,1)),
  criado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE registros_auditoria (
  id INTEGER PRIMARY KEY,
  acao TEXT NOT NULL,
  ator TEXT,
  entidade TEXT NOT NULL,
  entidade_id INTEGER,
  detalhes_json TEXT,
  data_hora TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE inbox_arquivos (
  id INTEGER PRIMARY KEY,
  nome_arquivo TEXT NOT NULL,
  caminho TEXT NOT NULL,
  checksum TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'pendente'
      CHECK (status IN ('pendente','processado','erro','duplicado')),
  candidato_id INTEGER REFERENCES candidatos(id),
  curriculo_id INTEGER REFERENCES curriculos(id),
  versao INTEGER NOT NULL DEFAULT 1,
  erro TEXT,
  criado_em TEXT NOT NULL DEFAULT (datetime('now')),
  atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------- indices de acesso ----------

CREATE INDEX idx_curriculos_candidato ON curriculos(candidato_id);
CREATE INDEX idx_evidencias_fato ON evidencias(tipo_fato, fato_id);
CREATE INDEX idx_pontuacoes_disputa ON pontuacoes(disputa_id);
CREATE INDEX idx_auditoria_entidade ON registros_auditoria(entidade, entidade_id);
"""

TABELAS_ESPERADAS = {
    "candidatos",
    "curriculos",
    "experiencias",
    "competencias",
    "certificacoes",
    "formacoes",
    "idiomas",
    "evidencias",
    "residual",
    "papeis",
    "presets",
    "versoes_preset",
    "disputas",
    "pontuacoes",
    "alocacoes",
    "cofre_dados_pessoais",
    "registros_auditoria",
    "inbox_arquivos",
}


def conectar(caminho: str | Path) -> sqlite3.Connection:
    """Abre o banco com foreign_keys ativo e linhas como dict-like."""
    con = sqlite3.connect(caminho)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def criar_schema(con: sqlite3.Connection) -> None:
    con.executescript(DDL)
    con.commit()


def tabelas(con: sqlite3.Connection) -> set[str]:
    linhas = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row["name"] for row in linhas}
