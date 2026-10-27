"""Fase 3 — calcula e persiste as pontuações da disputa demo.

Pré-requisitos: banco com schema + preset publicado + currículos ingeridos.

Uso:
  python scripts/score.py --db data/demo.db
  python scripts/score.py --db data/demo.db --limite 20
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# bootstrap: rodar via `python scripts/score.py` ou importado nos testes
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.governance.auditoria import registrar
from src.ingestion.db import conectar
from src.scoring.catalogo import CATALOGO
from src.scoring.contexto import carregar_contexto
from src.scoring.engine import pontuar
from src.scoring.preset import carregar_versao

NOME_DISPUTA_PADRAO = "Demo Case B - Energia"


def obter_ou_criar_disputa(con, versao_preset_id: int, nome: str) -> int:
    row = con.execute(
        "SELECT id FROM disputas WHERE nome_ficticio = ? AND preset_versao_id = ?",
        (nome, versao_preset_id),
    ).fetchone()
    if row:
        return int(row["id"])
    cur = con.execute(
        "INSERT INTO disputas (nome_ficticio, cliente_ficticio, preset_versao_id, status)"
        " VALUES (?, 'Utility Ficticia S.A.', ?, 'aberta')",
        (nome, versao_preset_id),
    )
    registrar(
        con,
        acao="criar_disputa",
        ator="score",
        entidade="disputas",
        entidade_id=cur.lastrowid,
        detalhes={"versao_preset_id": versao_preset_id},
    )
    return int(cur.lastrowid)


def candidatos_com_ultimo_curriculo(con, limite: int | None = None) -> list:
    sql = """
        SELECT c.id AS candidato_id, cur.id AS curriculo_id
        FROM candidatos c
        JOIN curriculos cur ON cur.candidato_id = c.id
        WHERE cur.id = (
            SELECT MAX(id) FROM curriculos WHERE candidato_id = c.id
        )
        ORDER BY c.id
    """
    if limite is not None:
        sql += " LIMIT ?"
        return con.execute(sql, (limite,)).fetchall()
    return con.execute(sql).fetchall()


def upsert_pontuacao(con, dados: dict) -> None:
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


def _top_por_papel(con, disputa_id: int, papel_id: int, n: int = 3) -> list[dict]:
    rows = con.execute(
        """
        SELECT c.nome_pseudonimo, p.pontuacao_final, p.confianca, p.detalhe_json
        FROM pontuacoes p
        JOIN candidatos c ON c.id = p.candidato_id
        WHERE p.disputa_id = ? AND p.papel_id = ?
        ORDER BY p.pontuacao_final DESC
        """,
        (disputa_id, papel_id),
    ).fetchall()
    elegiveis = []
    for row in rows:
        detalhe = json.loads(row["detalhe_json"])
        if detalhe.get("status_gates") == "eliminado":
            continue
        elegiveis.append(
            {
                "nome": row["nome_pseudonimo"],
                "score": row["pontuacao_final"],
                "confianca": row["confianca"],
                "status": detalhe.get("status_gates"),
            }
        )
        if len(elegiveis) >= n:
            break
    return elegiveis


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/demo.db")
    parser.add_argument("--disputa", default=NOME_DISPUTA_PADRAO)
    parser.add_argument("--versao", type=int, default=None)
    parser.add_argument("--limite", type=int, default=None)
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"erro: banco nao encontrado em {args.db}")
        print("rode antes: build_db.py e ingest.py")
        return 2

    con = conectar(args.db)
    try:
        preset = carregar_versao(con, args.versao)
        papeis = {
            row["sigla"]: int(row["id"])
            for row in con.execute("SELECT id, sigla FROM papeis")
        }
        catalogos = [CATALOGO[sigla] for sigla in papeis if sigla in CATALOGO]
        if not catalogos:
            print("erro: nenhum papel do catálogo encontrado no banco")
            return 2

        disputa_id = obter_ou_criar_disputa(con, preset.versao_preset_id, args.disputa)
        candidatos = candidatos_com_ultimo_curriculo(con, args.limite)

        resumo = {
            sigla: {"total": 0, "elegivel": 0, "revisar": 0, "eliminado": 0}
            for sigla in papeis
        }
        gravadas = 0
        for candidato in candidatos:
            contexto = carregar_contexto(con, int(candidato["curriculo_id"]))
            for perfil in catalogos:
                resultado = pontuar(
                    contexto, perfil, preset.pesos, preset.gates
                )
                upsert_pontuacao(
                    con,
                    {
                        "disputa_id": disputa_id,
                        "candidato_id": contexto.candidato_id,
                        "papel_id": papeis[perfil.sigla],
                        "versao_preset_id": preset.versao_preset_id,
                        "curriculo_versao_id": contexto.curriculo_id,
                        "pontuacao_ponderada": resultado.pontuacao_ponderada,
                        "confianca": resultado.confianca,
                        "pontuacao_final": resultado.pontuacao_final,
                        "detalhe": resultado.detalhe,
                    },
                )
                resumo[perfil.sigla]["total"] += 1
                resumo[perfil.sigla][resultado.status_gates] += 1
                gravadas += 1
        registrar(
            con,
            acao="calcular_pontuacoes",
            ator="score",
            entidade="disputas",
            entidade_id=disputa_id,
            detalhes={
                "versao_preset_id": preset.versao_preset_id,
                "pontuacoes": gravadas,
                "por_papel": resumo,
            },
        )
        con.commit()

        print(
            f"disputa: {args.disputa} (id {disputa_id})"
            f" | preset v{preset.numero} ({preset.status})"
            f" | candidatos: {len(candidatos)}"
        )
        print(f"pontuacoes gravadas: {gravadas}")
        for perfil in catalogos:
            dados = resumo[perfil.sigla]
            print(
                f"  {perfil.sigla}: {dados['total']} "
                f"(elegivel {dados['elegivel']}, revisar {dados['revisar']}, "
                f"eliminado {dados['eliminado']})"
            )
            for item in _top_por_papel(con, disputa_id, papeis[perfil.sigla]):
                print(
                    f"    top: {item['nome']} {item['score']:.2f} "
                    f"(conf {item['confianca']:.1f}, {item['status']})"
                )
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
