"""Fase 5 — preview por papel, sem gravar no banco.

preview_papel(con, disputa_id, versao_base_id, papel, pesos, gates, limite)
  -> dict com 'resumo' (antes/depois/entraram/sairam/sem_score) e 'linhas' (top-N).

O 'antes' vem das pontuacoes persistidas da versao base; o 'depois' aplica os
pesos/gates do rascunho. Nada e gravado no banco.
"""
from __future__ import annotations

import json

from src.scoring.catalogo import CATALOGO
from src.scoring.contexto import carregar_contexto
from src.scoring.engine import pontuar

STATUS_VALIDOS = ("elegivel", "revisar", "eliminado")


def _status(valor: str | None) -> str:
    return valor if valor in STATUS_VALIDOS else "revisar"


def preview_papel(
    con,
    disputa_id: int,
    versao_base_id: int,
    papel: str,
    pesos: dict,
    gates: dict,
    limite: int = 10,
) -> dict:
    """Gera preview de top-N candidatos para o papel, comparando versao base vs rascunho."""
    perfil = CATALOGO.get(papel)
    if perfil is None:
        raise ValueError(f"Papel '{papel}' nao encontrado no CATALOGO")

    # Pool: todos os candidatos com o curriculo mais recente, igual ao
    # recalcular_disputa. Inclui quem ainda nao tem score na versao base.
    candidatos = con.execute(
        """
        WITH ultimo AS (
            SELECT candidato_id, MAX(id) AS curriculo_id
            FROM curriculos
            GROUP BY candidato_id
        )
        SELECT c.id AS candidato_id, u.curriculo_id, c.nome_pseudonimo
        FROM candidatos c
        JOIN ultimo u ON u.candidato_id = c.id
        ORDER BY c.id
        """
    ).fetchall()

    # 'Antes': score persistido da versao base, apenas do curriculo mais
    # recente (scores antigos ficam no banco por reprodutibilidade).
    base_rows = con.execute(
        """
        WITH ultimo AS (
            SELECT candidato_id, MAX(id) AS curriculo_id
            FROM curriculos
            GROUP BY candidato_id
        )
        SELECT p.candidato_id, p.pontuacao_final, p.detalhe_json
        FROM pontuacoes p
        JOIN papeis pa ON pa.id = p.papel_id
        JOIN ultimo u
          ON u.candidato_id = p.candidato_id
         AND u.curriculo_id = p.curriculo_versao_id
        WHERE p.disputa_id = ? AND p.versao_preset_id = ? AND pa.sigla = ?
        """,
        (disputa_id, versao_base_id, papel),
    ).fetchall()

    base_by_id: dict[int, tuple[float, dict]] = {}
    for row in base_rows:
        if row["pontuacao_final"] is None:
            continue
        cid = int(row["candidato_id"])
        try:
            detalhe = json.loads(row["detalhe_json"]) if row["detalhe_json"] else {}
        except (TypeError, ValueError):
            detalhe = {}
        if not isinstance(detalhe, dict):
            detalhe = {}
        base_by_id[cid] = (float(row["pontuacao_final"]), detalhe)

    # 'Depois': recalcula todos os candidatos do pool com o rascunho.
    linhas_todas: list[dict] = []
    contagens_depois = {status: 0 for status in STATUS_VALIDOS}
    for cand in candidatos:
        cid = int(cand["candidato_id"])
        score_antes, detalhe_antes = base_by_id.get(cid, (None, {}))
        contexto = carregar_contexto(con, int(cand["curriculo_id"]))
        resultado = pontuar(contexto, perfil, pesos, gates)
        status_novo = _status(resultado.status_gates)
        contagens_depois[status_novo] += 1

        novo = score_antes is None
        linhas_todas.append(
            {
                "candidato_id": cid,
                "nome": cand["nome_pseudonimo"],
                "score_atual": score_antes,
                "score_novo": round(float(resultado.pontuacao_final), 2),
                "delta": (
                    None
                    if score_antes is None
                    else round(float(resultado.pontuacao_final) - score_antes, 2)
                ),
                "status_atual": "revisar"
                if novo
                else _status(detalhe_antes.get("status_gates")),
                "status_novo": status_novo,
                "novo": novo,
            }
        )

    # 'Antes' contagens: todos os candidatos com score na versao base.
    contagens_antes = {status: 0 for status in STATUS_VALIDOS}
    for _cid, (_score, detalhe) in base_by_id.items():
        contagens_antes[_status(detalhe.get("status_gates"))] += 1

    # Top N antes/depois por score desc, desempate por candidato_id.
    top_antes = sorted(
        ((cid, score) for cid, (score, _detalhe) in base_by_id.items()),
        key=lambda item: (-item[1], item[0]),
    )[:limite]
    top_antes_ids = {cid for cid, _score in top_antes}

    linhas_todas.sort(key=lambda r: (-r["score_novo"], r["candidato_id"]))
    linhas = linhas_todas[:limite]
    top_depois_ids = {r["candidato_id"] for r in linhas}

    nomes = {r["candidato_id"]: r["nome"] for r in linhas_todas}
    entraram = [
        r["nome"] for r in linhas if r["candidato_id"] not in top_antes_ids
    ]
    sairam = [
        nomes[cid] for cid, _score in top_antes if cid not in top_depois_ids
    ]

    return {
        "resumo": {
            "antes": contagens_antes,
            "depois": contagens_depois,
            "entraram": entraram,
            "sairam": sairam,
            "sem_score": sum(
                1
                for cand in candidatos
                if int(cand["candidato_id"]) not in base_by_id
            ),
        },
        "linhas": linhas,
    }