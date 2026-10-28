"""Fase 4 — score do time e alocação.

    ScoreTime = média das pontuações alocadas
                + bônus de cobertura total
                - penalidades (fonte única, confiança baixa,
                  lacuna de domínio, currículo antigo)

O score do time é uma leitura da decisão, não uma nota escondida: cada
penalidade e cada risco vem com o papel/candidato que o gerou.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from src.governance.auditoria import registrar

VAGAS_PADRAO = {"pm_scrum": 1, "ba_bsa": 1, "java": 2, "bi_data": 1}
PAPEIS_COM_DOMINIO = {"pm_scrum", "ba_bsa"}


@dataclass
class SlotAlocado:
    papel: str
    papel_nome: str
    vaga: int
    candidato_id: int
    candidato_nome: str
    score: float
    confianca: float
    dominio_energia: float
    dias_ingestao: int
    status_gates: str
    ancoras: list[str] = field(default_factory=list)


@dataclass
class ScoreTime:
    media: float
    bonus_cobertura: float
    penalidades: dict[str, float]
    score_final: float
    total_vagas: int
    preenchidas: int
    confianca_media: float
    riscos: list[dict] = field(default_factory=list)

    def como_dict(self) -> dict:
        return {
            "media": self.media,
            "bonus_cobertura": self.bonus_cobertura,
            "penalidades": self.penalidades,
            "score_final": self.score_final,
            "total_vagas": self.total_vagas,
            "preenchidas": self.preenchidas,
            "confianca_media": self.confianca_media,
            "riscos": self.riscos,
        }


def calcular_score_time(
    slots: list[SlotAlocado],
    vagas: dict[str, int],
    pool_elegivel: dict[str, int],
) -> ScoreTime:
    total_vagas = sum(max(0, int(v)) for v in vagas.values())
    preenchidas = len([s for s in slots if s.candidato_id])
    cobertura = preenchidas / total_vagas if total_vagas else 0.0
    media = (
        sum(float(s.score) for s in slots) / preenchidas if preenchidas else 0.0
    )
    bonus = round(5.0 * cobertura, 2)
    confianca_media = (
        sum(float(s.confianca) for s in slots) / preenchidas if preenchidas else 0.0
    )

    pen_fonte = min(
        10.0,
        5.0
        * sum(
            1
            for papel, quantidade in vagas.items()
            if quantidade > 0 and pool_elegivel.get(papel, 0) <= 1
        ),
    )
    pen_confianca = 5.0 if confianca_media < 30 else (2.5 if confianca_media < 40 else 0.0)
    lacunas = [
        s
        for s in slots
        if s.papel in PAPEIS_COM_DOMINIO and s.dominio_energia <= 0
    ]
    pen_dominio = min(5.0, 2.5 * len(lacunas))
    antigos = [s for s in slots if s.dias_ingestao > 365]
    pen_antigo = min(5.0, 2.5 * len(antigos))
    penalidades = {
        "fonte_unica": round(pen_fonte, 2),
        "confianca": round(pen_confianca, 2),
        "lacuna_dominio": round(pen_dominio, 2),
        "curriculo_antigo": round(pen_antigo, 2),
    }
    final = media + bonus - sum(penalidades.values())
    final = round(max(0.0, min(100.0, final)), 2)

    riscos: list[dict] = []
    for papel, quantidade in vagas.items():
        if quantidade > 0 and pool_elegivel.get(papel, 0) <= 1:
            riscos.append(
                {
                    "tipo": "fonte_unica",
                    "papel": papel,
                    "mensagem": (
                        f"Fonte única em {papel}: "
                        f"{pool_elegivel.get(papel, 0)} candidato(s) elegível(is)."
                    ),
                }
            )
    for slot in slots:
        if slot.confianca < 30:
            riscos.append(
                {
                    "tipo": "confianca_baixa",
                    "papel": slot.papel,
                    "candidato_id": slot.candidato_id,
                    "mensagem": (
                        f"Confiança {slot.confianca:.1f} abaixo de 30: "
                        f"{slot.candidato_nome}."
                    ),
                }
            )
        if slot.papel in PAPEIS_COM_DOMINIO and slot.dominio_energia <= 0:
            riscos.append(
                {
                    "tipo": "lacuna_dominio",
                    "papel": slot.papel,
                    "candidato_id": slot.candidato_id,
                    "mensagem": (
                        f"Sem domínio de energia no perfil: {slot.candidato_nome}."
                    ),
                }
            )
        if slot.dias_ingestao > 365:
            riscos.append(
                {
                    "tipo": "curriculo_antigo",
                    "papel": slot.papel,
                    "candidato_id": slot.candidato_id,
                    "mensagem": (
                        f"Currículo com {slot.dias_ingestao} dias: "
                        f"{slot.candidato_nome}."
                    ),
                }
            )
        if slot.status_gates == "eliminado":
            riscos.append(
                {
                    "tipo": "candidato_eliminado",
                    "papel": slot.papel,
                    "candidato_id": slot.candidato_id,
                    "mensagem": (
                        f"Candidato alocado está eliminado nos gates: "
                        f"{slot.candidato_nome}."
                    ),
                }
            )
    if preenchidas < total_vagas:
        riscos.append(
            {
                "tipo": "cobertura_incompleta",
                "mensagem": (
                    f"Cobertura {preenchidas}/{total_vagas} vagas — "
                    "time incompleto para a proposta."
                ),
            }
        )

    return ScoreTime(
        media=round(media, 2),
        bonus_cobertura=bonus,
        penalidades=penalidades,
        score_final=final,
        total_vagas=total_vagas,
        preenchidas=preenchidas,
        confianca_media=round(confianca_media, 2),
        riscos=riscos,
    )


def listar_vagas(con) -> list[dict]:
    """Lê as vagas do preset a partir de `papeis.requisitos_json`."""
    vagas: list[dict] = []
    for row in con.execute(
        "SELECT id, nome, sigla, requisitos_json FROM papeis ORDER BY id"
    ):
        requisitos = json.loads(row["requisitos_json"] or "{}")
        quantidade = int(requisitos.get("vagas", VAGAS_PADRAO.get(row["sigla"], 0)))
        for vaga in range(1, quantidade + 1):
            vagas.append(
                {
                    "papel_id": int(row["id"]),
                    "papel": row["sigla"],
                    "papel_nome": row["nome"],
                    "vaga": vaga,
                    "quantidade": quantidade,
                }
            )
    return vagas


def carregar_alocacoes(con, disputa_id: int) -> list[dict]:
    return [
        dict(row)
        for row in con.execute(
            "SELECT a.*, p.sigla AS papel, p.nome AS papel_nome"
            " FROM alocacoes a JOIN papeis p ON p.id = a.papel_id"
            " WHERE a.disputa_id = ? AND a.status = 'alocado'"
            " ORDER BY a.papel_id, a.id",
            (disputa_id,),
        )
    ]


def _eliminados_na_selecao(con, disputa_id: int, selecoes: list[dict]) -> list[int]:
    """Candidatos cujo score atual no papel está eliminado pelos gates."""
    eliminados: list[int] = []
    for selecao in selecoes:
        candidato_id = int(selecao["candidato_id"])
        papel_id = int(selecao["papel_id"])
        row = con.execute(
            """
            SELECT p.detalhe_json
            FROM pontuacoes p
            WHERE p.disputa_id = ?
              AND p.candidato_id = ?
              AND p.papel_id = ?
              AND p.curriculo_versao_id = (
                  SELECT MAX(id) FROM curriculos WHERE candidato_id = ?
              )
            ORDER BY p.id DESC
            LIMIT 1
            """,
            (disputa_id, candidato_id, papel_id, candidato_id),
        ).fetchone()
        if row is None or not row["detalhe_json"]:
            continue
        try:
            status = json.loads(row["detalhe_json"]).get("status_gates")
        except (TypeError, ValueError):
            status = None
        if status == "eliminado":
            eliminados.append(candidato_id)
    return eliminados


def salvar_alocacao(
    con,
    disputa_id: int,
    selecoes: list[dict],
    ator: str = "demo",
) -> dict:
    """Substitui a alocação ativa da disputa.

    `selecoes`: lista de `{papel_id, candidato_id}` na ordem das vagas.
    Um candidato só pode ocupar uma vaga. Candidato eliminado pelos gates
    não pode ser alocado silenciosamente.
    """
    candidatos = [int(s["candidato_id"]) for s in selecoes if s.get("candidato_id")]
    if len(candidatos) != len(set(candidatos)):
        raise ValueError("um candidato não pode ocupar duas vagas")
    if len(candidatos) != len(selecoes):
        raise ValueError("todas as vagas precisam de um candidato")

    disputa = con.execute(
        "SELECT edital_versao_id FROM disputas WHERE id = ?", (disputa_id,)
    ).fetchone()
    if disputa is not None and disputa["edital_versao_id"] is not None:
        from src.scoring.composicao import validar_selecao_demanda

        validar_selecao_demanda(con, disputa_id, selecoes)
    else:
        eliminados = _eliminados_na_selecao(con, disputa_id, selecoes)
        if eliminados:
            raise ValueError(
                "candidato(s) eliminado(s) pelos gates não pode(m) ser alocado(s): "
                + ", ".join(str(cid) for cid in sorted(set(eliminados)))
            )

    anteriores = {
        int(row["candidato_id"])
        for row in con.execute(
            "SELECT candidato_id FROM alocacoes"
            " WHERE disputa_id = ? AND status = 'alocado'",
            (disputa_id,),
        )
    }
    con.execute("DELETE FROM alocacoes WHERE disputa_id = ?", (disputa_id,))
    for selecao in selecoes:
        con.execute(
            "INSERT INTO alocacoes (disputa_id, papel_id, candidato_id, status)"
            " VALUES (?, ?, ?, 'alocado')",
            (disputa_id, int(selecao["papel_id"]), int(selecao["candidato_id"])),
        )

    for candidato_id in candidatos:
        con.execute(
            "UPDATE candidatos SET status_curacao = 'alocado' WHERE id = ?",
            (candidato_id,),
        )
    removidos = anteriores - set(candidatos)
    for candidato_id in removidos:
        con.execute(
            "UPDATE candidatos SET status_curacao = 'curado' WHERE id = ?",
            (candidato_id,),
        )

    registrar(
        con,
        acao="salvar_alocacao",
        ator=ator,
        entidade="disputas",
        entidade_id=disputa_id,
        detalhes={
            "selecoes": [
                {
                    "papel_id": int(s["papel_id"]),
                    "candidato_id": int(s["candidato_id"]),
                }
                for s in selecoes
            ],
            "removidos": sorted(removidos),
        },
    )
    con.commit()
    return {
        "alocados": len(candidatos),
        "removidos": len(removidos),
    }
