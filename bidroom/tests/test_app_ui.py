"""Fase 4 — smoke das telas Streamlit com um banco sintético."""
from __future__ import annotations

import json
from pathlib import Path

from streamlit.testing.v1 import AppTest

from scripts.build_db import construir
from src.ingestion.db import conectar

ROOT = Path(__file__).resolve().parents[1]


def _banco(tmp_path: Path) -> Path:
    db = tmp_path / "app.db"
    construir(db)
    con = conectar(db)
    try:
        papeis = {
            row["sigla"]: int(row["id"])
            for row in con.execute("SELECT id, sigla FROM papeis")
        }
        candidato = con.execute(
            "INSERT INTO candidatos (nome_pseudonimo, status_curacao)"
            " VALUES ('Candidato 001', 'curado')"
        ).lastrowid
        curriculo = con.execute(
            "INSERT INTO curriculos (candidato_id, nome_arquivo, caminho_cache,"
            " checksum, n_paginas, versao) VALUES (?, 'cv.docx', 'cv.docx', 'abc', 2, 1)",
            (candidato,),
        ).lastrowid
        con.execute(
            "INSERT INTO competencias (curriculo_id, nome, anos, familia)"
            " VALUES (?, 'Java', 5, 'linguagem')",
            (curriculo,),
        )
        detalhe = {
            "categorias": [
                {
                    "categoria": "aderencia",
                    "score": 100,
                    "peso": 100,
                    "confianca": 50,
                    "contribuicao": 100,
                    "detalhe": {"presentes": ["Java"], "faltantes": []},
                }
            ],
            "gates": [{"gate": "G5", "status": "revisar", "motivo": "disponibilidade"}],
            "status_gates": "revisar",
            "ancoras_pm": [],
            "anos_experiencia": 5,
        }
        con.execute(
            "INSERT INTO disputas (nome_ficticio, preset_versao_id) VALUES ('Demo UI', 1)"
        )
        disputa_id = con.execute("SELECT id FROM disputas").fetchone()["id"]
        for papel in ("pm_scrum", "ba_bsa", "java", "bi_data"):
            con.execute(
                "INSERT INTO pontuacoes (disputa_id, candidato_id, papel_id,"
                " versao_preset_id, curriculo_versao_id, pontuacao_ponderada,"
                " confianca, pontuacao_final, detalhe_json)"
                " VALUES (?, ?, ?, 1, ?, 50, 50, 55, ?)",
                (disputa_id, candidato, papeis[papel], curriculo, json.dumps(detalhe)),
            )
        con.commit()
    finally:
        con.close()
    return db


def test_t1_t2_t3_t4_renderizam(tmp_path, monkeypatch):
    db = _banco(tmp_path)
    monkeypatch.setenv("BIDROOM_DB", str(db))
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=40)
    at.run()
    assert not at.exception
    assert at.title[0].value == "Dashboard de impacto"

    for pagina, titulo in [
        ("Bid Room", "Bid Room — ranking"),
        ("Perfil", "Candidato 001"),
        ("Alocação", "Alocação do time"),
    ]:
        at.session_state["pagina"] = pagina
        at.run()
        assert not at.exception, at.exception
        assert at.title[0].value == titulo
