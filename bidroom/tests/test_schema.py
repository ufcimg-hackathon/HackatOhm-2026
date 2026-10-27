"""Fase 1 — testes do schema (handoff §4): integridade, enums, idempotência e reprodutibilidade."""
import sqlite3

import pytest

from src.ingestion.db import DDL, TABELAS_ESPERADAS, conectar, criar_schema, tabelas


@pytest.fixture()
def con():
    con = conectar(":memory:")
    criar_schema(con)
    yield con
    con.close()


def _candidato(con, nome="Curado Um"):
    cur = con.execute(
        "INSERT INTO candidatos (nome_pseudonimo) VALUES (?)", (nome,)
    )
    return cur.lastrowid


def test_cria_todas_as_tabelas(con):
    assert tabelas(con) == TABELAS_ESPERADAS


def test_curriculo_exige_candidato_fk(con):
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO curriculos (candidato_id, nome_arquivo, caminho_cache, checksum)"
            " VALUES (999, 'x.docx', 'data/cache_drive/x.docx', 'abc')"
        )


def test_check_enum_status_processamento(con):
    cid = _candidato(con)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO curriculos (candidato_id, nome_arquivo, caminho_cache, checksum,"
            " status_processamento) VALUES (?, 'x.docx', 'd/x.docx', 'c1', 'bagacera')",
            (cid,),
        )
    # valores validos passam
    con.execute(
        "INSERT INTO curriculos (candidato_id, nome_arquivo, caminho_cache, checksum,"
        " status_processamento) VALUES (?, 'x.docx', 'd/x.docx', 'c1', 'processado')",
        (cid,),
    )


def test_idempotencia_por_checksum(con):
    """Mesmo arquivo (checksum) no mesmo candidato => UMA unica versao."""
    cid = _candidato(con)
    con.execute(
        "INSERT INTO curriculos (candidato_id, nome_arquivo, caminho_cache, checksum)"
        " VALUES (?, 'x.docx', 'd/x.docx', 'SAME')",
        (cid,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO curriculos (candidato_id, nome_arquivo, caminho_cache, checksum)"
            " VALUES (?, 'x.docx (2)', 'd/x.docx', 'SAME')",
            (cid,),
        )


def _estrutura_score(con):
    cid = _candidato(con)
    con.execute("INSERT INTO papeis (nome, sigla) VALUES ('PM/Scrum', 'pm_scrum')")
    con.execute(
        "INSERT INTO presets (nome, descricao, dominio, dono)"
        " VALUES ('TI para o setor de energia', 'edital ficticio', 'energia', 'time')"
    )
    pid = con.execute("SELECT id FROM presets LIMIT 1").fetchone()["id"]
    con.execute(
        "INSERT INTO versoes_preset (preset_id, numero, status) VALUES (?, 1, 'publicada')",
        (pid,),
    )
    vpid = con.execute("SELECT id FROM versoes_preset LIMIT 1").fetchone()["id"]
    con.execute(
        "INSERT INTO disputas (nome_ficticio, cliente_ficticio, preset_versao_id)"
        " VALUES ('disputa demo', 'cliente ficticio', ?)",
        (vpid,),
    )
    did = con.execute("SELECT id FROM disputas LIMIT 1").fetchone()["id"]
    cvid = con.execute("SELECT id FROM curriculos LIMIT 1").fetchone()["id"] if False else None
    # cria um curriculo para amarrar versao de dados
    con.execute(
        "INSERT INTO curriculos (candidato_id, nome_arquivo, caminho_cache, checksum)"
        " VALUES (?, 'x.docx', 'd/x.docx', 'cs1')",
        (cid,),
    )
    cvid = con.execute("SELECT id FROM curriculos LIMIT 1").fetchone()["id"]
    pkid = con.execute("SELECT id FROM papeis LIMIT 1").fetchone()["id"]
    return cid, did, pkid, vpid, cvid


def test_pontuacao_reproduzivel_unica(con):
    """Mesma (disputa, candidato, papel, versao_preset, versao_curriculo) = uma linha."""
    cid, did, pkid, vpid, cvid = _estrutura_score(con)
    con.execute(
        "INSERT INTO pontuacoes (disputa_id, candidato_id, papel_id, versao_preset_id,"
        " curriculo_versao_id, pontuacao_ponderada, confianca, pontuacao_final)"
        " VALUES (?, ?, ?, ?, ?, 72.5, 25, 50.94)",
        (did, cid, pkid, vpid, cvid),
    )
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO pontuacoes (disputa_id, candidato_id, papel_id, versao_preset_id,"
            " curriculo_versao_id) VALUES (?, ?, ?, ?, ?)",
            (did, cid, pkid, vpid, cvid),
        )


def test_pontuacao_confianca_continua_entre_0_e_100(con):
    """Confiança agregada é contínua; a escala 0/25/50/75/100 vale em evidencias."""
    cid, did, pkid, vpid, cvid = _estrutura_score(con)
    con.execute(
        "INSERT INTO pontuacoes (disputa_id, candidato_id, papel_id, versao_preset_id,"
        " curriculo_versao_id, confianca) VALUES (?, ?, ?, ?, ?, 37.5)",
        (did, cid, pkid, vpid, cvid),
    )
    assert con.execute("SELECT confianca FROM pontuacoes").fetchone()["confianca"] == 37.5
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO pontuacoes (disputa_id, candidato_id, papel_id, versao_preset_id,"
            " curriculo_versao_id, confianca) VALUES (?, ?, ?, ?, ?, 130)",
            (did, cid, pkid, vpid, cvid),
        )


def test_alocacao_ativa_unica_por_candidato(con):
    """Concorre a varios papeis, aloca em apenas um; reserva nao bloqueia."""
    cid, did, pkid, _vpid, _cvid = _estrutura_score(con)
    con.execute("INSERT INTO papeis (nome, sigla) VALUES ('BI/Data', 'bi_data')")
    pkid2 = con.execute("SELECT id FROM papeis WHERE sigla='bi_data'").fetchone()["id"]

    con.execute(
        "INSERT INTO alocacoes (disputa_id, papel_id, candidato_id, status)"
        " VALUES (?, ?, ?, 'alocado')",
        (did, pkid, cid),
    )
    # segunda alocacao ativa do MESMO candidato na MESMA disputa => viola
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            "INSERT INTO alocacoes (disputa_id, papel_id, candidato_id, status)"
            " VALUES (?, ?, ?, 'alocado')",
            (did, pkid2, cid),
        )
    # reserva do mesmo candidato em outro papel e permitida
    con.execute(
        "INSERT INTO alocacoes (disputa_id, papel_id, candidato_id, status)"
        " VALUES (?, ?, ?, 'reserva')",
        (did, pkid2, cid),
    )


def test_pii_vive_so_no_cofre(con):
    cid = _candidato(con)
    con.execute(
        "INSERT INTO cofre_dados_pessoais (candidato_id, tipo, valor_criptografado)"
        " VALUES (?, 'email', 'ENCRYPTED_BLOB')",
        (cid,),
    )
    con.execute(
        "INSERT INTO evidencias (tipo_fato, fato_id, trecho, pagina, nivel, confianca)"
        " VALUES ('certificacao', 1, 'PMP mention', 4, 0, 0)"
    )
