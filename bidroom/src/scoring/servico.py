"""Fase 4 — serviços de pontuação usados pela UI.

A UI não recalcula a disputa inteira a cada upload: atualiza somente o
candidato afetado, mantendo as pontuações antigas (reprodutibilidade) e
gravando as novas contra o currículo recém-versionado.
"""
from __future__ import annotations

import json
import sqlite3

from src.governance.auditoria import registrar
from src.scoring.catalogo import CATALOGO
from src.scoring.contexto import carregar_contexto
from src.scoring.engine import ResultadoScore, pontuar
from src.scoring.preset import (
    PresetConfig,
    carregar_versao,
    proxima_versao,
    validar_pesos,
)


def obter_disputa_atual(con: sqlite3.Connection) -> sqlite3.Row | None:
    """Disputa aberta mais recente; fallback para a última criada."""
    row = con.execute(
        "SELECT * FROM disputas WHERE status = 'aberta'"
        " ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        row = con.execute(
            "SELECT * FROM disputas ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return row


def ultimo_curriculo(con: sqlite3.Connection, candidato_id: int) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM curriculos WHERE candidato_id = ?"
        " ORDER BY versao DESC, id DESC LIMIT 1",
        (candidato_id,),
    ).fetchone()


def upsert_pontuacao(con: sqlite3.Connection, dados: dict) -> None:
    con.execute(
        """
        INSERT INTO pontuacoes (
            disputa_id, candidato_id, papel_id, versao_preset_id,
            curriculo_versao_id, pontuacao_ponderada, confianca,
            pontuacao_final, detalhe_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (disputa_id, candidato_id, papel_id, versao_preset_id,
                     curriculo_versao_id)
        DO UPDATE SET
            pontuacao_ponderada = excluded.pontuacao_ponderada,
            confianca = excluded.confianca,
            pontuacao_final = excluded.pontuacao_final,
            detalhe_json = excluded.detalhe_json,
            criado_em = datetime('now')
        """,
        (
            dados["disputa_id"],
            dados["candidato_id"],
            dados["papel_id"],
            dados["versao_preset_id"],
            dados["curriculo_versao_id"],
            dados["pontuacao_ponderada"],
            dados["confianca"],
            dados["pontuacao_final"],
            json.dumps(dados["detalhe"], ensure_ascii=False),
        ),
    )


def recalcular_candidato(
    con: sqlite3.Connection,
    candidato_id: int,
    ator: str = "ui",
) -> list[ResultadoScore]:
    """Recalcula todos os papéis do candidato no currículo mais recente.

    Devolve lista vazia quando não há disputa ativa; nesse caso o upload é
    mantido, mas a UI avisa que a nota será calculada na próxima disputa.
    """
    disputa = obter_disputa_atual(con)
    if disputa is None:
        return []
    preset: PresetConfig = carregar_versao(con, int(disputa["preset_versao_id"]))
    curriculo = ultimo_curriculo(con, candidato_id)
    if curriculo is None:
        return []

    papeis = {
        row["sigla"]: int(row["id"])
        for row in con.execute("SELECT id, sigla FROM papeis")
    }
    contexto = carregar_contexto(con, int(curriculo["id"]))
    resultados: list[ResultadoScore] = []
    for sigla, papel_id in papeis.items():
        perfil = CATALOGO.get(sigla)
        if perfil is None:
            continue
        resultado = pontuar(contexto, perfil, preset.pesos, preset.gates)
        upsert_pontuacao(
            con,
            {
                "disputa_id": int(disputa["id"]),
                "candidato_id": candidato_id,
                "papel_id": papel_id,
                "versao_preset_id": preset.versao_preset_id,
                "curriculo_versao_id": int(curriculo["id"]),
                "pontuacao_ponderada": resultado.pontuacao_ponderada,
                "confianca": resultado.confianca,
                "pontuacao_final": resultado.pontuacao_final,
                "detalhe": resultado.detalhe,
            },
        )
        resultados.append(resultado)
    registrar(
        con,
        acao="recalcular_score_candidato",
        ator=ator,
        entidade="candidatos",
        entidade_id=candidato_id,
        detalhes={
            "disputa_id": int(disputa["id"]),
            "curriculo_id": int(curriculo["id"]),
            "papel_ids": [papeis[r.papel] for r in resultados],
        },
        commit=False,
    )
    con.commit()
    return resultados


