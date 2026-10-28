"""Fase 1 — testes do build_db (schema + seed de configuração)."""
import json

from scripts.build_db import construir
from src.ingestion.db import TABELAS_ESPERADAS, conectar


def test_build_db_cria_schema_e_seed(tmp_path):
    destino = tmp_path / "demo.db"
    resumo = construir(destino)
    assert destino.exists()
    assert resumo["tabelas"] == len(TABELAS_ESPERADAS)
    assert resumo["papeis"] == 4
    assert resumo["presets"] == 1
    assert resumo["versoes_preset"] == 1


def test_preset_v1_publicado_com_pesos_e_gates(tmp_path):
    destino = tmp_path / "demo.db"
    construir(destino)
    con = conectar(destino)
    try:
        row = con.execute(
            "SELECT status, pesos_json, gates_json FROM versoes_preset"
        ).fetchone()
        assert row["status"] == "publicada"
        pesos = json.loads(row["pesos_json"])
        gates = json.loads(row["gates_json"])
        assert set(pesos["categorias"]) == {"pm_scrum", "ba_bsa", "java", "bi_data"}
        for sigla, categorias in pesos["categorias"].items():
            assert abs(sum(categorias.values()) - 100) < 0.01, sigla
        assert set(gates) == {"G1", "G2", "G3", "G4", "G5", "G6"}
    finally:
        con.close()
