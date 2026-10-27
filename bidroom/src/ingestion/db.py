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

CREATE TABLE IF NOT EXISTS candidatos (
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

CREATE TABLE IF NOT EXISTS curriculos (
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

CREATE TABLE IF NOT EXISTS experiencias (
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

CREATE TABLE IF NOT EXISTS competencias (
  id INTEGER PRIMARY KEY,
  curriculo_id INTEGER NOT NULL REFERENCES curriculos(id),
  nome TEXT NOT NULL,
  anos REAL,
  ultimo_uso TEXT,
  familia TEXT
);

-- certificacoes nascem N0: nenhuma da base tem anexo (handoff §4)
CREATE TABLE IF NOT EXISTS certificacoes (
  id INTEGER PRIMARY KEY,
  curriculo_id INTEGER NOT NULL REFERENCES curriculos(id),
  nome TEXT NOT NULL,
  emissor TEXT,
  ano INTEGER,
  data_conclusao TEXT
);

CREATE TABLE IF NOT EXISTS formacoes (
  id INTEGER PRIMARY KEY,
  curriculo_id INTEGER NOT NULL REFERENCES curriculos(id),
  grau TEXT,
  curso TEXT,
  instituicao TEXT,
  ano_inicio INTEGER,
  ano_fim INTEGER
);

CREATE TABLE IF NOT EXISTS idiomas (
  id INTEGER PRIMARY KEY,
  curriculo_id INTEGER NOT NULL REFERENCES curriculos(id),
  idioma TEXT NOT NULL,
  nivel TEXT
);

-- evidencia polimorfica: (tipo_fato, fato_id) aponta para o fato
CREATE TABLE IF NOT EXISTS evidencias (
  id INTEGER PRIMARY KEY,
  tipo_fato TEXT NOT NULL,
  fato_id INTEGER NOT NULL,
  trecho TEXT NOT NULL,
  pagina INTEGER,
  nivel INTEGER NOT NULL CHECK (nivel IN (0,1,2,3,4)),
  confianca INTEGER NOT NULL CHECK (confianca IN (0,25,50,75,100))
);

-- mencoes nao mapeadas pela taxonomia (nao pontuam; evolucao futura — §5.4)
CREATE TABLE IF NOT EXISTS residual (
  id INTEGER PRIMARY KEY,
  curriculo_id INTEGER NOT NULL REFERENCES curriculos(id),
  termo TEXT,
  trecho TEXT NOT NULL
);

-- ---------- configuracao de edital ----------

CREATE TABLE IF NOT EXISTS papeis (
  id INTEGER PRIMARY KEY,
  nome TEXT NOT NULL,
  sigla TEXT NOT NULL UNIQUE,
  requisitos_json TEXT
);

CREATE TABLE IF NOT EXISTS presets (
  id INTEGER PRIMARY KEY,
  nome TEXT NOT NULL UNIQUE,
  descricao TEXT,
  dominio TEXT,
  status TEXT NOT NULL DEFAULT 'rascunho',
  dono TEXT
);

CREATE TABLE IF NOT EXISTS versoes_preset (
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

CREATE TABLE IF NOT EXISTS disputas (
  id INTEGER PRIMARY KEY,
  nome_ficticio TEXT NOT NULL,
  cliente_ficticio TEXT,
  preset_versao_id INTEGER REFERENCES versoes_preset(id),
  edital_versao_id INTEGER,
  status TEXT NOT NULL DEFAULT 'aberta'
);

-- ---------- edital e demanda estruturada ----------

CREATE TABLE IF NOT EXISTS editais (
  id INTEGER PRIMARY KEY,
  nome TEXT NOT NULL,
  cliente_ficticio TEXT,
  descricao TEXT,
  status TEXT NOT NULL DEFAULT 'rascunho'
      CHECK (status IN ('rascunho','em_revisao','ativo','arquivado')),
  dono TEXT,
  criado_em TEXT NOT NULL DEFAULT (datetime('now')),
  atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS edital_versoes (
  id INTEGER PRIMARY KEY,
  edital_id INTEGER NOT NULL REFERENCES editais(id),
  numero INTEGER NOT NULL,
  tipo TEXT NOT NULL DEFAULT 'original'
      CHECK (tipo IN ('original','retificacao')),
  versao_anterior_id INTEGER REFERENCES edital_versoes(id),
  nome_arquivo TEXT NOT NULL,
  caminho TEXT NOT NULL,
  checksum TEXT NOT NULL,
  texto_extraido TEXT,
  n_paginas INTEGER,
  status TEXT NOT NULL DEFAULT 'recebida'
      CHECK (status IN ('recebida','extraida','em_revisao','aprovada','ativa','substituida','erro')),
  efeito_json TEXT,
  observacao TEXT,
  autor TEXT,
  criado_em TEXT NOT NULL DEFAULT (datetime('now')),
  atualizado_em TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (edital_id, numero),
  UNIQUE (edital_id, checksum)
);

CREATE TABLE IF NOT EXISTS edital_requisitos (
  id INTEGER PRIMARY KEY,
  edital_versao_id INTEGER NOT NULL REFERENCES edital_versoes(id),
  tipo TEXT NOT NULL CHECK (tipo IN (
    'papel','vaga','competencia','experiencia_minima','certificacao',
    'comprovante','periodo_trabalho','criterio_eliminatorio','pontuacao',
    'limite','regra_combinacao','tratamento_documento','outro'
  )),
  escopo TEXT NOT NULL DEFAULT 'individual'
      CHECK (escopo IN ('individual','equipe','empresa')),
  chave TEXT,
  descricao TEXT NOT NULL,
  descricao_original TEXT,
  regra_json TEXT,
  regra_original_json TEXT,
  trecho TEXT NOT NULL,
  localizacao TEXT NOT NULL,
  pagina INTEGER,
  obrigatorio INTEGER NOT NULL DEFAULT 1 CHECK (obrigatorio IN (0,1)),
  ambiguidade INTEGER NOT NULL DEFAULT 0 CHECK (ambiguidade IN (0,1)),
  ambiguidade_motivo TEXT,
  status TEXT NOT NULL DEFAULT 'pendente'
      CHECK (status IN ('pendente','aprovado','rejeitado','alterado')),
  revisor TEXT,
  revisado_em TEXT,
  observacao_revisao TEXT,
  criado_em TEXT NOT NULL DEFAULT (datetime('now')),
  atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS edital_anexos (
  id INTEGER PRIMARY KEY,
  edital_versao_id INTEGER NOT NULL REFERENCES edital_versoes(id),
  nome_arquivo TEXT NOT NULL,
  caminho TEXT NOT NULL,
  checksum TEXT NOT NULL,
  tipo TEXT,
  criado_em TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (edital_versao_id, checksum)
);

-- ---------- decisao ----------

CREATE TABLE IF NOT EXISTS pontuacoes (
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

-- pontuação dinâmica calculada contra uma versão aprovada de edital.
-- A tabela legada `pontuacoes` continua válida para disputas com preset fixo.
CREATE TABLE IF NOT EXISTS pontuacoes_demanda (
  id INTEGER PRIMARY KEY,
  disputa_id INTEGER NOT NULL REFERENCES disputas(id),
  candidato_id INTEGER NOT NULL REFERENCES candidatos(id),
  papel_id INTEGER NOT NULL REFERENCES papeis(id),
  edital_versao_id INTEGER NOT NULL REFERENCES edital_versoes(id),
  curriculo_versao_id INTEGER NOT NULL REFERENCES curriculos(id),
  nota_tecnica REAL NOT NULL DEFAULT 0,
  score_interno_apoio REAL NOT NULL DEFAULT 0,
  confianca REAL NOT NULL DEFAULT 0 CHECK (confianca BETWEEN 0 AND 100),
  atualidade REAL NOT NULL DEFAULT 0 CHECK (atualidade BETWEEN 0 AND 100),
  status TEXT NOT NULL DEFAULT 'pendente'
      CHECK (status IN ('elegivel','pendente','eliminado')),
  detalhe_json TEXT,
  criado_em TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (disputa_id, candidato_id, papel_id, edital_versao_id,
          curriculo_versao_id)
);

CREATE TABLE IF NOT EXISTS alocacoes (
  id INTEGER PRIMARY KEY,
  disputa_id INTEGER NOT NULL REFERENCES disputas(id),
  papel_id INTEGER NOT NULL REFERENCES papeis(id),
  candidato_id INTEGER NOT NULL REFERENCES candidatos(id),
  status TEXT NOT NULL DEFAULT 'alocado'
      CHECK (status IN ('alocado','reserva'))
);

-- um candidato ocupa no maximo uma alocacao ativa por disputa
-- (pode concorrer a varios papeis, mas so aloca em um; reservas nao contam)
CREATE UNIQUE INDEX IF NOT EXISTS idx_alocacao_ativa_unica
  ON alocacoes(disputa_id, candidato_id) WHERE status = 'alocado';

-- ---------- governanca ----------

CREATE TABLE IF NOT EXISTS cofre_dados_pessoais (
  id INTEGER PRIMARY KEY,
  candidato_id INTEGER NOT NULL REFERENCES candidatos(id),
  tipo TEXT NOT NULL CHECK (tipo IN ('email','telefone','endereco')),
  valor_criptografado TEXT NOT NULL,
  criptografado INTEGER NOT NULL DEFAULT 1 CHECK (criptografado IN (0,1)),
  criado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS registros_auditoria (
  id INTEGER PRIMARY KEY,
  acao TEXT NOT NULL,
  ator TEXT,
  entidade TEXT NOT NULL,
  entidade_id INTEGER,
  detalhes_json TEXT,
  data_hora TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS inbox_arquivos (
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

-- ---------- atualidade, revisão e comprovação ----------

-- Estado de declaração/validação de cada fato. A frescura (atualizado,
-- atenção, desatualizado, vencido) é calculada a partir das datas; não é
-- sobrescrita pelo simples ato de reenviar um currículo.
CREATE TABLE IF NOT EXISTS atualidade_fatos (
  id INTEGER PRIMARY KEY,
  tipo_fato TEXT NOT NULL,
  fato_id INTEGER NOT NULL,
  responsavel TEXT,
  fonte TEXT,
  ultima_confirmacao TEXT,
  valido_ate TEXT,
  proxima_revisao TEXT,
  estado TEXT NOT NULL DEFAULT 'declarado'
      CHECK (estado IN ('desconhecido','declarado','enviado','validado','vencido')),
  criado_em TEXT NOT NULL DEFAULT (datetime('now')),
  atualizado_em TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (tipo_fato, fato_id)
);

CREATE TABLE IF NOT EXISTS disponibilidades (
  id INTEGER PRIMARY KEY,
  candidato_id INTEGER NOT NULL REFERENCES candidatos(id),
  inicio TEXT,
  fim TEXT,
  percentual INTEGER CHECK (percentual BETWEEN 0 AND 100),
  fonte TEXT,
  responsavel TEXT,
  confirmado_em TEXT,
  valido_ate TEXT,
  estado TEXT NOT NULL DEFAULT 'desconhecido'
      CHECK (estado IN ('desconhecido','declarado','confirmado','vencido')),
  criado_em TEXT NOT NULL DEFAULT (datetime('now')),
  atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS fila_revisao (
  id INTEGER PRIMARY KEY,
  candidato_id INTEGER NOT NULL REFERENCES candidatos(id),
  tipo TEXT NOT NULL,
  referencia_tipo TEXT,
  referencia_id INTEGER,
  motivo TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pendente'
      CHECK (status IN ('pendente','em_andamento','resolvida','ignorada')),
  responsavel TEXT,
  prazo TEXT,
  detalhes_json TEXT,
  erro TEXT,
  criado_em TEXT NOT NULL DEFAULT (datetime('now')),
  atualizado_em TEXT NOT NULL DEFAULT (datetime('now')),
  resolvido_em TEXT
);

CREATE TABLE IF NOT EXISTS atualizacoes_pendentes (
  id INTEGER PRIMARY KEY,
  disputa_id INTEGER NOT NULL REFERENCES disputas(id),
  candidato_id INTEGER REFERENCES candidatos(id),
  tipo TEXT NOT NULL,
  referencia_tipo TEXT,
  referencia_id INTEGER,
  motivo TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pendente'
      CHECK (status IN ('pendente','resolvida')),
  detalhes_json TEXT,
  erro TEXT,
  criado_em TEXT NOT NULL DEFAULT (datetime('now')),
  resolvido_em TEXT
);

CREATE TABLE IF NOT EXISTS evidencias_documentais (
  id INTEGER PRIMARY KEY,
  tipo_fato TEXT NOT NULL,
  fato_id INTEGER NOT NULL,
  nome_arquivo TEXT NOT NULL,
  caminho TEXT NOT NULL,
  checksum TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'enviado'
      CHECK (status IN ('enviado','validado','rejeitado','vencido')),
  revisor TEXT,
  enviado_em TEXT NOT NULL DEFAULT (datetime('now')),
  analisado_em TEXT,
  valido_ate TEXT,
  observacao TEXT,
  criado_em TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (tipo_fato, fato_id, checksum)
);

-- ---------- cultura, metas e reconhecimento (Etapa 7) ----------

CREATE TABLE IF NOT EXISTS programas_reconhecimento (
  id INTEGER PRIMARY KEY,
  nome TEXT NOT NULL,
  descricao TEXT,
  ano INTEGER NOT NULL,
  regra_json TEXT,
  visibilidade TEXT NOT NULL DEFAULT 'agregado'
      CHECK (visibilidade IN ('agregado','publico','privado')),
  ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0,1)),
  criado_em TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS metas_desenvolvimento (
  id INTEGER PRIMARY KEY,
  candidato_id INTEGER NOT NULL REFERENCES candidatos(id),
  programa_id INTEGER REFERENCES programas_reconhecimento(id),
  descricao TEXT NOT NULL,
  certificacao_alvo TEXT,
  prazo TEXT,
  status TEXT NOT NULL DEFAULT 'pendente'
      CHECK (status IN ('pendente','concluida','cancelada')),
  criado_em TEXT NOT NULL DEFAULT (datetime('now')),
  concluido_em TEXT
);

-- Conquista é diferente de "documento enviado": a data de conclusão manda no
-- ano de referência; o envio tardio do comprovante não move a conquista para
-- o ano atual. A chave única impede que reenvios inflem a contagem.
CREATE TABLE IF NOT EXISTS conquistas (
  id INTEGER PRIMARY KEY,
  candidato_id INTEGER NOT NULL REFERENCES candidatos(id),
  certificacao_id INTEGER NOT NULL REFERENCES certificacoes(id),
  programa_id INTEGER NOT NULL REFERENCES programas_reconhecimento(id),
  ano_referencia INTEGER NOT NULL,
  data_conclusao TEXT,
  status TEXT NOT NULL DEFAULT 'pendente'
      CHECK (status IN ('pendente','validada','rejeitada','revogada')),
  evidencia_id INTEGER REFERENCES evidencias_documentais(id),
  criado_em TEXT NOT NULL DEFAULT (datetime('now')),
  validado_em TEXT,
  motivo TEXT,
  UNIQUE (candidato_id, certificacao_id, programa_id, ano_referencia)
);

-- ---------- indices de acesso ----------

CREATE INDEX IF NOT EXISTS idx_curriculos_candidato ON curriculos(candidato_id);
CREATE INDEX IF NOT EXISTS idx_evidencias_fato ON evidencias(tipo_fato, fato_id);
CREATE INDEX IF NOT EXISTS idx_pontuacoes_disputa ON pontuacoes(disputa_id);
CREATE INDEX IF NOT EXISTS idx_pontuacoes_demanda_disputa
  ON pontuacoes_demanda(disputa_id, edital_versao_id);
CREATE INDEX IF NOT EXISTS idx_auditoria_entidade ON registros_auditoria(entidade, entidade_id);
CREATE INDEX IF NOT EXISTS idx_atualidade_fato
  ON atualidade_fatos(tipo_fato, fato_id);
CREATE INDEX IF NOT EXISTS idx_disponibilidades_candidato
  ON disponibilidades(candidato_id);
CREATE INDEX IF NOT EXISTS idx_fila_revisao_status
  ON fila_revisao(status, candidato_id);
CREATE INDEX IF NOT EXISTS idx_atualizacoes_pendentes_disputa
  ON atualizacoes_pendentes(disputa_id, status);
CREATE INDEX IF NOT EXISTS idx_evidencias_documentais_fato
  ON evidencias_documentais(tipo_fato, fato_id);
CREATE INDEX IF NOT EXISTS idx_edital_versoes_edital
  ON edital_versoes(edital_id, numero);
CREATE INDEX IF NOT EXISTS idx_edital_requisitos_versao
  ON edital_requisitos(edital_versao_id, status);
CREATE INDEX IF NOT EXISTS idx_edital_requisitos_tipo
  ON edital_requisitos(tipo, escopo);
CREATE INDEX IF NOT EXISTS idx_edital_anexos_versao
  ON edital_anexos(edital_versao_id);
CREATE INDEX IF NOT EXISTS idx_disputas_edital_versao
  ON disputas(edital_versao_id);

-- Etapa 8: índices para o caminho quente de ranking/composição e cultura.
CREATE INDEX IF NOT EXISTS idx_curriculos_candidato_versao
  ON curriculos(candidato_id, versao);
CREATE INDEX IF NOT EXISTS idx_alocacoes_candidato_status
  ON alocacoes(candidato_id, status, disputa_id);
CREATE INDEX IF NOT EXISTS idx_alocacoes_disputa_status
  ON alocacoes(disputa_id, status);
CREATE INDEX IF NOT EXISTS idx_evidencias_documentais_fato_status
  ON evidencias_documentais(tipo_fato, fato_id, status, valido_ate);
CREATE INDEX IF NOT EXISTS idx_pontuacoes_demanda_lookup
  ON pontuacoes_demanda(disputa_id, edital_versao_id, candidato_id);
CREATE INDEX IF NOT EXISTS idx_conquistas_programa_ano
  ON conquistas(programa_id, ano_referencia, status);
CREATE INDEX IF NOT EXISTS idx_conquistas_candidato
  ON conquistas(candidato_id);
CREATE INDEX IF NOT EXISTS idx_metas_candidato_status
  ON metas_desenvolvimento(candidato_id, status);
CREATE INDEX IF NOT EXISTS idx_programas_ano_ativo
  ON programas_reconhecimento(ano, ativo);
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
    "pontuacoes_demanda",
    "alocacoes",
    "cofre_dados_pessoais",
    "registros_auditoria",
    "inbox_arquivos",
    "atualidade_fatos",
    "disponibilidades",
    "fila_revisao",
    "atualizacoes_pendentes",
    "evidencias_documentais",
    "editais",
    "edital_versoes",
    "edital_requisitos",
    "edital_anexos",
    "programas_reconhecimento",
    "metas_desenvolvimento",
    "conquistas",
}


def conectar(caminho: str | Path) -> sqlite3.Connection:
    """Abre o banco com foreign_keys ativo e linhas como dict-like."""
    con = sqlite3.connect(caminho)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def _garantir_coluna(
    con: sqlite3.Connection, tabela: str, coluna: str, definicao: str
) -> None:
    colunas = {
        row["name"] for row in con.execute(f"PRAGMA table_info({tabela})")
    }
    if coluna not in colunas:
        con.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {definicao}")


def migrar(con: sqlite3.Connection) -> None:
    """Migrações aditivas para bancos criados antes das etapas novas."""
    _garantir_coluna(con, "fila_revisao", "detalhes_json", "TEXT")
    _garantir_coluna(con, "fila_revisao", "erro", "TEXT")
    _garantir_coluna(con, "atualizacoes_pendentes", "detalhes_json", "TEXT")
    _garantir_coluna(con, "atualizacoes_pendentes", "erro", "TEXT")
    _garantir_coluna(con, "disputas", "edital_versao_id", "INTEGER")
    _garantir_coluna(con, "certificacoes", "data_conclusao", "TEXT")


def criar_schema(con: sqlite3.Connection) -> None:
    con.executescript(DDL)
    migrar(con)
    con.commit()


def tabelas(con: sqlite3.Connection) -> set[str]:
    linhas = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {row["name"] for row in linhas}
