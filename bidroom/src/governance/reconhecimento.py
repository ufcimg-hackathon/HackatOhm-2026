"""Etapa 7 — cultura, metas e reconhecimento.

Regras centrais:
- a conquista é a certificação concluída e validada no ano de referência;
- a data de conclusão manda no ano, não a data de envio do comprovante;
- reenvio do mesmo comprovante não cria outra conquista;
- rejeição, vencimento ou revogação retiram a conquista do ranking;
- o ranking cultural é apenas leitura e nunca altera elegibilidade ou nota.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from typing import Any

from src.governance.auditoria import registrar
from src.governance.comprovacao import estado_comprovacao, resumo_comprovacao
from src.governance.atualidade import resumo_candidato

ROTULOS_CONQUISTA = {
    "pendente": "Pendente",
    "validada": "Validada",
    "rejeitada": "Rejeitada",
    "revogada": "Revogada",
}
REGRA_PADRAO = {
    "contar": "certificacoes_distintas_validadas",
    "desempate": "data_conclusao_mais_recente",
    "pontos_por_certificacao": 1,
}


def _agora() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _json(texto: str | None) -> dict:
    if not texto:
        return {}
    try:
        valor = json.loads(texto)
    except (TypeError, ValueError):
        return {}
    return valor if isinstance(valor, dict) else {}


def _programa(con, programa_id: int) -> dict:
    row = con.execute(
        "SELECT * FROM programas_reconhecimento WHERE id = ?", (programa_id,)
    ).fetchone()
    if row is None:
        raise KeyError("programa de reconhecimento não encontrado")
    item = dict(row)
    item["regra"] = {**REGRA_PADRAO, **_json(row["regra_json"])}
    return item


def criar_programa(
    con: sqlite3.Connection,
    nome: str,
    ano: int,
    *,
    descricao: str = "",
    regra: dict | None = None,
    visibilidade: str = "agregado",
    ator: str = "ui",
) -> dict:
    if not nome.strip():
        raise ValueError("programa exige nome")
    if visibilidade not in ("agregado", "publico", "privado"):
        raise ValueError("visibilidade inválida")
    regra_final = {**REGRA_PADRAO, **(regra or {})}
    cur = con.execute(
        "INSERT INTO programas_reconhecimento"
        " (nome, descricao, ano, regra_json, visibilidade)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            nome.strip(),
            descricao.strip() or None,
            int(ano),
            json.dumps(regra_final, ensure_ascii=False, sort_keys=True),
            visibilidade,
        ),
    )
    programa_id = int(cur.lastrowid)
    registrar(
        con,
        acao="criar_programa_reconhecimento",
        ator=ator,
        entidade="programas_reconhecimento",
        entidade_id=programa_id,
        detalhes={"nome": nome, "ano": int(ano), "visibilidade": visibilidade},
        commit=False,
    )
    con.commit()
    return _programa(con, programa_id)


def listar_programas(con, *, somente_ativos: bool = False) -> list[dict]:
    where = " WHERE ativo = 1" if somente_ativos else ""
    rows = con.execute(
        "SELECT * FROM programas_reconhecimento" + where + " ORDER BY ano DESC, id DESC"
    ).fetchall()
    resultado = []
    for row in rows:
        item = dict(row)
        item["regra"] = {**REGRA_PADRAO, **_json(row["regra_json"])}
        resultado.append(item)
    return resultado


def criar_meta(
    con: sqlite3.Connection,
    candidato_id: int,
    *,
    descricao: str,
    programa_id: int | None = None,
    certificacao_alvo: str | None = None,
    prazo: str | None = None,
    ator: str = "ui",
) -> int:
    if not descricao.strip():
        raise ValueError("meta exige descrição")
    if programa_id is not None:
        _programa(con, programa_id)
    row = con.execute("SELECT id FROM candidatos WHERE id = ?", (candidato_id,)).fetchone()
    if row is None:
        raise KeyError("candidato não encontrado")
    cur = con.execute(
        "INSERT INTO metas_desenvolvimento"
        " (candidato_id, programa_id, descricao, certificacao_alvo, prazo)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            int(candidato_id),
            programa_id,
            descricao.strip(),
            certificacao_alvo.strip() if certificacao_alvo else None,
            prazo,
        ),
    )
    meta_id = int(cur.lastrowid)
    registrar(
        con,
        acao="criar_meta_desenvolvimento",
        ator=ator,
        entidade="metas_desenvolvimento",
        entidade_id=meta_id,
        detalhes={"candidato_id": int(candidato_id), "descricao": descricao},
        commit=False,
    )
    con.commit()
    return meta_id


def listar_metas(
    con,
    *,
    candidato_id: int | None = None,
    programa_id: int | None = None,
) -> list[dict]:
    where = ["1=1"]
    params: list[Any] = []
    if candidato_id is not None:
        where.append("m.candidato_id = ?")
        params.append(int(candidato_id))
    if programa_id is not None:
        where.append("m.programa_id = ?")
        params.append(int(programa_id))
    rows = con.execute(
        "SELECT m.*, c.nome_pseudonimo, p.nome AS programa_nome"
        " FROM metas_desenvolvimento m"
        " JOIN candidatos c ON c.id = m.candidato_id"
        " LEFT JOIN programas_reconhecimento p ON p.id = m.programa_id"
        " WHERE " + " AND ".join(where) + " ORDER BY m.id DESC",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def atualizar_meta(
    con: sqlite3.Connection,
    meta_id: int,
    status: str,
    *,
    ator: str = "ui",
) -> dict:
    if status not in ("pendente", "concluida", "cancelada"):
        raise ValueError("status de meta inválido")
    row = con.execute(
        "SELECT * FROM metas_desenvolvimento WHERE id = ?", (meta_id,)
    ).fetchone()
    if row is None:
        raise KeyError("meta não encontrada")
    concluido = _agora() if status == "concluida" else None
    con.execute(
        "UPDATE metas_desenvolvimento SET status = ?, concluido_em = ?"
        " WHERE id = ?",
        (status, concluido, meta_id),
    )
    registrar(
        con,
        acao="atualizar_meta_desenvolvimento",
        ator=ator,
        entidade="metas_desenvolvimento",
        entidade_id=meta_id,
        detalhes={"status": status},
        commit=False,
    )
    con.commit()
    return {"id": int(meta_id), "status": status, "concluido_em": concluido}


def _certificacao(con, certificacao_id: int) -> dict:
    row = con.execute(
        "SELECT f.*, cur.candidato_id, c.nome_pseudonimo"
        " FROM certificacoes f"
        " JOIN curriculos cur ON cur.id = f.curriculo_id"
        " JOIN candidatos c ON c.id = cur.candidato_id"
        " WHERE f.id = ?",
        (certificacao_id,),
    ).fetchone()
    if row is None:
        raise KeyError("certificação não encontrada")
    return dict(row)


def _data_conclusao(certificacao: dict) -> str | None:
    texto = certificacao.get("data_conclusao")
    if texto:
        try:
            return date.fromisoformat(str(texto)[:10]).isoformat()
        except ValueError:
            pass
    ano = certificacao.get("ano")
    if ano is not None:
        # O currículo antigo só tem ano; usamos 31/12 como limite conservador
        # para desempate, sem inventar um dia/mês que não existe na fonte.
        return f"{int(ano):04d}-12-31"
    return None


def _ano_referencia(certificacao: dict) -> int | None:
    data = _data_conclusao(certificacao)
    if data:
        return int(data[:4])
    return None


def _evidencia_valida(con, certificacao_id: int) -> int | None:
    row = con.execute(
        "SELECT id FROM evidencias_documentais"
        " WHERE tipo_fato = 'certificacao' AND fato_id = ?"
        " AND status = 'validado'"
        " AND (valido_ate IS NULL OR date(valido_ate) >= date('now'))"
        " ORDER BY id DESC LIMIT 1",
        (certificacao_id,),
    ).fetchone()
    return int(row["id"]) if row else None


def _conquista_existente(
    con, candidato_id: int, certificacao_id: int, programa_id: int, ano: int
) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM conquistas WHERE candidato_id = ?"
        " AND certificacao_id = ? AND programa_id = ? AND ano_referencia = ?",
        (candidato_id, certificacao_id, programa_id, ano),
    ).fetchone()


def sincronizar_conquistas(
    con: sqlite3.Connection,
    programa_id: int,
    *,
    ator: str = "sistema",
) -> dict:
    """Cria/atualiza conquistas a partir de certificações e provas válidas.

    A operação é idempotente. Conquista revogada ou rejeitada não é
    reativada automaticamente; uma decisão humana explícita é necessária.
    """
    programa = _programa(con, programa_id)
    ano_programa = int(programa["ano"])
    certificacoes = con.execute(
        "SELECT f.*, cur.candidato_id, c.nome_pseudonimo"
        " FROM certificacoes f"
        " JOIN curriculos cur ON cur.id = f.curriculo_id"
        " JOIN candidatos c ON c.id = cur.candidato_id"
        " ORDER BY f.id"
    ).fetchall()
    criadas = atualizadas = ignoradas = 0
    for row in certificacoes:
        cert = dict(row)
        ano = _ano_referencia(cert)
        if ano != ano_programa:
            continue
        existente = _conquista_existente(
            con, int(cert["candidato_id"]), int(cert["id"]), programa_id, ano
        )
        estado = estado_comprovacao(con, "certificacao", int(cert["id"]))
        evidencia_id = _evidencia_valida(con, int(cert["id"]))
        if existente is not None:
            if existente["status"] in ("revogada", "rejeitada"):
                ignoradas += 1
                continue
            if estado == "validado" and evidencia_id is not None:
                if (
                    existente["status"] != "validada"
                    or existente["evidencia_id"] != evidencia_id
                ):
                    con.execute(
                        "UPDATE conquistas SET status = 'validada',"
                        " evidencia_id = ?, validado_em = ?, motivo = NULL"
                        " WHERE id = ?",
                        (evidencia_id, _agora(), int(existente["id"])),
                    )
                    atualizadas += 1
            elif existente["status"] == "validada":
                con.execute(
                    "UPDATE conquistas SET status = 'pendente',"
                    " evidencia_id = NULL, validado_em = NULL,"
                    " motivo = 'comprovação não está válida' WHERE id = ?",
                    (int(existente["id"]),),
                )
                atualizadas += 1
            continue
        if estado == "validado" and evidencia_id is not None:
            con.execute(
                "INSERT INTO conquistas"
                " (candidato_id, certificacao_id, programa_id, ano_referencia,"
                " data_conclusao, status, evidencia_id, validado_em)"
                " VALUES (?, ?, ?, ?, ?, 'validada', ?, ?)",
                (
                    int(cert["candidato_id"]),
                    int(cert["id"]),
                    programa_id,
                    ano,
                    _data_conclusao(cert),
                    evidencia_id,
                    _agora(),
                ),
            )
            criadas += 1
    registrar(
        con,
        acao="sincronizar_conquistas",
        ator=ator,
        entidade="programas_reconhecimento",
        entidade_id=programa_id,
        detalhes={
            "ano": ano_programa,
            "criadas": criadas,
            "atualizadas": atualizadas,
            "ignoradas": ignoradas,
        },
        commit=False,
    )
    con.commit()
    return {
        "programa_id": programa_id,
        "ano": ano_programa,
        "criadas": criadas,
        "atualizadas": atualizadas,
        "ignoradas": ignoradas,
    }


def listar_conquistas(
    con,
    *,
    candidato_id: int | None = None,
    programa_id: int | None = None,
) -> list[dict]:
    where = ["1=1"]
    params: list[Any] = []
    if candidato_id is not None:
        where.append("q.candidato_id = ?")
        params.append(int(candidato_id))
    if programa_id is not None:
        where.append("q.programa_id = ?")
        params.append(int(programa_id))
    rows = con.execute(
        "SELECT q.*, f.nome AS certificacao, f.ano, f.data_conclusao AS cert_data,"
        " c.nome_pseudonimo, p.nome AS programa_nome"
        " FROM conquistas q"
        " JOIN certificacoes f ON f.id = q.certificacao_id"
        " JOIN candidatos c ON c.id = q.candidato_id"
        " JOIN programas_reconhecimento p ON p.id = q.programa_id"
        " WHERE " + " AND ".join(where) + " ORDER BY q.id DESC",
        params,
    ).fetchall()
    resultado = []
    for row in rows:
        item = dict(row)
        item["estado_evidencia"] = estado_comprovacao(
            con, "certificacao", int(row["certificacao_id"])
        )
        resultado.append(item)
    return resultado


def validar_conquista(
    con: sqlite3.Connection,
    conquista_id: int,
    *,
    revisor: str,
    ator: str = "ui",
) -> dict:
    row = con.execute(
        "SELECT * FROM conquistas WHERE id = ?", (conquista_id,)
    ).fetchone()
    if row is None:
        raise KeyError("conquista não encontrada")
    if row["status"] == "revogada":
        raise ValueError("conquista revogada exige decisão explícita de reabertura")
    evidencia_id = _evidencia_valida(con, int(row["certificacao_id"]))
    if evidencia_id is None:
        raise ValueError("conquista exige comprovação válida")
    con.execute(
        "UPDATE conquistas SET status = 'validada', evidencia_id = ?,"
        " validado_em = ?, motivo = NULL WHERE id = ?",
        (evidencia_id, _agora(), conquista_id),
    )
    registrar(
        con,
        acao="validar_conquista",
        ator=ator,
        entidade="conquistas",
        entidade_id=conquista_id,
        detalhes={"revisor": revisor, "evidencia_id": evidencia_id},
        commit=False,
    )
    con.commit()
    return {"id": int(conquista_id), "status": "validada"}


def rejeitar_conquista(
    con: sqlite3.Connection,
    conquista_id: int,
    *,
    motivo: str,
    ator: str = "ui",
) -> dict:
    if not motivo.strip():
        raise ValueError("rejeição exige motivo")
    row = con.execute(
        "SELECT id FROM conquistas WHERE id = ?", (conquista_id,)
    ).fetchone()
    if row is None:
        raise KeyError("conquista não encontrada")
    con.execute(
        "UPDATE conquistas SET status = 'rejeitada', motivo = ? WHERE id = ?",
        (motivo.strip(), conquista_id),
    )
    registrar(
        con,
        acao="rejeitar_conquista",
        ator=ator,
        entidade="conquistas",
        entidade_id=conquista_id,
        detalhes={"motivo": motivo},
        commit=False,
    )
    con.commit()
    return {"id": int(conquista_id), "status": "rejeitada"}


def revogar_conquista(
    con: sqlite3.Connection,
    conquista_id: int,
    *,
    motivo: str,
    ator: str = "ui",
) -> dict:
    if not motivo.strip():
        raise ValueError("revogação exige motivo")
    row = con.execute(
        "SELECT id FROM conquistas WHERE id = ?", (conquista_id,)
    ).fetchone()
    if row is None:
        raise KeyError("conquista não encontrada")
    con.execute(
        "UPDATE conquistas SET status = 'revogada', motivo = ? WHERE id = ?",
        (motivo.strip(), conquista_id),
    )
    registrar(
        con,
        acao="revogar_conquista",
        ator=ator,
        entidade="conquistas",
        entidade_id=conquista_id,
        detalhes={"motivo": motivo},
        commit=False,
    )
    con.commit()
    return {"id": int(conquista_id), "status": "revogada"}


def _ordem_desempate(item: dict, regra: dict) -> tuple:
    nome = item["nome"]
    total = item["total"]
    ultima = item["ultima_conclusao"]
    try:
        ordinal = date.fromisoformat(ultima).toordinal() if ultima else 0
    except ValueError:
        ordinal = 0
    desempate = regra.get("desempate", "data_conclusao_mais_recente")
    if desempate == "nome":
        return (-total, nome)
    if desempate == "certificacoes":
        return (-total, -len(item["certificacoes"]), nome)
    return (-total, -ordinal, nome)


def ranking_cultural(
    con: sqlite3.Connection,
    programa_id: int,
    *,
    ano: int | None = None,
    hoje: date | None = None,
) -> list[dict]:
    """Ranking anual somente leitura; não altera notas nem elegibilidade."""
    programa = _programa(con, programa_id)
    referencia = int(ano or programa["ano"])
    rows = con.execute(
        "SELECT q.*, f.nome AS certificacao, f.data_conclusao AS cert_data,"
        " f.ano AS cert_ano, c.nome_pseudonimo"
        " FROM conquistas q"
        " JOIN certificacoes f ON f.id = q.certificacao_id"
        " JOIN candidatos c ON c.id = q.candidato_id"
        " WHERE q.programa_id = ? AND q.ano_referencia = ?"
        " AND q.status = 'validada' ORDER BY q.id",
        (programa_id, referencia),
    ).fetchall()
    por_candidato: dict[int, dict] = {}
    for row in rows:
        item = dict(row)
        if estado_comprovacao(
            con, "certificacao", int(item["certificacao_id"]), hoje
        ) != "validado":
            continue
        candidato_id = int(item["candidato_id"])
        atual = por_candidato.setdefault(
            candidato_id,
            {
                "candidato_id": candidato_id,
                "nome": item["nome_pseudonimo"],
                "total": 0,
                "certificacoes": [],
                "_certificacao_ids": set(),
                "ultima_conclusao": None,
            },
        )
        certificacao_id = int(item["certificacao_id"])
        if certificacao_id not in atual["_certificacao_ids"]:
            atual["_certificacao_ids"].add(certificacao_id)
            atual["certificacoes"].append(item["certificacao"])
            atual["total"] += int(
                programa["regra"].get("pontos_por_certificacao", 1)
            )
        data = item.get("data_conclusao") or item.get("cert_data")
        if data:
            data = str(data)[:10]
            if atual["ultima_conclusao"] is None or data > atual["ultima_conclusao"]:
                atual["ultima_conclusao"] = data
    itens = list(por_candidato.values())
    for item in itens:
        item.pop("_certificacao_ids", None)
    itens.sort(key=lambda item: _ordem_desempate(item, programa["regra"]))
    for posicao, item in enumerate(itens, start=1):
        item["posicao"] = posicao
        item["regra_desempate"] = programa["regra"].get("desempate")
        item["visibilidade"] = programa["visibilidade"]
    return itens


def resumo_participacao(
    con,
    candidato_id: int,
    *,
    programa_id: int | None = None,
) -> dict:
    atualidade = resumo_candidato(con, candidato_id)
    comprovacao = resumo_comprovacao(con, candidato_id)
    metas = listar_metas(con, candidato_id=candidato_id, programa_id=programa_id)
    conquistas = listar_conquistas(
        con, candidato_id=candidato_id, programa_id=programa_id
    )
    proximos: list[str] = []
    for pendencia in comprovacao["pendencias"]:
        proximos.append(
            f"Enviar comprovação de {pendencia['descricao']} "
            f"({pendencia['rotulo']})."
        )
    for meta in metas:
        if meta["status"] == "pendente":
            prazo = meta.get("prazo") or "sem prazo"
            proximos.append(f"Concluir meta: {meta['descricao']} (até {prazo}).")
    return {
        "candidato_id": int(candidato_id),
        "atualidade": atualidade,
        "comprovacao": comprovacao,
        "metas": metas,
        "conquistas": conquistas,
        "total_conquistas_validadas": sum(
            1 for item in conquistas if item["status"] == "validada"
        ),
        "proximos_passos": proximos,
    }