def recalcular_disputa(
    con: sqlite3.Connection,
    disputa_id: int,
    versao_preset_id: int,
    ator: str = "ui",
    commit: bool = True,
) -> dict:
    """Recalcula todas as pontuações da disputa para a versão do preset."""

    preset = carregar_versao(con, versao_preset_id)
    papeis = {
        row["sigla"]: int(row["id"])
        for row in con.execute("SELECT id, sigla FROM papeis")
    }
    candidatos = con.execute(
        """
        SELECT c.id AS candidato_id, cur.id AS curriculo_id
        FROM candidatos c
        JOIN curriculos cur ON cur.candidato_id = c.id
        WHERE cur.id = (
            SELECT MAX(id) FROM curriculos WHERE candidato_id = c.id
        )
        ORDER BY c.id
        """
    ).fetchall()

    resumo = {
        sigla: {"total": 0, "elegivel": 0, "revisar": 0, "eliminado": 0}
        for sigla in papeis
    }
    gravadas = 0

    for candidato in candidatos:
        contexto = carregar_contexto(con, int(candidato["curriculo_id"]))
        for sigla, papel_id in papeis.items():
            perfil = CATALOGO.get(sigla)
            if perfil is None:
                continue
            resultado = pontuar(contexto, perfil, preset.pesos, preset.gates)
            upsert_pontuacao(
                con,
                {
                    "disputa_id": disputa_id,
                    "candidato_id": int(candidato["candidato_id"]),
                    "papel_id": papel_id,
                    "versao_preset_id": preset.versao_preset_id,
                    "curriculo_versao_id": int(candidato["curriculo_id"]),
                    "pontuacao_ponderada": resultado.pontuacao_ponderada,
                    "confianca": resultado.confianca,
                    "pontuacao_final": resultado.pontuacao_final,
                    "detalhe": resultado.detalhe,
                },
            )
            resumo[sigla]["total"] += 1
            resumo[sigla][resultado.status_gates] += 1
            gravadas += 1

    registrar(
        con,
        acao="calcular_pontuacoes",
        ator=ator,
        entidade="disputas",
        entidade_id=disputa_id,
        detalhes={
            "versao_preset_id": preset.versao_preset_id,
            "pontuacoes": gravadas,
            "por_papel": resumo,
        },
        commit=False,
    )
    if commit:
        con.commit()
    return {"pontuacoes": gravadas, "por_papel": resumo}


