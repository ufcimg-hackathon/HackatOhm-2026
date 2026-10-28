"""Fase 3 — leitura da versão do preset vigente (pesos + gates)."""
from __future__ import annotations

import json
from dataclasses import dataclass


CATEGORIAS = (
    "aderencia", "dominio_energia", "certificacoes", "experiencia",
    "idioma", "educacao", "ferramentas",
)
PAPEIS = ("pm_scrum", "ba_bsa", "java", "bi_data")


@dataclass
class PresetConfig:
    versao_preset_id: int
    preset_id: int
    numero: int
    status: str
    pesos: dict
    gates: dict


def carregar_versao(con, versao_preset_id: int | None = None) -> PresetConfig:
    """Carrega a versão pedida ou a publicada mais recente (fallback: última)."""
    if versao_preset_id is not None:
        row = con.execute(
            "SELECT * FROM versoes_preset WHERE id = ?", (versao_preset_id,)
        ).fetchone()
    else:
        row = con.execute(
            "SELECT * FROM versoes_preset WHERE status = 'publicada'"
            " ORDER BY numero DESC, id DESC LIMIT 1"
        ).fetchone()
        if row is None:
            row = con.execute(
                "SELECT * FROM versoes_preset ORDER BY numero DESC, id DESC LIMIT 1"
            ).fetchone()
    if row is None:
        raise ValueError("nenhuma versão de preset encontrada no banco")

    pesos = json.loads(row["pesos_json"] or "{}")
    # compatibilidade com o seed antigo (pesos flat por papel, sem piso)
    if "categorias" not in pesos:
        pesos = {"piso_confianca": 0.6, "categorias": pesos}
    gates = json.loads(row["gates_json"] or "{}")
    return PresetConfig(
        versao_preset_id=int(row["id"]),
        preset_id=int(row["preset_id"]),
        numero=int(row["numero"]),
        status=row["status"],
        pesos=pesos,
        gates=gates,
    )


def validar_pesos(pesos: dict) -> None:
    """Valida que os pesos satisfazem as restrições do preset."""
    piso = pesos.get("piso_confianca")
    if (
        not isinstance(piso, (int, float))
        or isinstance(piso, bool)
        or not 0.0 <= float(piso) <= 1.0
    ):
        raise ValueError("piso_confianca deve ser um número entre 0 e 1")

    categorias = pesos.get("categorias")
    if not isinstance(categorias, dict):
        raise ValueError("pesos precisa ter a chave 'categorias'")

    for papel in PAPEIS:
        valores = categorias.get(papel)
        if not isinstance(valores, dict):
            raise ValueError(f"pesos ausentes para o papel {papel}")
        faltando = [c for c in CATEGORIAS if c not in valores]
        if faltando:
            raise ValueError(f"{papel}: categorias ausentes: {faltando}")
        soma = sum(float(valores[c]) for c in CATEGORIAS)
        if abs(soma - 100.0) > 0.01:
            raise ValueError(f"{papel}: pesos somam {soma}, esperado 100")


def proxima_versao(con, preset_id: int) -> int:
    """Próximo número de versão para o given preset_id."""
    row = con.execute(
        "SELECT COALESCE(MAX(numero), 0) + 1 AS proximo"
        " FROM versoes_preset WHERE preset_id = ?",
        (preset_id,),
    ).fetchone()
    return int(row["proximo"])


def listar_versoes(con, preset_id: int):
    """Lista todas as versões de um preset, ordenadas do mais novo para o mais velho."""
    return con.execute(
        "SELECT id, numero, status, autor, criado_em"
        " FROM versoes_preset WHERE preset_id = ?"
        " ORDER BY numero DESC, id DESC",
        (preset_id,),
    ).fetchall()