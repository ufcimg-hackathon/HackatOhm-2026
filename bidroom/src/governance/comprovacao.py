"""Etapa 3 — ciclo de comprovação.

Estados distintos:
- declarado: o fato existe no currículo, sem documento;
- enviado: documento recebido, aguardando análise;
- validado: documento aceito e vigente;
- rejeitado: documento recusado com motivo;
- vencido: documento aceito, mas fora da validade.

O mesmo documento (checksum) para o mesmo fato não cria outra linha. A
validação propaga a atualização para todas as disputas abertas; a decisão
anterior só é recalculada quando o responsável pedir.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.governance.auditoria import registrar
from src.governance.ciclo import criar_item_fila, propagar_para_disputas_abertas

TIPOS_COMPROVAVEIS = ("certificacao", "formacao", "experiencia")
TABELAS_FATO = {
    "competencia": "competencias",
    "experiencia": "experiencias",
    "certificacao": "certificacoes",
    "formacao": "formacoes",
    "idioma": "idiomas",
}
ROTULOS = {
    "declarado": "Declarado",
    "enviado": "Comprovante enviado",
    "validado": "Validado",
    "rejeitado": "Rejeitado",
    "vencido": "Vencido",
}


def _agora() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def caminho_evidencias() -> Path:
    pasta = Path(os.environ.get("BIDROOM_EVIDENCIAS", "data/evidencias"))
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def _info_fato(con, tipo_fato: str, fato_id: int) -> dict | None:
    tabela = TABELAS_FATO.get(tipo_fato)
    if tabela is None:
        return None
    if tipo_fato == "experiencia":
        descricao = (
            "COALESCE(f.papel, 'Experiência') || ' · ' ||"
            " COALESCE(f.empresa, '—')"
        )
    elif tipo_fato == "certificacao":
        descricao = "f.nome"
    elif tipo_fato == "formacao":
        descricao = (
            "COALESCE(f.grau, 'Formação') || ' · ' || COALESCE(f.curso, '—')"
        )
    elif tipo_fato == "competencia":
        descricao = "f.nome"
    else:
        descricao = "f.idioma"
    row = con.execute(
        f"SELECT f.id, f.curriculo_id, c.candidato_id, {descricao} AS descricao"
        f" FROM {tabela} f JOIN curriculos c ON c.id = f.curriculo_id"
        " WHERE f.id = ?",
        (fato_id,),
    ).fetchone()
    return dict(row) if row else None


def listar_evidencias(con, tipo_fato: str, fato_id: int) -> list[dict]:
    rows = con.execute(
        "SELECT * FROM evidencias_documentais WHERE tipo_fato = ? AND fato_id = ?"
        " ORDER BY id DESC",
        (tipo_fato, fato_id),
    ).fetchall()
    return [dict(row) for row in rows]


def estado_comprovacao(
    con, tipo_fato: str, fato_id: int, hoje: date | None = None
) -> str:
    """Agrega o melhor estado disponível, sem confundir declaração e prova."""
    referencia = hoje or date.today()
    estados = [row["status"] for row in listar_evidencias(con, tipo_fato, fato_id)]
    if "validado" in estados:
        for row in listar_evidencias(con, tipo_fato, fato_id):
            if row["status"] != "validado":
                continue
            validade = row.get("valido_ate")
            if validade:
                try:
                    if date.fromisoformat(str(validade)[:10]) < referencia:
                        continue
                except ValueError:
                    continue
            return "validado"
        return "vencido"
    if "enviado" in estados:
        return "enviado"
    if "vencido" in estados:
        return "vencido"
    if "rejeitado" in estados:
        return "rejeitado"
    return "declarado"


def requisito_satisfeito(
    con, tipo_fato: str, fato_id: int, hoje: date | None = None
) -> bool:
    """Documento rejeitado ou vencido não satisfaz requisito obrigatório."""
    return estado_comprovacao(con, tipo_fato, fato_id, hoje) == "validado"


def _upsert_atualidade(
    con,
    tipo_fato: str,
    fato_id: int,
    *,
    estado: str,
    fonte: str,
    valido_ate: str | None = None,
    confirmar: bool = False,
) -> None:
    agora = _agora()
    ultima = agora if confirmar else None
    proxima = (
        (date.today() + timedelta(days=365)).isoformat() if confirmar else None
    )
    con.execute(
        "INSERT INTO atualidade_fatos (tipo_fato, fato_id, ultima_confirmacao,"
        " valido_ate, proxima_revisao, fonte, estado)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (tipo_fato, fato_id) DO UPDATE SET"
        " ultima_confirmacao = COALESCE(excluded.ultima_confirmacao,"
        " atualidade_fatos.ultima_confirmacao),"
        " valido_ate = COALESCE(excluded.valido_ate, atualidade_fatos.valido_ate),"
        " proxima_revisao = COALESCE(excluded.proxima_revisao,"
        " atualidade_fatos.proxima_revisao),"
        " fonte = excluded.fonte, estado = excluded.estado,"
        " atualizado_em = datetime('now')",
        (tipo_fato, fato_id, ultima, valido_ate, proxima, fonte, estado),
    )


def _guardar_arquivo(nome_arquivo: str, conteudo: bytes, checksum: str) -> Path:
    nome = Path(nome_arquivo).name or "comprovante.docx"
    destino = caminho_evidencias() / f"{checksum}_{nome}"
    if not destino.exists():
        destino.write_bytes(conteudo)
    return destino


def anexar_evidencia(
    con,
    tipo_fato: str,
    fato_id: int,
    *,
    nome_arquivo: str,
    conteudo: bytes,
    ator: str,
    valido_ate: str | None = None,
    observacao: str | None = None,
    commit: bool = True,
) -> dict:
    if tipo_fato not in TIPOS_COMPROVAVEIS:
        raise ValueError(f"tipo de fato sem comprovação: {tipo_fato}")
    if not conteudo:
        raise ValueError("arquivo de comprovante vazio")
    if Path(nome_arquivo).suffix.lower() != ".docx":
        raise ValueError("comprovante deve ser .docx no MVP")
    info = _info_fato(con, tipo_fato, fato_id)
    if info is None:
        raise KeyError("fato não encontrado")
    checksum = hashlib.sha256(conteudo).hexdigest()
    existente = con.execute(
        "SELECT * FROM evidencias_documentais WHERE tipo_fato = ?"
        " AND fato_id = ? AND checksum = ?",
        (tipo_fato, fato_id, checksum),
    ).fetchone()
    if existente is not None:
        if existente["status"] == "rejeitado":
            con.execute(
                "UPDATE evidencias_documentais SET status = 'enviado',"
                " revisor = NULL, analisado_em = NULL, observacao = ?,"
                " enviado_em = datetime('now') WHERE id = ?",
                (observacao, existente["id"]),
            )
            _upsert_atualidade(
                con, tipo_fato, fato_id, estado="enviado", fonte="documento"
            )
            criar_item_fila(
                con,
                int(info["candidato_id"]),
                "analise_comprovante",
                f"Reenvio de comprovante: {info['descricao']}",
                referencia_tipo=tipo_fato,
                referencia_id=fato_id,
                detalhes={"evidencia_id": int(existente["id"])},
                commit=False,
            )
            registrar(
                con,
                acao="reenviar_evidencia",
                ator=ator,
                entidade="evidencias_documentais",
                entidade_id=int(existente["id"]),
                detalhes={"tipo_fato": tipo_fato, "fato_id": fato_id},
                commit=False,
            )
            if commit:
                con.commit()
            return {
                "evidencia_id": int(existente["id"]),
                "status": "enviado",
                "duplicado": True,
            }
        return {
            "evidencia_id": int(existente["id"]),
            "status": existente["status"],
            "duplicado": True,
        }

    destino = _guardar_arquivo(nome_arquivo, conteudo, checksum)
    cur = con.execute(
        "INSERT INTO evidencias_documentais (tipo_fato, fato_id, nome_arquivo,"
        " caminho, checksum, status, valido_ate, observacao)"
        " VALUES (?, ?, ?, ?, ?, 'enviado', ?, ?)",
        (
            tipo_fato,
            fato_id,
            Path(nome_arquivo).name,
            str(destino),
            checksum,
            valido_ate,
            observacao,
        ),
    )
    evidencia_id = int(cur.lastrowid)
    _upsert_atualidade(
        con, tipo_fato, fato_id, estado="enviado", fonte="documento"
    )
    criar_item_fila(
        con,
        int(info["candidato_id"]),
        "analise_comprovante",
        f"Analisar comprovante: {info['descricao']}",
        referencia_tipo=tipo_fato,
        referencia_id=fato_id,
        detalhes={"evidencia_id": evidencia_id},
        commit=False,
    )
    registrar(
        con,
        acao="anexar_evidencia",
        ator=ator,
        entidade="evidencias_documentais",
        entidade_id=evidencia_id,
        detalhes={
            "tipo_fato": tipo_fato,
            "fato_id": fato_id,
            "checksum": checksum,
        },
        commit=False,
    )
    if commit:
        con.commit()
    return {"evidencia_id": evidencia_id, "status": "enviado", "duplicado": False}


def _evidencia(con, evidencia_id: int) -> sqlite3.Row:
    row = con.execute(
        "SELECT * FROM evidencias_documentais WHERE id = ?", (evidencia_id,)
    ).fetchone()
    if row is None:
        raise KeyError("evidência não encontrada")
    return row


def validar_evidencia(
    con,
    evidencia_id: int,
    *,
    revisor: str,
    ator: str = "ui",
    valido_ate: str | None = None,
    observacao: str | None = None,
) -> dict:
    row = _evidencia(con, evidencia_id)
    validade = valido_ate or row["valido_ate"]
    if validade:
        try:
            if date.fromisoformat(str(validade)[:10]) < date.today():
                raise ValueError("não é possível validar documento já vencido")
        except ValueError as exc:
            raise ValueError("validade inválida ou vencida") from exc
    con.execute(
        "UPDATE evidencias_documentais SET status = 'validado', revisor = ?,"
        " analisado_em = datetime('now'), valido_ate = ?, observacao = ?"
        " WHERE id = ?",
        (revisor, validade, observacao, evidencia_id),
    )
    tipo_fato = row["tipo_fato"]
    fato_id = int(row["fato_id"])
    _upsert_atualidade(
        con,
        tipo_fato,
        fato_id,
        estado="validado",
        fonte="documento",
        valido_ate=validade,
        confirmar=True,
    )
    con.execute(
        "UPDATE fila_revisao SET status = 'resolvida', resolvido_em = datetime('now'),"
        " atualizado_em = datetime('now') WHERE tipo = 'analise_comprovante'"
        " AND referencia_tipo = ? AND referencia_id = ?"
        " AND status IN ('pendente','em_andamento')",
        (tipo_fato, fato_id),
    )
    info = _info_fato(con, tipo_fato, fato_id)
    if info is not None:
        propagar_para_disputas_abertas(
            con,
            int(info["candidato_id"]),
            "comprovacao_validada",
            f"Comprovação validada: {info['descricao']}",
            referencia_tipo=tipo_fato,
            referencia_id=fato_id,
            detalhes={"evidencia_id": evidencia_id, "valido_ate": validade},
            ator=ator,
            commit=False,
        )
    registrar(
        con,
        acao="validar_evidencia",
        ator=ator,
        entidade="evidencias_documentais",
        entidade_id=evidencia_id,
        detalhes={"tipo_fato": tipo_fato, "fato_id": fato_id, "revisor": revisor},
        commit=False,
    )
    con.commit()
    return {"evidencia_id": evidencia_id, "status": "validado"}


def rejeitar_evidencia(
    con,
    evidencia_id: int,
    *,
    revisor: str,
    motivo: str,
    ator: str = "ui",
) -> dict:
    if not motivo.strip():
        raise ValueError("rejeição exige motivo")
    row = _evidencia(con, evidencia_id)
    con.execute(
        "UPDATE evidencias_documentais SET status = 'rejeitado', revisor = ?,"
        " analisado_em = datetime('now'), observacao = ? WHERE id = ?",
        (revisor, motivo, evidencia_id),
    )
    tipo_fato = row["tipo_fato"]
    fato_id = int(row["fato_id"])
    _upsert_atualidade(
        con, tipo_fato, fato_id, estado="declarado", fonte="documento"
    )
    con.execute(
        "UPDATE fila_revisao SET status = 'resolvida', resolvido_em = datetime('now'),"
        " atualizado_em = datetime('now') WHERE tipo = 'analise_comprovante'"
        " AND referencia_tipo = ? AND referencia_id = ?"
        " AND status IN ('pendente','em_andamento')",
        (tipo_fato, fato_id),
    )
    info = _info_fato(con, tipo_fato, fato_id)
    if info is not None:
        criar_item_fila(
            con,
            int(info["candidato_id"]),
            "comprovacao_rejeitada",
            f"Comprovante rejeitado: {info['descricao']} — {motivo}",
            referencia_tipo=tipo_fato,
            referencia_id=fato_id,
            detalhes={"evidencia_id": evidencia_id, "motivo": motivo},
            commit=False,
        )
        propagar_para_disputas_abertas(
            con,
            int(info["candidato_id"]),
            "comprovacao_rejeitada",
            f"Comprovante rejeitado: {info['descricao']}",
            referencia_tipo=tipo_fato,
            referencia_id=fato_id,
            detalhes={"evidencia_id": evidencia_id, "motivo": motivo},
            ator=ator,
            commit=False,
        )
    registrar(
        con,
        acao="rejeitar_evidencia",
        ator=ator,
        entidade="evidencias_documentais",
        entidade_id=evidencia_id,
        detalhes={"tipo_fato": tipo_fato, "fato_id": fato_id, "motivo": motivo},
        commit=False,
    )
    con.commit()
    return {"evidencia_id": evidencia_id, "status": "rejeitado"}


def verificar_vencimento_evidencias(
    con, hoje: date | None = None, ator: str = "sistema"
) -> list[dict]:
    referencia = (hoje or date.today()).isoformat()
    vencidas: list[dict] = []
    rows = con.execute(
        "SELECT * FROM evidencias_documentais WHERE status = 'validado'"
        " AND valido_ate IS NOT NULL AND date(valido_ate) < date(?)",
        (referencia,),
    ).fetchall()
    for row in rows:
        con.execute(
            "UPDATE evidencias_documentais SET status = 'vencido' WHERE id = ?",
            (row["id"],),
        )
        tipo_fato = row["tipo_fato"]
        fato_id = int(row["fato_id"])
        _upsert_atualidade(
            con, tipo_fato, fato_id, estado="vencido", fonte="documento"
        )
        info = _info_fato(con, tipo_fato, fato_id)
        if info is not None:
            propagar_para_disputas_abertas(
                con,
                int(info["candidato_id"]),
                "comprovacao_vencida",
                f"Comprovante vencido: {info['descricao']}",
                referencia_tipo=tipo_fato,
                referencia_id=fato_id,
                detalhes={"evidencia_id": int(row["id"])},
                ator=ator,
                commit=False,
            )
        vencidas.append(
            {
                "evidencia_id": int(row["id"]),
                "tipo_fato": tipo_fato,
                "fato_id": fato_id,
            }
        )
    con.commit()
    return vencidas


def listar_pendencias_comprovacao(
    con, candidato_id: int | None = None, hoje: date | None = None
) -> list[dict]:
    where = ["1=1"]
    params: list[Any] = []
    if candidato_id is not None:
        where.append("c.candidato_id = ?")
        params.append(candidato_id)
    pendencias: list[dict] = []
    for tipo_fato in TIPOS_COMPROVAVEIS:
        tabela = TABELAS_FATO[tipo_fato]
        if tipo_fato == "experiencia":
            descricao = (
                "COALESCE(f.papel, 'Experiência') || ' · ' ||"
                " COALESCE(f.empresa, '—')"
            )
        elif tipo_fato == "certificacao":
            descricao = "f.nome"
        else:
            descricao = (
                "COALESCE(f.grau, 'Formação') || ' · ' || COALESCE(f.curso, '—')"
            )
        rows = con.execute(
            f"SELECT f.id, c.candidato_id, {descricao} AS descricao"
            f" FROM {tabela} f JOIN curriculos c ON c.id = f.curriculo_id"
            f" WHERE {' AND '.join(where)}",
            params,
        ).fetchall()
        for row in rows:
            estado = estado_comprovacao(con, tipo_fato, int(row["id"]), hoje)
            if estado == "validado":
                continue
            pendencias.append(
                {
                    "tipo_fato": tipo_fato,
                    "fato_id": int(row["id"]),
                    "candidato_id": int(row["candidato_id"]),
                    "descricao": row["descricao"],
                    "estado": estado,
                    "rotulo": ROTULOS[estado],
                }
            )
    return pendencias


def resumo_comprovacao(
    con, candidato_id: int, hoje: date | None = None
) -> dict:
    pendencias = listar_pendencias_comprovacao(con, candidato_id, hoje)
    total = 0
    for tipo_fato in TIPOS_COMPROVAVEIS:
        tabela = TABELAS_FATO[tipo_fato]
        total += con.execute(
            f"SELECT COUNT(*) c FROM {tabela} f"
            " JOIN curriculos c ON c.id = f.curriculo_id"
            " WHERE c.candidato_id = ?",
            (candidato_id,),
        ).fetchone()["c"]
    por_estado: dict[str, int] = {}
    for item in pendencias:
        por_estado[item["estado"]] = por_estado.get(item["estado"], 0) + 1
    validados = total - len(pendencias)
    if validados:
        por_estado["validado"] = validados
    return {
        "total": total,
        "por_estado": por_estado,
        "pendencias": pendencias,
    }
