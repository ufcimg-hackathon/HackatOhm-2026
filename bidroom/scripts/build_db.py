"""Fase 1 — cria o banco demo (schema + configuração mínima).

Uso:
  python scripts/build_db.py [--db data/demo.db]

O que faz nesta fase:
1. cria o schema completo (handoff §4);
2. semeia os 4 papéis do preset e a versão 1 **publicada** do preset
   "TI para o setor de energia", com pesos (handoff §6.3) e gates G1–G6;
3. registra o evento em auditoria.

A ingestão/extração dos 228 entra na Fase 2 (este script vira o ponto de entrada).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# bootstrap: rodar via `python scripts/build_db.py` ou importado nos testes
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.governance.auditoria import registrar
from src.ingestion.db import conectar, criar_schema, tabelas

PAPEIS = [
    ("PM/Scrum Sênior", "pm_scrum", {"anos_minimos": 5, "nivel": "senior", "vagas": 1}),
    ("BA/BSA Sênior", "ba_bsa", {"anos_minimos": 5, "nivel": "senior", "vagas": 1}),
    ("Java Developer Sênior", "java", {"anos_minimos": 5, "nivel": "senior", "vagas": 2}),
    ("BI/Data Analyst Sênior", "bi_data", {"anos_minimos": 5, "nivel": "senior", "vagas": 1}),
]

# Pesos por categoria (handoff §6.3) — v1 publicada na Fase 3
PESOS_ENERGIA_V1 = {
    "piso_confianca": 0.6,
    "categorias": {
        "pm_scrum": {
            "aderencia": 30, "dominio_energia": 20, "certificacoes": 15,
            "experiencia": 20, "idioma": 5, "educacao": 5, "ferramentas": 5,
        },
        "ba_bsa": {
            "aderencia": 30, "dominio_energia": 20, "certificacoes": 10,
            "experiencia": 20, "idioma": 10, "educacao": 5, "ferramentas": 5,
        },
        "java": {
            "aderencia": 40, "dominio_energia": 15, "certificacoes": 10,
            "experiencia": 20, "idioma": 5, "educacao": 5, "ferramentas": 5,
        },
        "bi_data": {
            "aderencia": 40, "dominio_energia": 15, "certificacoes": 10,
            "experiencia": 20, "idioma": 5, "educacao": 5, "ferramentas": 5,
        },
    },
}

# Gates G1–G6 (handoff §6.1) — configuráveis na versão do preset
GATES_ENERGIA_V1 = {
    "G1": {"ativo": True, "min_core": 3, "min_core_n2": 2},
    "G2": {
        "ativo": True,
        "anos_minimos": {"pm_scrum": 5, "ba_bsa": 5, "java": 5, "bi_data": 5},
    },
    "G3": {
        "ativo": True,
        "niveis_aceitos": [
            "fluente", "fluent", "avancado", "avançado", "advanced",
            "nativo", "native",
        ],
    },
    "G4": {"ativo": True},
    "G5": {"ativo": True},
    "G6": {"ativo": True, "min_core_n2": 2},
}


def construir(caminho: str | Path) -> dict:
    destino = Path(caminho)
    destino.parent.mkdir(parents=True, exist_ok=True)
    con = conectar(destino)
    try:
        criar_schema(con)
        for nome, sigla, requisitos in PAPEIS:
            con.execute(
                "INSERT INTO papeis (nome, sigla, requisitos_json) VALUES (?, ?, ?)",
                (nome, sigla, json.dumps(requisitos, ensure_ascii=False)),
            )
        con.execute(
            "INSERT INTO presets (nome, descricao, dominio, status, dono)"
            " VALUES (?, ?, ?, 'rascunho', 'time')",
            (
                "TI para o setor de energia",
                "Edital ficticio de servicos de TI para utility/energia",
                "energia",
            ),
        )
        preset_id = con.execute("SELECT id FROM presets LIMIT 1").fetchone()["id"]
        con.execute(
            "INSERT INTO versoes_preset (preset_id, numero, pesos_json, gates_json,"
            " status, autor) VALUES (?, 1, ?, ?, 'publicada', 'build')",
            (
                preset_id,
                json.dumps(PESOS_ENERGIA_V1, ensure_ascii=False),
                json.dumps(GATES_ENERGIA_V1, ensure_ascii=False),
            ),
        )
        registrar(
            con,
            acao="seed_config_inicial",
            ator="build",
            entidade="banco",
            entidade_id=None,
            detalhes={"tabelas": len(tabelas(con)), "papeis": len(PAPEIS)},
        )
        con.commit()
        return {
            "tabelas": len(tabelas(con)),
            "papeis": con.execute("SELECT COUNT(*) c FROM papeis").fetchone()["c"],
            "presets": con.execute("SELECT COUNT(*) c FROM presets").fetchone()["c"],
            "versoes_preset": con.execute(
                "SELECT COUNT(*) c FROM versoes_preset"
            ).fetchone()["c"],
        }
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/demo.db")
    args = parser.parse_args()
    resumo = construir(args.db)
    print(f"banco criado em {args.db}")
    for chave, valor in resumo.items():
        print(f"  {chave}: {valor}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
