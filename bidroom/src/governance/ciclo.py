"""Etapa 2 — ciclo contínuo de atualização.

Fluxo:
1. uma alteração (disponibilidade, certificação, conclusão de projeto,
   vencimento ou revisão periódica) vira um item da fila de revisão;
2. todas as disputas abertas recebem uma pendência de atualização, com um
   snapshot da decisão anterior;
3. o recálculo pode ser feito depois; enquanto não termina, a recomendação
   aparece como pendente e o erro, se houver, fica visível;
4. confirmar sem mudanças registra nova verificação, sem criar qualificação.

Nada aqui apaga `pontuacoes` ou `alocacoes`: a decisão anterior é preservada
até que o responsável decida recalcular.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from src.governance.atualidade import confirmar_candidato
from src.governance.auditoria import registrar

TABELAS_FATO = {
    "competencia": "competencias",
    "experiencia": "experiencias",
    "certificacao": "certificacoes",
    "formacao": "formacoes",
    "idioma": "idiomas",
}


def _agora() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _json(dados: dict | None) -> str | None:
    return json.dumps(dados, ensure_ascii=False, sort_keys=True) if dados else None


def _curriculo_atual(con, candidato_id: int) -> int | None:
    row = con.execute(
        "SELECT id FROM curriculos WHERE candidato_id = ?"
        " ORDER BY versao DESC, id DESC LIMIT 1",
        (candidato_id,),
    ).fetchone()
    return int(row["id"]) if row else None


def _candidato_do_fato(con, tipo_fato: str, fato_id: int) -> int | None:
    tabela = TABELAS_FATO.get(tipo_fato)
    if tabela is None:
        return None
    row = con.execute(
        f"SELECT c.candidato_id FROM {tabela} f"
        " JOIN curriculos c ON c.id = f.curriculo_id WHERE f.id = ?",
        (fato_id,),
    ).fetchone()
    return int(row["candidato_id"]) if row else None


def _item_fila_existente(
    con,
    candidato_id: int,
    tipo: str,
    referencia_tipo: str | None,
    referencia_id: int | None,
) -> int | None:
    row = con.execute(
        "SELECT id FROM fila_revisao WHERE candidato_id = ? AND tipo = ?"
        " AND COALESCE(referencia_tipo, '') = COALESCE(?, '')"
        " AND COALESCE(referencia_id, 0) = COALESCE(?, 0)"
        " AND status IN ('pendente','em_andamento') ORDER BY id DESC LIMIT 1",
        (candidato_id, tipo, referencia_tipo, referencia_id),
    ).fetchone()
    return int(row["id"]) if row else None


def criar_item_fila(
    con,
    candidato_id: int,
    tipo: str,
    motivo: str,
    *,
    responsavel: str | None = None,
    prazo: str | None = None,
    referencia_tipo: str | None = None,
    referencia_id: int | None = None,
    detalhes: dict | None = None,
    deduplicar: bool = True,
    commit: bool = True,
) -> int:
    """Cria um item da fila; por padrão não duplica pendência igual."""
    if deduplicar:
        existente = _item_fila_existente(
            con, candidato_id, tipo, referencia_tipo, referencia_id
        )
        if existente is not None:
            return existente
    cur = con.execute(
        "INSERT INTO fila_revisao (candidato_id, tipo, referencia_tipo,"
        " referencia_id, motivo, responsavel, prazo, detalhes_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            candidato_id,
            tipo,
            referencia_tipo,
            referencia_id,
            motivo,
            responsavel,
            prazo,
            _json(detalhes),
        ),
    )
    if commit:
        con.commit()
    return int(cur.lastrowid)


def listar_fila(
    con,
    status: str | None = None,
    candidato_id: int | None = None,
) -> list[dict]:
    where = ["1=1"]
    params: list[Any] = []
    if status:
        where.append("f.status = ?")
        params.append(status)
    if candidato_id is not None:
        where.append("f.candidato_id = ?")
        params.append(candidato_id)
    rows = con.execute(
        "SELECT f.*, c.nome_pseudonimo FROM fila_revisao f"
        " JOIN candidatos c ON c.id = f.candidato_id"
        f" WHERE {' AND '.join(where)} ORDER BY f.id DESC",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def atualizar_item_fila(
    con,
    item_id: int,
    status: str,
    ator: str,
    *,
    responsavel: str | None = None,
    erro: str | None = None,
    commit: bool = True,
) -> None:
    if status not in ("pendente", "em_andamento", "resolvida", "ignorada"):
        raise ValueError(f"status de fila inválido: {status}")
    resolvido = _agora() if status in ("resolvida", "ignorada") else None
    con.execute(
        "UPDATE fila_revisao SET status = ?, responsavel = COALESCE(?, responsavel),"
        " erro = ?, resolvido_em = ?, atualizado_em = datetime('now') WHERE id = ?",
        (status, responsavel, erro, resolvido, item_id),
    )
    registrar(
        con,
        acao="atualizar_fila_revisao",
        ator=ator,
        entidade="fila_revisao",
        entidade_id=item_id,
        detalhes={"status": status, "erro": erro},
        commit=False,
    )
    if commit:
        con.commit()


def _snapshot_decisao(con, disputa_id: int, candidato_id: int) -> dict:
    alocacoes = [
        dict(row)
        for row in con.execute(
            "SELECT a.papel_id, p.sigla AS papel, a.status"
            " FROM alocacoes a JOIN papeis p ON p.id = a.papel_id"
            " WHERE a.disputa_id = ? AND a.candidato_id = ?",
            (disputa_id, candidato_id),
        )
    ]
    pontuacoes = [
        dict(row)
        for row in con.execute(
            "SELECT papel_id, pontuacao_final, confianca, criado_em"
            " FROM pontuacoes WHERE disputa_id = ? AND candidato_id = ?"
            " ORDER BY id DESC LIMIT 8",
            (disputa_id, candidato_id),
        )
    ]
    return {"alocacoes": alocacoes, "pontuacoes": pontuacoes}


def propagar_para_disputas_abertas(
    con,
    candidato_id: int,
    tipo: str,
    motivo: str,
    *,
    referencia_tipo: str | None = None,
    referencia_id: int | None = None,
    detalhes: dict | None = None,
    responsavel: str | None = None,
    ator: str = "sistema",
    commit: bool = True,
) -> dict:
    """Marca todas as disputas abertas e cria um item de fila por candidato."""
    fila_id = criar_item_fila(
        con,
        candidato_id,
        tipo,
        motivo,
        responsavel=responsavel,
        referencia_tipo=referencia_tipo,
        referencia_id=referencia_id,
        detalhes=detalhes,
        commit=False,
    )
    disputas: list[int] = []
    for disputa in con.execute(
        "SELECT id FROM disputas WHERE status = 'aberta' ORDER BY id"
    ).fetchall():
        disputa_id = int(disputa["id"])
        existente = con.execute(
            "SELECT id FROM atualizacoes_pendentes WHERE disputa_id = ?"
            " AND candidato_id = ? AND tipo = ?"
            " AND COALESCE(referencia_tipo, '') = COALESCE(?, '')"
            " AND COALESCE(referencia_id, 0) = COALESCE(?, 0)"
            " AND status = 'pendente' LIMIT 1",
            (disputa_id, candidato_id, tipo, referencia_tipo, referencia_id),
        ).fetchone()
        if existente is not None:
            continue
        snapshot = _snapshot_decisao(con, disputa_id, candidato_id)
        dados = {**(detalhes or {}), "snapshot_decisao": snapshot}
        con.execute(
            "INSERT INTO atualizacoes_pendentes (disputa_id, candidato_id,"
            " tipo, referencia_tipo, referencia_id, motivo, detalhes_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                disputa_id,
                candidato_id,
                tipo,
                referencia_tipo,
                referencia_id,
                motivo,
                _json(dados),
            ),
        )
        disputas.append(disputa_id)
    registrar(
        con,
        acao="propagar_atualizacao",
        ator=ator,
        entidade="candidatos",
        entidade_id=candidato_id,
        detalhes={
            "tipo": tipo,
            "motivo": motivo,
            "disputas": disputas,
            "fila_id": fila_id,
        },
        commit=False,
    )
    if commit:
        con.commit()
    return {"fila_id": fila_id, "disputas": disputas}


def atualizar_disponibilidade(
    con,
    candidato_id: int,
    *,
    inicio: str | None,
    fim: str | None,
    percentual: int | None,
    fonte: str = "manual",
    responsavel: str | None = None,
    valido_ate: str | None = None,
    ator: str = "ui",
) -> dict:
    if percentual is not None and not (0 <= int(percentual) <= 100):
        raise ValueError("percentual de disponibilidade deve estar entre 0 e 100")
    if inicio and fim and inicio > fim:
        raise ValueError("início da disponibilidade não pode ser depois do fim")
    cur = con.execute(
        "INSERT INTO disponibilidades (candidato_id, inicio, fim, percentual,"
        " fonte, responsavel, confirmado_em, valido_ate, estado)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'confirmado')",
        (
            candidato_id,
            inicio,
            fim,
            percentual,
            fonte,
            responsavel,
            _agora(),
            valido_ate,
        ),
    )
    disponibilidade_id = int(cur.lastrowid)
    resultado = propagar_para_disputas_abertas(
        con,
        candidato_id,
        "disponibilidade",
        "Disponibilidade alterada",
        referencia_tipo="disponibilidade",
        referencia_id=disponibilidade_id,
        detalhes={
            "inicio": inicio,
            "fim": fim,
            "percentual": percentual,
            "fonte": fonte,
        },
        responsavel=responsavel,
        ator=ator,
        commit=False,
    )
    registrar(
        con,
        acao="atualizar_disponibilidade",
        ator=ator,
        entidade="disponibilidades",
        entidade_id=disponibilidade_id,
        detalhes={"candidato_id": candidato_id, "inicio": inicio, "fim": fim},
        commit=False,
    )
    con.commit()
    return {"disponibilidade_id": disponibilidade_id, **resultado}


def adicionar_certificacao(
    con,
    candidato_id: int,
    *,
    nome: str,
    emissor: str | None = None,
    ano: int | None = None,
    ator: str = "ui",
    responsavel: str | None = None,
) -> dict:
    if not nome.strip():
        raise ValueError("nome da certificação é obrigatório")
    curriculo_id = _curriculo_atual(con, candidato_id)
    if curriculo_id is None:
        raise ValueError("candidato sem currículo")
    cur = con.execute(
        "INSERT INTO certificacoes (curriculo_id, nome, emissor, ano)"
        " VALUES (?, ?, ?, ?)",
        (curriculo_id, nome.strip(), emissor, ano),
    )
    certificacao_id = int(cur.lastrowid)
    con.execute(
        "INSERT INTO atualidade_fatos (tipo_fato, fato_id, responsavel, fonte,"
        " estado) VALUES ('certificacao', ?, ?, 'manual', 'declarado')",
        (certificacao_id, responsavel),
    )
    resultado = propagar_para_disputas_abertas(
        con,
        candidato_id,
        "nova_certificacao",
        f"Nova certificação declarada: {nome}",
        referencia_tipo="certificacao",
        referencia_id=certificacao_id,
        detalhes={"nome": nome, "emissor": emissor, "ano": ano},
        responsavel=responsavel,
        ator=ator,
        commit=False,
    )
    comprovacao_id = criar_item_fila(
        con,
        candidato_id,
        "comprovacao_certificacao",
        f"Certificação sem comprovante: {nome}",
        responsavel=responsavel,
        referencia_tipo="certificacao",
        referencia_id=certificacao_id,
        detalhes={"certificacao_id": certificacao_id},
        commit=False,
    )
    registrar(
        con,
        acao="adicionar_certificacao",
        ator=ator,
        entidade="certificacoes",
        entidade_id=certificacao_id,
        detalhes={"candidato_id": candidato_id, "nome": nome},
        commit=False,
    )
    con.commit()
    return {
        "certificacao_id": certificacao_id,
        "comprovacao_fila_id": comprovacao_id,
        **resultado,
    }


def concluir_experiencia(
    con,
    experiencia_id: int,
    *,
    data_fim: str,
    ator: str = "ui",
    responsavel: str | None = None,
) -> dict:
    row = con.execute(
        "SELECT e.id, c.candidato_id FROM experiencias e"
        " JOIN curriculos c ON c.id = e.curriculo_id WHERE e.id = ?",
        (experiencia_id,),
    ).fetchone()
    if row is None:
        raise KeyError("experiência não encontrada")
    candidato_id = int(row["candidato_id"])
    con.execute(
        "UPDATE experiencias SET data_fim = ? WHERE id = ?",
        (data_fim, experiencia_id),
    )
    con.execute(
        "INSERT INTO atualidade_fatos (tipo_fato, fato_id, ultima_confirmacao,"
        " fonte, estado) VALUES ('experiencia', ?, ?, 'manual', 'declarado')"
        " ON CONFLICT (tipo_fato, fato_id) DO UPDATE SET"
        " ultima_confirmacao = excluded.ultima_confirmacao,"
        " fonte = excluded.fonte, atualizado_em = datetime('now')",
        (experiencia_id, _agora()),
    )
    resultado = propagar_para_disputas_abertas(
        con,
        candidato_id,
        "conclusao_projeto",
        "Conclusão de projeto/experiência registrada",
        referencia_tipo="experiencia",
        referencia_id=experiencia_id,
        detalhes={"data_fim": data_fim},
        responsavel=responsavel,
        ator=ator,
        commit=False,
    )
    registrar(
        con,
        acao="concluir_experiencia",
        ator=ator,
        entidade="experiencias",
        entidade_id=experiencia_id,
        detalhes={"candidato_id": candidato_id, "data_fim": data_fim},
        commit=False,
    )
    con.commit()
    return {"experiencia_id": experiencia_id, **resultado}


def confirmar_sem_mudancas(con, candidato_id: int, ator: str) -> dict:
    """Confirma os fatos atuais; não cria certificação/experiência nova."""
    fatos = confirmar_candidato(con, candidato_id, ator)
    agora = _agora()
    cur = con.execute(
        "UPDATE atualizacoes_pendentes SET status = 'resolvida',"
        " resolvido_em = ? WHERE candidato_id = ? AND status = 'pendente'"
        " AND tipo = 'revisao_periodica'",
        (agora, candidato_id),
    )
    con.execute(
        "UPDATE fila_revisao SET status = 'resolvida', resolvido_em = ?,"
        " atualizado_em = datetime('now') WHERE candidato_id = ?"
        " AND status IN ('pendente','em_andamento') AND tipo = 'revisao_periodica'",
        (agora, candidato_id),
    )
    registrar(
        con,
        acao="confirmar_sem_mudancas",
        ator=ator,
        entidade="candidatos",
        entidade_id=candidato_id,
        detalhes={
            "fatos_confirmados": fatos,
            "pendencias_revisao_resolvidas": cur.rowcount,
        },
        commit=False,
    )
    con.commit()
    return {
        "fatos_confirmados": fatos,
        "pendencias_revisao_resolvidas": cur.rowcount,
    }


def verificar_vencimentos(
    con, hoje: date | None = None, ator: str = "sistema"
) -> dict:
    """Cria pendências para validade vencida e revisão periódica."""
    referencia = (hoje or date.today()).isoformat()
    vencidos_fatos: list[dict] = []
    for row in con.execute(
        "SELECT tipo_fato, fato_id, valido_ate FROM atualidade_fatos"
        " WHERE valido_ate IS NOT NULL AND date(valido_ate) < date(?)"
        " AND estado != 'vencido'",
        (referencia,),
    ).fetchall():
        candidato_id = _candidato_do_fato(
            con, row["tipo_fato"], int(row["fato_id"])
        )
        if candidato_id is None:
            continue
        con.execute(
            "UPDATE atualidade_fatos SET estado = 'vencido',"
            " atualizado_em = datetime('now') WHERE tipo_fato = ? AND fato_id = ?",
            (row["tipo_fato"], row["fato_id"]),
        )
        resultado = propagar_para_disputas_abertas(
            con,
            candidato_id,
            "vencimento",
            f"Validade vencida: {row['tipo_fato']} #{row['fato_id']}",
            referencia_tipo=row["tipo_fato"],
            referencia_id=int(row["fato_id"]),
            detalhes={"valido_ate": row["valido_ate"]},
            ator=ator,
            commit=False,
        )
        vencidos_fatos.append(
            {
                "tipo_fato": row["tipo_fato"],
                "fato_id": int(row["fato_id"]),
                "candidato_id": candidato_id,
                **resultado,
            }
        )

    vencidos_disponibilidade: list[dict] = []
    for row in con.execute(
        "SELECT id, candidato_id, valido_ate FROM disponibilidades"
        " WHERE valido_ate IS NOT NULL AND date(valido_ate) < date(?)"
        " AND estado != 'vencido'",
        (referencia,),
    ).fetchall():
        con.execute(
            "UPDATE disponibilidades SET estado = 'vencido',"
            " atualizado_em = datetime('now') WHERE id = ?",
            (row["id"],),
        )
        resultado = propagar_para_disputas_abertas(
            con,
            int(row["candidato_id"]),
            "vencimento_disponibilidade",
            "Validade da disponibilidade vencida",
            referencia_tipo="disponibilidade",
            referencia_id=int(row["id"]),
            detalhes={"valido_ate": row["valido_ate"]},
            ator=ator,
            commit=False,
        )
        vencidos_disponibilidade.append(
            {"disponibilidade_id": int(row["id"]), **resultado}
        )

    revisoes: list[dict] = []
    for row in con.execute(
        "SELECT tipo_fato, fato_id, proxima_revisao FROM atualidade_fatos"
        " WHERE proxima_revisao IS NOT NULL AND date(proxima_revisao) <= date(?)",
        (referencia,),
    ).fetchall():
        candidato_id = _candidato_do_fato(
            con, row["tipo_fato"], int(row["fato_id"])
        )
        if candidato_id is None:
            continue
        resultado = propagar_para_disputas_abertas(
            con,
            candidato_id,
            "revisao_periodica",
            f"Revisão periódica: {row['tipo_fato']} #{row['fato_id']}",
            referencia_tipo=row["tipo_fato"],
            referencia_id=int(row["fato_id"]),
            detalhes={"proxima_revisao": row["proxima_revisao"]},
            ator=ator,
            commit=False,
        )
        revisoes.append(
            {
                "tipo_fato": row["tipo_fato"],
                "fato_id": int(row["fato_id"]),
                "candidato_id": candidato_id,
                **resultado,
            }
        )

    con.commit()
    return {
        "vencidos_fatos": vencidos_fatos,
        "vencidos_disponibilidade": vencidos_disponibilidade,
        "revisoes_periodicas": revisoes,
    }


def listar_atualizacoes_pendentes(
    con, disputa_id: int | None = None, status: str | None = "pendente"
) -> list[dict]:
    where = ["1=1"]
    params: list[Any] = []
    if disputa_id is not None:
        where.append("a.disputa_id = ?")
        params.append(disputa_id)
    if status:
        where.append("a.status = ?")
        params.append(status)
    rows = con.execute(
        "SELECT a.*, d.nome_ficticio,"
        " COALESCE(c.nome_pseudonimo, 'Demanda (edital)') AS nome_pseudonimo"
        " FROM atualizacoes_pendentes a"
        " JOIN disputas d ON d.id = a.disputa_id"
        " LEFT JOIN candidatos c ON c.id = a.candidato_id"
        f" WHERE {' AND '.join(where)} ORDER BY a.id DESC",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def classificar_disputa(con, disputa_id: int) -> dict:
    total = con.execute(
        "SELECT COUNT(*) c FROM atualizacoes_pendentes"
        " WHERE disputa_id = ? AND status = 'pendente'",
        (disputa_id,),
    ).fetchone()["c"]
    return {
        "disputa_id": disputa_id,
        "status": "pendente" if total else "atualizada",
        "pendencias": int(total),
    }


def recalcular_pendentes(
    con,
    disputa_id: int | None = None,
    ator: str = "sistema",
) -> dict:
    """Recalcula disputas com pendência; falha permanece visível e pendente."""
    from src.scoring.demanda import recalcular_disputa_demanda
    from src.scoring.servico import recalcular_disputa

    where = ["status = 'pendente'"]
    params: list[Any] = []
    if disputa_id is not None:
        where.append("disputa_id = ?")
        params.append(disputa_id)
    ids = [
        int(row["disputa_id"])
        for row in con.execute(
            "SELECT DISTINCT disputa_id FROM atualizacoes_pendentes"
            f" WHERE {' AND '.join(where)} ORDER BY disputa_id",
            params,
        )
    ]
    recalculadas: list[int] = []
    falhas: list[dict] = []
    for did in ids:
        disputa = con.execute(
            "SELECT id, preset_versao_id, edital_versao_id FROM disputas WHERE id = ?",
            (did,),
        ).fetchone()
        if disputa is None:
            erro = "disputa não encontrada"
            con.execute(
                "UPDATE atualizacoes_pendentes SET erro = ?"
                " WHERE disputa_id = ? AND status = 'pendente'",
                (erro, did),
            )
            con.commit()
            falhas.append({"disputa_id": did, "erro": erro})
            continue
        try:
            if disputa["edital_versao_id"] is not None:
                recalcular_disputa_demanda(
                    con,
                    did,
                    edital_versao_id=int(disputa["edital_versao_id"]),
                    ator=ator,
                    commit=False,
                )
            elif disputa["preset_versao_id"] is not None:
                recalcular_disputa(
                    con,
                    did,
                    int(disputa["preset_versao_id"]),
                    ator=ator,
                    commit=False,
                )
            else:
                raise ValueError("disputa sem preset ou edital publicado")
            con.execute(
                "UPDATE atualizacoes_pendentes SET status = 'resolvida',"
                " erro = NULL, resolvido_em = datetime('now')"
                " WHERE disputa_id = ? AND status = 'pendente'",
                (did,),
            )
            con.commit()
            recalculadas.append(did)
        except Exception as exc:  # noqa: BLE001 - falha fica registrada
            con.rollback()
            con.execute(
                "UPDATE atualizacoes_pendentes SET erro = ?"
                " WHERE disputa_id = ? AND status = 'pendente'",
                (str(exc), did),
            )
            con.commit()
            registrar(
                con,
                acao="erro_recalculo_pendente",
                ator=ator,
                entidade="disputas",
                entidade_id=did,
                detalhes={"erro": str(exc)},
            )
            falhas.append({"disputa_id": did, "erro": str(exc)})
    return {"recalculadas": recalculadas, "falhas": falhas}
