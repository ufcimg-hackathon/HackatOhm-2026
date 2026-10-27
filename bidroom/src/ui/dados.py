"""Fase 4 — camada de dados da UI (T1–T4).

Funções puras sobre SQLite: nada de Streamlit aqui, para poder testar sem
renderizar a aplicação. A UI só exibe dados já redigidos pelo pipeline; PII
aparece como `***` e nunca é decifrada nestas consultas.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pandas as pd

from src.scoring.contexto import ContextoCandidato, carregar_contexto

ROTULO_STATUS_CURACAO = {
    "nao_curado": "Não curado",
    "curado": "Curado",
    "alocado": "Alocado",
}
ROTULO_GATE = {
    "elegivel": "Elegível",
    "revisar": "Revisar",
    "eliminado": "Eliminado",
}
COLUNAS_RANKING = [
    "candidato_id",
    "nome",
    "status_curacao",
    "papel",
    "papel_nome",
    "score",
    "ponderada",
    "confianca",
    "evidencia_max",
    "status_gates",
    "gates_passou",
    "gates_revisar",
    "gates_elimina",
    "gates_ok",
    "ancoras",
    "anos",
    "aderencia",
    "dominio_energia",
    "certificacoes",
    "experiencia",
    "idioma",
    "educacao",
    "ferramentas",
    "curriculo_versao_id",
    "curriculo_versao",
    "data_ingestao",
    "dias_ingestao",
]


def _json(texto: str | None) -> dict:
    if not texto:
        return {}
    try:
        return json.loads(texto)
    except (TypeError, ValueError):
        return {}


def _dias_desde(texto: str | None) -> int:
    if not texto:
        return 0
    try:
        data = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if data.tzinfo is None:
        data = data.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - data).days)


def carregar_disputa(con: sqlite3.Connection) -> dict | None:
    row = con.execute(
        "SELECT d.*, v.numero AS preset_numero, v.status AS preset_status,"
        " p.nome AS preset_nome"
        " FROM disputas d"
        " LEFT JOIN versoes_preset v ON v.id = d.preset_versao_id"
        " LEFT JOIN presets p ON p.id = v.preset_id"
        " ORDER BY d.id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def carregar_ranking(
    con: sqlite3.Connection,
    disputa_id: int,
    versao_preset_id: int,
) -> pd.DataFrame:
    """Ranking atual: somente a última versão de currículo de cada candidato."""
    rows = con.execute(
        """
        WITH ultimo AS (
            SELECT candidato_id, MAX(id) AS curriculo_id
            FROM curriculos GROUP BY candidato_id
        ),
        evidencias_cur AS (
            SELECT curriculo_id, MAX(nivel) AS evidencia_max
            FROM (
                SELECT f.curriculo_id, ev.nivel
                FROM evidencias ev
                JOIN experiencias f
                  ON ev.tipo_fato = 'experiencia' AND ev.fato_id = f.id
                UNION ALL
                SELECT f.curriculo_id, ev.nivel
                FROM evidencias ev
                JOIN competencias f
                  ON ev.tipo_fato = 'competencia' AND ev.fato_id = f.id
                UNION ALL
                SELECT f.curriculo_id, ev.nivel
                FROM evidencias ev
                JOIN certificacoes f
                  ON ev.tipo_fato = 'certificacao' AND ev.fato_id = f.id
                UNION ALL
                SELECT f.curriculo_id, ev.nivel
                FROM evidencias ev
                JOIN formacoes f
                  ON ev.tipo_fato = 'formacao' AND ev.fato_id = f.id
                UNION ALL
                SELECT f.curriculo_id, ev.nivel
                FROM evidencias ev
                JOIN idiomas f
                  ON ev.tipo_fato = 'idioma' AND ev.fato_id = f.id
            )
            GROUP BY curriculo_id
        )
        SELECT p.candidato_id, c.nome_pseudonimo, c.status_curacao,
               p.papel_id, pa.sigla AS papel, pa.nome AS papel_nome,
               p.pontuacao_ponderada, p.confianca, p.pontuacao_final,
               p.curriculo_versao_id, cur.versao AS curriculo_versao,
               cur.data_ingestao, p.detalhe_json,
               COALESCE(ev.evidencia_max, 0) AS evidencia_max
        FROM pontuacoes p
        JOIN ultimo u
          ON u.candidato_id = p.candidato_id
         AND u.curriculo_id = p.curriculo_versao_id
        JOIN candidatos c ON c.id = p.candidato_id
        JOIN papeis pa ON pa.id = p.papel_id
        JOIN curriculos cur ON cur.id = p.curriculo_versao_id
        LEFT JOIN evidencias_cur ev ON ev.curriculo_id = cur.id
        WHERE p.disputa_id = ? AND p.versao_preset_id = ?
        ORDER BY p.pontuacao_final DESC, c.id
        """,
        (disputa_id, versao_preset_id),
    ).fetchall()

    registros = []
    for row in rows:
        detalhe = _json(row["detalhe_json"])
        categorias = {
            item.get("categoria"): item for item in detalhe.get("categorias", [])
        }
        gates = detalhe.get("gates", [])
        ancoras = [
            (
                f"{item.get('nome')} (sem prova)"
                if item.get("declarada") and not item.get("atingida")
                else item.get("nome")
            )
            for item in detalhe.get("ancoras_pm", [])
            if item.get("atingida") or item.get("declarada")
        ]
        registros.append(
            {
                "candidato_id": int(row["candidato_id"]),
                "nome": row["nome_pseudonimo"],
                "status_curacao": row["status_curacao"],
                "papel": row["papel"],
                "papel_nome": row["papel_nome"],
                "score": round(float(row["pontuacao_final"] or 0), 2),
                "ponderada": round(float(row["pontuacao_ponderada"] or 0), 2),
                "confianca": round(float(row["confianca"] or 0), 2),
                "evidencia_max": int(row["evidencia_max"] or 0),
                "status_gates": detalhe.get("status_gates", "revisar"),
                "gates_passou": sum(
                    1 for g in gates if g.get("status") == "passou"
                ),
                "gates_revisar": sum(
                    1 for g in gates if g.get("status") == "revisar"
                ),
                "gates_elimina": sum(
                    1 for g in gates if g.get("status") == "elimina"
                ),
                "gates_ok": ", ".join(
                    str(g.get("gate"))
                    for g in gates
                    if g.get("status") == "passou"
                )
                or "—",
                "ancoras": ", ".join(ancoras),
                "anos": round(float(detalhe.get("anos_experiencia") or 0), 1),
                "aderencia": round(float(categorias.get("aderencia", {}).get("score", 0)), 2),
                "dominio_energia": round(
                    float(categorias.get("dominio_energia", {}).get("score", 0)), 2
                ),
                "certificacoes": round(
                    float(categorias.get("certificacoes", {}).get("score", 0)), 2
                ),
                "experiencia": round(
                    float(categorias.get("experiencia", {}).get("score", 0)), 2
                ),
                "idioma": round(float(categorias.get("idioma", {}).get("score", 0)), 2),
                "educacao": round(
                    float(categorias.get("educacao", {}).get("score", 0)), 2
                ),
                "ferramentas": round(
                    float(categorias.get("ferramentas", {}).get("score", 0)), 2
                ),
                "curriculo_versao_id": int(row["curriculo_versao_id"]),
                "curriculo_versao": int(row["curriculo_versao"]),
                "data_ingestao": row["data_ingestao"],
                "dias_ingestao": _dias_desde(row["data_ingestao"]),
            }
        )
    return pd.DataFrame(registros, columns=COLUNAS_RANKING)


def carregar_dashboard(con: sqlite3.Connection) -> dict:
    total_curriculos = con.execute(
        "SELECT COUNT(*) c FROM curriculos"
    ).fetchone()["c"]
    total_candidatos = con.execute(
        "SELECT COUNT(*) c FROM candidatos"
    ).fetchone()["c"]
    status = {
        row["status_curacao"]: row["c"]
        for row in con.execute(
            "SELECT status_curacao, COUNT(*) c FROM candidatos"
            " GROUP BY status_curacao"
        )
    }
    for chave in ROTULO_STATUS_CURACAO:
        status.setdefault(chave, 0)
    ultima_ingestao = con.execute(
        "SELECT MAX(data_ingestao) d FROM curriculos"
    ).fetchone()["d"]
    curriculos_erro = con.execute(
        "SELECT COUNT(*) c FROM curriculos WHERE status_processamento = 'erro'"
    ).fetchone()["c"]
    alocados = con.execute(
        "SELECT COUNT(DISTINCT candidato_id) c FROM alocacoes"
        " WHERE status = 'alocado'"
    ).fetchone()["c"]

    disputa = carregar_disputa(con)
    scores = pd.DataFrame(
        columns=["papel", "papel_nome", "score", "confianca", "status_gates"]
    )
    if disputa and disputa.get("preset_versao_id"):
        scores = _carregar_scores_atuais(
            con, int(disputa["id"]), int(disputa["preset_versao_id"])
        )

    faixa = {
        "min": round(float(scores["score"].min()), 2) if not scores.empty else 0.0,
        "max": round(float(scores["score"].max()), 2) if not scores.empty else 0.0,
        "mediana": round(float(scores["score"].median()), 2)
        if not scores.empty
        else 0.0,
    }
    por_papel = []
    if not scores.empty:
        for papel, grupo in scores.groupby("papel", sort=False):
            por_papel.append(
                {
                    "papel": papel,
                    "papel_nome": grupo.iloc[0]["papel_nome"],
                    "total": int(len(grupo)),
                    "elegivel": int((grupo["status_gates"] == "elegivel").sum()),
                    "revisar": int((grupo["status_gates"] == "revisar").sum()),
                    "eliminado": int((grupo["status_gates"] == "eliminado").sum()),
                    "score_max": round(float(grupo["score"].max()), 2),
                    "score_medio": round(float(grupo["score"].mean()), 2),
                }
            )
    return {
        "total_curriculos": int(total_curriculos),
        "total_candidatos": int(total_candidatos),
        "status": status,
        "curados": int(status.get("curado", 0)),
        "curados_ou_alocados": int(status.get("curado", 0))
        + int(status.get("alocado", 0)),
        "alocados": int(status.get("alocado", 0)),
        "alocacoes_ativas": int(alocados),
        "ultima_ingestao": ultima_ingestao,
        "curriculos_erro": int(curriculos_erro),
        "disputa": disputa,
        "scores": scores,
        "faixa": faixa,
        "por_papel": por_papel,
    }


def _carregar_scores_atuais(
    con: sqlite3.Connection, disputa_id: int, versao_preset_id: int
) -> pd.DataFrame:
    rows = con.execute(
        """
        WITH ultimo AS (
            SELECT candidato_id, MAX(id) AS curriculo_id
            FROM curriculos GROUP BY candidato_id
        )
        SELECT pa.sigla AS papel, pa.nome AS papel_nome,
               p.pontuacao_final AS score, p.confianca, p.detalhe_json
        FROM pontuacoes p
        JOIN ultimo u
          ON u.candidato_id = p.candidato_id
         AND u.curriculo_id = p.curriculo_versao_id
        JOIN papeis pa ON pa.id = p.papel_id
        WHERE p.disputa_id = ? AND p.versao_preset_id = ?
        """,
        (disputa_id, versao_preset_id),
    ).fetchall()
    return pd.DataFrame(
        [
            {
                "papel": row["papel"],
                "papel_nome": row["papel_nome"],
                "score": float(row["score"] or 0),
                "confianca": float(row["confianca"] or 0),
                "status_gates": _json(row["detalhe_json"]).get(
                    "status_gates", "revisar"
                ),
            }
            for row in rows
        ]
    )


def listar_candidatos(con: sqlite3.Connection) -> pd.DataFrame:
    rows = con.execute(
        """
        WITH ultimo AS (
            SELECT candidato_id, MAX(id) AS curriculo_id
            FROM curriculos GROUP BY candidato_id
        )
        SELECT c.id, c.nome_pseudonimo, c.status_curacao,
               MAX(p.pontuacao_final) AS score
        FROM candidatos c
        LEFT JOIN ultimo u ON u.candidato_id = c.id
        LEFT JOIN pontuacoes p
          ON p.candidato_id = c.id
         AND p.curriculo_versao_id = u.curriculo_id
        GROUP BY c.id
        ORDER BY c.id
        """
    ).fetchall()
    return pd.DataFrame(
        [dict(row) for row in rows],
        columns=["id", "nome_pseudonimo", "status_curacao", "score"],
    )


def _evidencia(con: sqlite3.Connection, tipo: str, fato_id: int) -> dict | None:
    row = con.execute(
        "SELECT trecho, pagina, nivel, confianca FROM evidencias"
        " WHERE tipo_fato = ? AND fato_id = ? ORDER BY id LIMIT 1",
        (tipo, fato_id),
    ).fetchone()
    return dict(row) if row else None


def _com_evidencia(
    con: sqlite3.Connection, tipo: str, rows: list[sqlite3.Row]
) -> list[dict]:
    itens = []
    for row in rows:
        item = dict(row)
        item["evidencia"] = _evidencia(con, tipo, int(row["id"]))
        itens.append(item)
    return itens


def carregar_perfil(con: sqlite3.Connection, candidato_id: int) -> dict | None:
    candidato = con.execute(
        "SELECT * FROM candidatos WHERE id = ?", (candidato_id,)
    ).fetchone()
    if candidato is None:
        return None
    curriculo = con.execute(
        "SELECT id, candidato_id, data_ingestao, n_paginas, versao,"
        " status_processamento FROM curriculos"
        " WHERE candidato_id = ? ORDER BY versao DESC, id DESC LIMIT 1",
        (candidato_id,),
    ).fetchone()
    if curriculo is None:
        return None

    pii = {
        row["tipo"]: row["c"]
        for row in con.execute(
            "SELECT tipo, COUNT(*) c FROM cofre_dados_pessoais"
            " WHERE candidato_id = ? GROUP BY tipo",
            (candidato_id,),
        )
    }
    scores = []
    for row in con.execute(
        "SELECT p.*, pa.sigla AS papel, pa.nome AS papel_nome"
        " FROM pontuacoes p JOIN papeis pa ON pa.id = p.papel_id"
        " WHERE p.candidato_id = ? AND p.curriculo_versao_id = ?"
        " ORDER BY p.pontuacao_final DESC",
        (candidato_id, int(curriculo["id"])),
    ):
        item = dict(row)
        item["detalhe"] = _json(row["detalhe_json"])
        scores.append(item)

    curriculos = [
        dict(row)
        for row in con.execute(
            "SELECT id, versao, data_ingestao, n_paginas, status_processamento"
            " FROM curriculos WHERE candidato_id = ?"
            " ORDER BY versao DESC, id DESC",
            (candidato_id,),
        )
    ]
    experiencias = _com_evidencia(
        con,
        "experiencia",
        con.execute(
            "SELECT * FROM experiencias WHERE curriculo_id = ? ORDER BY id DESC",
            (int(curriculo["id"]),),
        ).fetchall(),
    )
    competencias = _com_evidencia(
        con,
        "competencia",
        con.execute(
            "SELECT * FROM competencias WHERE curriculo_id = ?"
            " ORDER BY nome COLLATE NOCASE",
            (int(curriculo["id"]),),
        ).fetchall(),
    )
    certificacoes = _com_evidencia(
        con,
        "certificacao",
        con.execute(
            "SELECT * FROM certificacoes WHERE curriculo_id = ? ORDER BY id",
            (int(curriculo["id"]),),
        ).fetchall(),
    )
    formacoes = _com_evidencia(
        con,
        "formacao",
        con.execute(
            "SELECT * FROM formacoes WHERE curriculo_id = ? ORDER BY id",
            (int(curriculo["id"]),),
        ).fetchall(),
    )
    idiomas = _com_evidencia(
        con,
        "idioma",
        con.execute(
            "SELECT * FROM idiomas WHERE curriculo_id = ? ORDER BY id",
            (int(curriculo["id"]),),
        ).fetchall(),
    )
    contexto = carregar_contexto(con, int(curriculo["id"]))
    return {
        "candidato": dict(candidato),
        "curriculo": dict(curriculo),
        "curriculos": curriculos,
        "pii": pii,
        "scores": scores,
        "experiencias": experiencias,
        "competencias": competencias,
        "certificacoes": certificacoes,
        "formacoes": formacoes,
        "idiomas": idiomas,
        "contexto": contexto,
    }


def pool_elegivel(ranking: pd.DataFrame) -> dict[str, int]:
    if ranking.empty:
        return {}
    elegiveis = ranking[ranking["status_gates"] != "eliminado"]
    return {
        str(papel): int(quantidade)
        for papel, quantidade in elegiveis.groupby("papel").size().items()
    }


def slots_da_disputa(con: sqlite3.Connection, disputa_id: int) -> list[dict]:
    from src.scoring.time import carregar_alocacoes, listar_vagas

    vagas = listar_vagas(con)
    alocacoes = carregar_alocacoes(con, disputa_id)
    por_papel: dict[str, list[dict]] = {}
    for alocacao in alocacoes:
        por_papel.setdefault(alocacao["papel"], []).append(alocacao)
    for vaga in vagas:
        usadas = por_papel.get(vaga["papel"], [])
        indice = vaga["vaga"] - 1
        vaga["alocacao"] = usadas[indice] if indice < len(usadas) else None
    return vagas


def anotar_contexto(contexto: ContextoCandidato) -> dict:
    """Resumo factual do contexto (sem PII) para a ficha."""
    return {
        "anos_experiencia": contexto.anos_experiencia,
        "competencias": len(contexto.competencias),
        "experiencias": len(contexto.experiencias),
        "certificacoes": len(contexto.certificacoes),
        "formacoes": len(contexto.formacoes),
        "idiomas": len(contexto.idiomas),
    }