def publicar_nova_versao(
    con: sqlite3.Connection,
    disputa_id: int,
    preset_id: int,
    pesos: dict,
    gates: dict,
    ator: str = "ui",
) -> dict:
    """Publica uma nova versão do preset e reponta a disputa ativa.

    - Valida pesos, insere nova versão, deprecia a anterior,
      reponta a disputa e recalcula as pontuações.
    - Retorna dict comversao_id, numero, pontuacoes, por_papel.
    """
    validar_pesos(pesos)

    row = con.execute(
        "SELECT preset_versao_id FROM disputas WHERE id = ?", (disputa_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"disputa {disputa_id} não encontrada")

    atual = carregar_versao(con, int(row["preset_versao_id"]))
    if atual.preset_id != int(preset_id):
        raise ValueError("a versão atual da disputa não pertence a este preset")

    numero = proxima_versao(con, int(preset_id))
    try:
        cur = con.execute(
            "INSERT INTO versoes_preset"
            " (preset_id, numero, pesos_json, gates_json, status, autor)"
            " VALUES (?, ?, ?, ?, 'publicada', ?)",
            (
                int(preset_id),
                numero,
                json.dumps(pesos, ensure_ascii=False),
                json.dumps(gates, ensure_ascii=False),
                ator,
            ),
        )
        novo_id = int(cur.lastrowid)

        con.execute(
            "UPDATE versoes_preset SET status = 'deprecada'"
            " WHERE preset_id = ? AND status = 'publicada' AND id <> ?",
            (int(preset_id), novo_id),
        )
        con.execute(
            "UPDATE disputas SET preset_versao_id = ? WHERE id = ?",
            (novo_id, disputa_id),
        )

        recalc = recalcular_disputa(
            con, disputa_id, novo_id, ator=ator, commit=False
        )
        registrar(
            con,
            acao="publicar_versao_preset",
            ator=ator,
            entidade="versoes_preset",
            entidade_id=novo_id,
            detalhes={
                "preset_id": int(preset_id),
                "numero": numero,
                "versao_anterior_id": atual.versao_preset_id,
                "disputa_id": disputa_id,
                "pontuacoes": recalc["pontuacoes"],
                "por_papel": recalc["por_papel"],
            },
            commit=False,
        )
        con.commit()
    except Exception:
        con.rollback()
        raise

    return {
        "versao_id": novo_id,
        "numero": numero,
        "pontuacoes": recalc["pontuacoes"],
        "por_papel": recalc["por_papel"],
    }


def rollback_preset(
    con: sqlite3.Connection,
    disputa_id: int,
    versao_id: int,
    ator: str = "ui",
) -> dict:
    """Reverte a disputa para a versão de preset indicada."""

    row = con.execute(
        "SELECT preset_versao_id FROM disputas WHERE id = ?", (disputa_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"disputa {disputa_id} não encontrada")

    atual = carregar_versao(con, int(row["preset_versao_id"]))
    alvo = carregar_versao(con, int(versao_id))

    if alvo.preset_id != atual.preset_id:
        raise ValueError("a versão de rollback não pertence ao mesmo preset")
    if alvo.status == "rascunho":
        raise ValueError("não é possível fazer rollback para um rascunho")
    if alvo.versao_preset_id == atual.versao_preset_id:
        return {
            "versao_id": alvo.versao_preset_id,
            "numero": alvo.numero,
            "recalculou": False,
        }

    try:
        con.execute(
            "UPDATE versoes_preset SET status = 'deprecada' WHERE id = ?",
            (atual.versao_preset_id,),
        )
        con.execute(
            "UPDATE versoes_preset SET status = 'publicada' WHERE id = ?",
            (alvo.versao_preset_id,),
        )
        con.execute(
            "UPDATE disputas SET preset_versao_id = ? WHERE id = ?",
            (alvo.versao_preset_id, disputa_id),
        )

        existe = con.execute(
            "SELECT COUNT(*) c FROM pontuacoes"
            " WHERE disputa_id = ? AND versao_preset_id = ?",
            (disputa_id, alvo.versao_preset_id),
        ).fetchone()["c"]
        recalculou = False
        if not existe:
            recalcular_disputa(
                con, disputa_id, alvo.versao_preset_id,
                ator=ator, commit=False,
            )
            recalculou = True

        registrar(
            con,
            acao="rollback_versao_preset",
            ator=ator,
            entidade="versoes_preset",
            entidade_id=alvo.versao_preset_id,
            detalhes={
                "disputa_id": disputa_id,
                "versao_anterior_id": atual.versao_preset_id,
                "versao_nova_id": alvo.versao_preset_id,
                "recalculou": recalculou,
            },
            commit=False,
        )
        con.commit()
    except Exception:
        con.rollback()
        raise

    return {
        "versao_id": alvo.versao_preset_id,
        "numero": alvo.numero,
        "recalculou": recalculou,
    }