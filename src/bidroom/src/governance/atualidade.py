"""Etapa 1 — atualidade, responsável e validade dos fatos.

A data de ingestão não é a data de confirmação. Um currículo recém-ingerido
nasce como declaração (`ultima_confirmacao = NULL`); a frescura só melhora
quando alguém confirma o fato ou valida uma evidência.

Limiares (configuráveis por ambiente):
- `BIDROOM_FRESHNESS_ATENCAO_DIAS` (padrão 180)
- `BIDROOM_FRESHNESS_DESATUALIZADO_DIAS` (padrão 365)
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta

from src.governance.auditoria import registrar

TIPOS_FATO = ("competencia", "experiencia", "certificacao", "formacao", "idioma")
ROTULOS_FRESCURA = {
    "sem_confirmacao": "Sem confirmação",
    "atualizado": "Atualizado",
    "atencao": "Atenção",
    "desatualizado": "Desatualizado",
    "vencido": "Vencido",
}


def _int_env(nome: str, padrao: int) -> int:
    try:
        valor = int(os.environ.get(nome, padrao))
    except (TypeError, ValueError):
        return padrao
    return max(1, valor)


def limiares() -> dict[str, int]:
    atencao = _int_env("BIDROOM_FRESHNESS_ATENCAO_DIAS", 180)
    desatualizado = _int_env("BIDROOM_FRESHNESS_DESATUALIZADO_DIAS", 365)
    if desatualizado < atencao:
        desatualizado = atencao
    return {
        "atencao_dias": atencao,
        "desatualizado_dias": desatualizado,
    }


def _data(valor: str | date | datetime | None) -> date | None:
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip()
    try:
        return datetime.fromisoformat(texto.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(texto[:10])
        except ValueError:
            return None


def dias_desde(valor: str | date | datetime | None, hoje: date | None = None) -> int | None:
    data = _data(valor)
    if data is None:
        return None
    return max(0, ((hoje or date.today()) - data).days)


def dias_ate(valor: str | date | datetime | None, hoje: date | None = None) -> int | None:
    data = _data(valor)
    if data is None:
        return None
    return ((hoje or date.today()) - data).days


def classificar(
    ultima_confirmacao: str | date | datetime | None,
    valido_ate: str | date | datetime | None = None,
    hoje: date | None = None,
) -> str:
    """Classifica a frescura de um fato."""
    referencia = hoje or date.today()
    validade = _data(valido_ate)
    if validade is not None and referencia > validade:
        return "vencido"
    if not ultima_confirmacao:
        return "sem_confirmacao"
    dias = dias_desde(ultima_confirmacao, referencia) or 0
    limites = limiares()
    if dias > limites["desatualizado_dias"]:
        return "desatualizado"
    if dias > limites["atencao_dias"]:
        return "atencao"
    return "atualizado"


def _fatos_do_curriculo(con, curriculo_id: int) -> list[dict]:
    """Lista fatos do currículo com descrição legível (sem PII nova)."""
    consultas = [
        (
            "competencia",
            "SELECT id, nome AS descricao FROM competencias WHERE curriculo_id = ?",
        ),
        (
            "experiencia",
            "SELECT id,"
            " COALESCE(papel, 'Experiência') || ' · ' ||"
            " COALESCE(empresa, '—') AS descricao"
            " FROM experiencias WHERE curriculo_id = ?",
        ),
        (
            "certificacao",
            "SELECT id, nome AS descricao FROM certificacoes WHERE curriculo_id = ?",
        ),
        (
            "formacao",
            "SELECT id,"
            " COALESCE(grau, 'Formação') || ' · ' ||"
            " COALESCE(curso, '—') AS descricao"
            " FROM formacoes WHERE curriculo_id = ?",
        ),
        (
            "idioma",
            "SELECT id, idioma AS descricao FROM idiomas WHERE curriculo_id = ?",
        ),
    ]
    itens: list[dict] = []
    for tipo, sql in consultas:
        for row in con.execute(sql, (curriculo_id,)):
            itens.append(
                {
                    "tipo_fato": tipo,
                    "fato_id": int(row["id"]),
                    "descricao": row["descricao"],
                }
            )
    return itens


def _curriculo_atual(con, candidato_id: int) -> int | None:
    row = con.execute(
        "SELECT id FROM curriculos WHERE candidato_id = ?"
        " ORDER BY versao DESC, id DESC LIMIT 1",
        (candidato_id,),
    ).fetchone()
    return int(row["id"]) if row else None


def garantir_atualidade(con, candidato_id: int, commit: bool = True) -> None:
    """Migração lazy: cria linhas ausentes para fatos de currículos antigos.

    A data de ingestão é usada como referência de origem, mas
    `ultima_confirmacao` fica NULL: ingestão não é confirmação.
    """
    curriculo_id = _curriculo_atual(con, candidato_id)
    if curriculo_id is None:
        return
    dono = con.execute(
        "SELECT dono FROM candidatos WHERE id = ?", (candidato_id,)
    ).fetchone()
    responsavel = dono["dono"] if dono else None
    for fato in _fatos_do_curriculo(con, curriculo_id):
        con.execute(
            "INSERT OR IGNORE INTO atualidade_fatos"
            " (tipo_fato, fato_id, responsavel, fonte, estado)"
            " VALUES (?, ?, ?, 'migracao_curriculo', 'declarado')",
            (fato["tipo_fato"], fato["fato_id"], responsavel),
        )
    if not con.execute(
        "SELECT 1 FROM disponibilidades WHERE candidato_id = ? LIMIT 1",
        (candidato_id,),
    ).fetchone():
        con.execute(
            "INSERT INTO disponibilidades"
            " (candidato_id, fonte, estado) VALUES (?, 'nao_informado', 'desconhecido')",
            (candidato_id,),
        )
    if commit:
        con.commit()


def _linhas_atualidade(con, curriculo_id: int) -> list[dict]:
    itens: list[dict] = []
    for fato in _fatos_do_curriculo(con, curriculo_id):
        row = con.execute(
            "SELECT ultima_confirmacao, valido_ate, proxima_revisao, estado,"
            " responsavel, fonte FROM atualidade_fatos"
            " WHERE tipo_fato = ? AND fato_id = ?",
            (fato["tipo_fato"], fato["fato_id"]),
        ).fetchone()
        ultima = row["ultima_confirmacao"] if row else None
        validade = row["valido_ate"] if row else None
        estado = row["estado"] if row else "declarado"
        frescura = classificar(ultima, validade)
        itens.append(
            {
                **fato,
                "ultima_confirmacao": ultima,
                "valido_ate": validade,
                "estado": estado,
                "responsavel": row["responsavel"] if row else None,
                "fonte": row["fonte"] if row else None,
                "frescura": frescura,
                "rotulo_frescura": ROTULOS_FRESCURA.get(frescura, frescura),
            }
        )
    return itens


def resumo_candidato(
    con, candidato_id: int, hoje: date | None = None
) -> dict:
    """Resumo de frescura e pendências do candidato."""
    garantir_atualidade(con, candidato_id)
    curriculo_id = _curriculo_atual(con, candidato_id)
    if curriculo_id is None:
        return {
            "total": 0,
            "por_status": {},
            "itens": [],
            "pendencias": [],
            "disponibilidade": None,
            "limiares": limiares(),
        }
    itens = _linhas_atualidade(con, curriculo_id)
    for item in itens:
        item["frescura"] = classificar(
            item["ultima_confirmacao"], item["valido_ate"], hoje
        )
        item["rotulo_frescura"] = ROTULOS_FRESCURA[item["frescura"]]
    por_status: dict[str, int] = {}
    for item in itens:
        por_status[item["frescura"]] = por_status.get(item["frescura"], 0) + 1

    pendencias = [
        item
        for item in itens
        if item["frescura"] != "atualizado" or item["estado"] in ("enviado", "vencido")
    ]
    disponibilidade = con.execute(
        "SELECT * FROM disponibilidades WHERE candidato_id = ?"
        " ORDER BY id DESC LIMIT 1",
        (candidato_id,),
    ).fetchone()
    disp = dict(disponibilidade) if disponibilidade else None
    if disp is None:
        disp = {
            "estado": "desconhecido",
            "fonte": "nao_informado",
            "confirmado_em": None,
            "valido_ate": None,
            "inicio": None,
            "fim": None,
            "percentual": None,
        }
    disp["frescura"] = (
        classificar(disp.get("confirmado_em"), disp.get("valido_ate"), hoje)
        if disp.get("estado") != "desconhecido"
        else "sem_confirmacao"
    )
    if disp.get("estado") in ("desconhecido", "vencido") or disp["frescura"] != "atualizado":
        pendencias.append(
            {
                "tipo_fato": "disponibilidade",
                "fato_id": disp.get("id"),
                "descricao": "Disponibilidade no período da demanda",
                "ultima_confirmacao": disp.get("confirmado_em"),
                "valido_ate": disp.get("valido_ate"),
                "estado": disp.get("estado"),
                "frescura": disp["frescura"],
                "rotulo_frescura": ROTULOS_FRESCURA.get(
                    disp["frescura"], disp["frescura"]
                ),
            }
        )
    return {
        "total": len(itens),
        "por_status": por_status,
        "itens": itens,
        "pendencias": pendencias,
        "disponibilidade": disp,
        "limiares": limiares(),
    }


def listar_pendencias(con, candidato_id: int | None = None) -> list[dict]:
    """Pendências de um candidato ou de todos (para a Governança)."""
    if candidato_id is not None:
        return resumo_candidato(con, candidato_id)["pendencias"]
    candidatos = [
        int(row["id"]) for row in con.execute("SELECT id FROM candidatos ORDER BY id")
    ]
    pendencias: list[dict] = []
    for cid in candidatos:
        for item in resumo_candidato(con, cid)["pendencias"]:
            pendencias.append({"candidato_id": cid, **item})
    return pendencias


def confirmar_fato(
    con,
    tipo_fato: str,
    fato_id: int,
    ator: str,
    fonte: str = "confirmacao",
    valido_ate: str | None = None,
    commit: bool = True,
) -> None:
    """Confirma que o fato continua correto; não cria qualificação nova."""
    agora = datetime.now().replace(microsecond=0).isoformat(sep=" ")
    proxima = (
        date.today() + timedelta(days=limiares()["desatualizado_dias"])
    ).isoformat()
    row = con.execute(
        "SELECT id FROM atualidade_fatos WHERE tipo_fato = ? AND fato_id = ?",
        (tipo_fato, fato_id),
    ).fetchone()
    if row:
        con.execute(
            "UPDATE atualidade_fatos SET ultima_confirmacao = ?, valido_ate ="
            " COALESCE(?, valido_ate), proxima_revisao = ?, fonte = ?,"
            " atualizado_em = datetime('now') WHERE tipo_fato = ? AND fato_id = ?",
            (agora, valido_ate, proxima, fonte, tipo_fato, fato_id),
        )
    else:
        con.execute(
            "INSERT INTO atualidade_fatos"
            " (tipo_fato, fato_id, ultima_confirmacao, valido_ate,"
            " proxima_revisao, fonte, estado)"
            " VALUES (?, ?, ?, ?, ?, ?, 'declarado')",
            (tipo_fato, fato_id, agora, valido_ate, proxima, fonte),
        )
    registrar(
        con,
        acao="confirmar_atualidade",
        ator=ator,
        entidade="atualidade_fatos",
        entidade_id=fato_id,
        detalhes={"tipo_fato": tipo_fato, "fonte": fonte, "valido_ate": valido_ate},
        commit=False,
    )
    if commit:
        con.commit()


def confirmar_candidato(con, candidato_id: int, ator: str) -> int:
    """Confirma todos os fatos atuais do candidato em uma ação explícita."""
    garantir_atualidade(con, candidato_id)
    curriculo_id = _curriculo_atual(con, candidato_id)
    if curriculo_id is None:
        return 0
    fatos = _fatos_do_curriculo(con, curriculo_id)
    for fato in fatos:
        confirmar_fato(
            con, fato["tipo_fato"], fato["fato_id"], ator=ator, commit=False
        )
    registrar(
        con,
        acao="confirmar_candidato",
        ator=ator,
        entidade="candidatos",
        entidade_id=candidato_id,
        detalhes={"fatos": len(fatos)},
        commit=False,
    )
    con.commit()
    return len(fatos)


def definir_responsavel(
    con,
    tipo_fato: str,
    fato_id: int,
    responsavel: str | None,
    ator: str,
) -> None:
    garantir = con.execute(
        "SELECT id FROM atualidade_fatos WHERE tipo_fato = ? AND fato_id = ?",
        (tipo_fato, fato_id),
    ).fetchone()
    if garantir:
        con.execute(
            "UPDATE atualidade_fatos SET responsavel = ?, atualizado_em = datetime('now')"
            " WHERE tipo_fato = ? AND fato_id = ?",
            (responsavel, tipo_fato, fato_id),
        )
    else:
        con.execute(
            "INSERT INTO atualidade_fatos (tipo_fato, fato_id, responsavel, estado)"
            " VALUES (?, ?, ?, 'declarado')",
            (tipo_fato, fato_id, responsavel),
        )
    registrar(
        con,
        acao="definir_responsavel_atualidade",
        ator=ator,
        entidade="atualidade_fatos",
        entidade_id=fato_id,
        detalhes={"tipo_fato": tipo_fato, "responsavel": responsavel},
        commit=False,
    )
    con.commit()
