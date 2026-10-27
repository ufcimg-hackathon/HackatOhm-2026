"""Etapa 5 — pontuação dinâmica contra a versão aprovada do edital.

O motor antigo (`engine.py`) continua atendendo disputas com preset fixo. Esta
camada interpreta os requisitos revisados do edital e produz três leituras
separadas, como exige a etapa:

- `nota_tecnica`: pontos previstos no edital;
- `score_interno_apoio`: sinal interno de completude/evidência, que não é
  probabilidade e não substitui a nota do edital;
- `confianca` e `atualidade`: qualidade e frescura da informação usada.

Cada requisito gera um resultado explícito (`atendido`, `pendente`,
`nao_atendido`) com motivo e fatos de origem. Requisitos de equipe/empresa não
são convertidos em ponto individual; ficam registrados como coletivos.
"""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from src.extraction.regras import CERTIFICACOES, COMPETENCIAS
from src.governance.auditoria import registrar
from src.governance.editais import listar_requisitos, obter_versao
from src.scoring.contexto import (
    ContextoCandidato,
    anos_intervalos,
    carregar_contexto,
    carregar_contextos,
)

STATUS_ATENDIDO = "atendido"
STATUS_PENDENTE = "pendente"
STATUS_NAO_ATENDIDO = "nao_atendido"
STATUS_ELIMINADO = "eliminado"

STATUS_GERAL = ("elegivel", "pendente", "eliminado")
TIPOS_INFORMATIVOS = {"papel", "vaga", "limite", "regra_combinacao"}
FRESCURA_FATOR = {
    "atualizado": 1.0,
    "atencao": 0.75,
    "desatualizado": 0.4,
    "sem_confirmacao": 0.25,
    "vencido": 0.0,
}
EVIDENCIA_FATOR = {
    "validado": 1.0,
    "enviado": 0.5,
    "declarado": 0.25,
    "rejeitado": 0.0,
    "vencido": 0.0,
}
NIVEIS_IDIOMA = {
    "nativo": 1.0,
    "fluente": 1.0,
    "avancado": 1.0,
    "intermediario": 0.6,
    "basico": 0.3,
}


def _norm(texto: str | None) -> str:
    if not texto:
        return ""
    return " ".join(
        unicodedata.normalize("NFKD", str(texto))
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
        .split()
    )


def _slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _norm(texto)).strip("_")[:60] or "papel"


def _match_nome(alvo: str | None, texto: str | None) -> bool:
    a = _norm(alvo)
    t = _norm(texto)
    if not a or not t:
        return False
    if a == t:
        return True
    if len(a) <= 4:
        return bool(re.search(r"\b" + re.escape(a) + r"\b", t))
    return a in t or t in a


def _num(valor: Any) -> float | None:
    if valor is None or valor == "":
        return None
    try:
        return float(str(valor).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _data(texto: str | None) -> date | None:
    if not texto:
        return None
    try:
        return date.fromisoformat(str(texto)[:10])
    except ValueError:
        return None


def _media(valores: list[float], pesos: list[float] | None = None) -> float:
    if not valores:
        return 0.0
    if pesos is None or len(pesos) != len(valores):
        return round(sum(valores) / len(valores), 2)
    total_peso = sum(max(0.0, peso) for peso in pesos)
    if total_peso <= 0:
        return 0.0
    return round(
        sum(valor * max(0.0, peso) for valor, peso in zip(valores, pesos))
        / total_peso,
        2,
    )


def _fator_frescura(fato: Any) -> float:
    return FRESCURA_FATOR.get(getattr(fato, "frescura", "sem_confirmacao"), 0.25)


def _confianca_fato(fato: Any) -> float:
    base = float(getattr(fato, "confianca", 0) or 0)
    evidencia = EVIDENCIA_FATOR.get(
        getattr(fato, "evidencia_estado", "declarado"), 0.25
    )
    return round(max(base, evidencia * 100), 2)


def _atualidade_fato(fato: Any) -> float:
    frescura = _fator_frescura(fato)
    evidencia = EVIDENCIA_FATOR.get(
        getattr(fato, "evidencia_estado", "declarado"), 0.25
    )
    return round(100 * (0.6 * frescura + 0.4 * evidencia), 2)


def _fato_info(tipo: str, fato: Any) -> dict:
    descricao = (
        getattr(fato, "nome", None)
        or getattr(fato, "dominio", None)
        or getattr(fato, "curso", None)
        or getattr(fato, "idioma", None)
        or tipo
    )
    return {
        "tipo": tipo,
        "fato_id": getattr(fato, "fato_id", None),
        "descricao": descricao,
        "confianca": _confianca_fato(fato),
        "frescura": getattr(fato, "frescura", "sem_confirmacao"),
        "evidencia_estado": getattr(fato, "evidencia_estado", "declarado"),
        "valido_ate": getattr(fato, "valido_ate", None),
    }


def _fatos_resumo(fatos: list[tuple[str, Any]]) -> list[dict]:
    return [_fato_info(tipo, fato) for tipo, fato in fatos]


def _conf_atual(fatos: list[tuple[str, Any]]) -> tuple[float, float, str]:
    if not fatos:
        return 0.0, 0.0, "declarado"
    confianca = _media([_confianca_fato(fato) for _, fato in fatos])
    atualidade = _media([_atualidade_fato(fato) for _, fato in fatos])
    prioridade = {
        "validado": 0,
        "enviado": 1,
        "vencido": 2,
        "rejeitado": 3,
        "declarado": 4,
    }
    estado = min(
        (getattr(fato, "evidencia_estado", "declarado") for _, fato in fatos),
        key=lambda valor: prioridade.get(valor, 9),
    )
    return confianca, atualidade, estado


@dataclass
class ResultadoRequisito:
    requisito_id: int
    tipo: str
    escopo: str
    descricao: str
    status: str
    motivo: str
    obrigatorio: bool
    pontos_possiveis: float = 0.0
    pontos_obtidos: float = 0.0
    atendimento: float | None = None
    confianca: float = 0.0
    atualidade: float = 0.0
    evidencia: str = "declarado"
    fatos: list[dict] = field(default_factory=list)
    detalhe: dict = field(default_factory=dict)

    def como_dict(self) -> dict:
        return {
            "requisito_id": self.requisito_id,
            "tipo": self.tipo,
            "escopo": self.escopo,
            "descricao": self.descricao,
            "status": self.status,
            "motivo": self.motivo,
            "obrigatorio": self.obrigatorio,
            "pontos_possiveis": round(self.pontos_possiveis, 2),
            "pontos_obtidos": round(self.pontos_obtidos, 2),
            "atendimento": (
                round(self.atendimento, 4) if self.atendimento is not None else None
            ),
            "confianca": round(self.confianca, 2),
            "atualidade": round(self.atualidade, 2),
            "evidencia": self.evidencia,
            "fatos": self.fatos,
            "detalhe": self.detalhe,
        }


@dataclass
class ResultadoDemanda:
    candidato_id: int
    curriculo_id: int
    edital_versao_id: int
    papel_id: int
    papel: str
    papel_nome: str
    vaga: int | None
    nota_tecnica: float
    pontuacao_maxima: float
    score_interno_apoio: float
    confianca: float
    atualidade: float
    status: str
    requisitos: list[ResultadoRequisito] = field(default_factory=list)
    coletivos: list[ResultadoRequisito] = field(default_factory=list)
    detalhe: dict = field(default_factory=dict)

    def como_dict(self) -> dict:
        return {
            "candidato_id": self.candidato_id,
            "curriculo_id": self.curriculo_id,
            "edital_versao_id": self.edital_versao_id,
            "papel_id": self.papel_id,
            "papel": self.papel,
            "papel_nome": self.papel_nome,
            "vaga": self.vaga,
            "nota_tecnica": round(self.nota_tecnica, 2),
            "pontuacao_maxima": round(self.pontuacao_maxima, 2),
            "score_interno_apoio": round(self.score_interno_apoio, 2),
            "confianca": round(self.confianca, 2),
            "atualidade": round(self.atualidade, 2),
            "status": self.status,
            "requisitos": [item.como_dict() for item in self.requisitos],
            "coletivos": [item.como_dict() for item in self.coletivos],
            "detalhe": self.detalhe,
        }


def _resultado(
    req: dict,
    status: str,
    motivo: str,
    *,
    pontos_possiveis: float = 0.0,
    pontos_obtidos: float = 0.0,
    atendimento: float | None = None,
    confianca: float = 0.0,
    atualidade: float = 0.0,
    evidencia: str = "declarado",
    fatos: list[tuple[str, Any]] | None = None,
    detalhe: dict | None = None,
) -> ResultadoRequisito:
    return ResultadoRequisito(
        requisito_id=int(req.get("id") or 0),
        tipo=req.get("tipo", "outro"),
        escopo=req.get("escopo", "individual"),
        descricao=req.get("descricao", ""),
        status=status,
        motivo=motivo,
        obrigatorio=bool(req.get("obrigatorio")),
        pontos_possiveis=pontos_possiveis,
        pontos_obtidos=pontos_obtidos,
        atendimento=atendimento,
        confianca=confianca,
        atualidade=atualidade,
        evidencia=evidencia,
        fatos=_fatos_resumo(fatos or []),
        detalhe=detalhe or {},
    )


def _aplicar_requisito(req: dict, papel: dict) -> bool:
    alvo = (req.get("regra") or {}).get("papel")
    return not alvo or _norm(alvo) == _norm(papel["sigla"])


def _garantir_papel(con, req: dict) -> dict:
    regra = req.get("regra") or {}
    sigla = str(regra.get("sigla") or _slug(req.get("descricao", "")))
    nome = str(regra.get("nome") or req.get("descricao") or sigla)
    vagas = int(regra["vagas"]) if regra.get("vagas") is not None else None
    row = con.execute(
        "SELECT id, nome, sigla, requisitos_json FROM papeis WHERE sigla = ?",
        (sigla,),
    ).fetchone()
    if row is not None:
        return {
            "papel_id": int(row["id"]),
            "sigla": row["sigla"],
            "nome": row["nome"],
            "vagas": vagas,
            "requisito_id": int(req["id"]),
        }
    cur = con.execute(
        "INSERT INTO papeis (nome, sigla, requisitos_json) VALUES (?, ?, ?)",
        (
            nome,
            sigla,
            json.dumps(
                {"vagas": vagas or 0, "origem": "edital", "edital_versao_id": req.get("edital_versao_id")},
                ensure_ascii=False,
            ),
        ),
    )
    return {
        "papel_id": int(cur.lastrowid),
        "sigla": sigla,
        "nome": nome,
        "vagas": vagas,
        "requisito_id": int(req["id"]),
    }


def carregar_demanda(con, edital_versao_id: int) -> dict:
    versao = obter_versao(con, edital_versao_id)
    if versao is None:
        raise KeyError("versão de edital não encontrada")
    if versao["status"] not in ("aprovada", "ativa", "substituida"):
        raise ValueError(
            f"versão {versao['status']} não pode ser usada para pontuar"
        )
    requisitos = [
        req
        for req in listar_requisitos(con, edital_versao_id)
        if req["status"] in ("aprovado", "alterado")
    ]
    if not requisitos:
        raise ValueError("versão sem requisitos aprovados")
    papeis: list[dict] = []
    for req in requisitos:
        if req["tipo"] != "papel":
            continue
        papeis.append(_garantir_papel(con, req))
    if not papeis:
        raise ValueError(
            "demanda sem papel identificado; revise o edital antes de pontuar"
        )
    pontuacao_maxima = 0.0
    for req in requisitos:
        if req["tipo"] == "pontuacao" and req["escopo"] == "individual":
            pontos = _num((req.get("regra") or {}).get("pontos"))
            if pontos:
                pontuacao_maxima += pontos
    return {
        "versao": versao,
        "versao_id": edital_versao_id,
        "requisitos": requisitos,
        "papeis": papeis,
        "pontuacao_maxima": round(pontuacao_maxima, 2),
        "coletivos": [
            req
            for req in requisitos
            if req["escopo"] != "individual"
            and req["tipo"] not in TIPOS_INFORMATIVOS
        ],
    }


def _status_faltante(req: dict) -> str:
    return STATUS_PENDENTE if req.get("obrigatorio") else STATUS_NAO_ATENDIDO


def _competencias(ctx: ContextoCandidato) -> list[tuple[str, Any]]:
    return [("competencia", fato) for fato in ctx.competencias]


def _certificacoes(ctx: ContextoCandidato) -> list[tuple[str, Any]]:
    return [("certificacao", fato) for fato in ctx.certificacoes]


def _formacoes(ctx: ContextoCandidato) -> list[tuple[str, Any]]:
    return [("formacao", fato) for fato in ctx.formacoes]


def _idiomas(ctx: ContextoCandidato) -> list[tuple[str, Any]]:
    return [("idioma", fato) for fato in ctx.idiomas]


def _experiencias(
    ctx: ContextoCandidato, dominio: str | None = None
) -> list[tuple[str, Any]]:
    fatos = ctx.experiencias
    if dominio:
        fatos = [
            fato
            for fato in fatos
            if _match_nome(dominio, fato.dominio)
            or _match_nome(dominio, fato.resumo)
        ]
    return [("experiencia", fato) for fato in fatos]


def _encontrar_em(
    itens: list[str], fatos: list[tuple[str, Any]], atributo: str
) -> tuple[list[tuple[str, Any]], list[str]]:
    encontrados: list[tuple[str, Any]] = []
    faltantes: list[str] = []
    for item in itens:
        achados = [
            (tipo, fato)
            for tipo, fato in fatos
            if _match_nome(item, getattr(fato, atributo, None))
        ]
        if achados:
            encontrados.extend(achados)
        else:
            faltantes.append(item)
    return encontrados, faltantes


def _avaliar_competencia(req: dict, ctx: ContextoCandidato) -> ResultadoRequisito:
    regra = req.get("regra") or {}
    itens = [str(item) for item in (regra.get("itens") or []) if item]
    if not itens:
        return _resultado(req, _status_faltante(req), "Competência sem item identificado")
    encontrados, faltantes = _encontrar_em(itens, _competencias(ctx), "nome")
    operador = _norm(regra.get("operador") or "todas")
    if operador == "qualquer":
        atende = bool(encontrados)
    else:
        atende = not faltantes
    if atende:
        status = STATUS_ATENDIDO
        motivo = "Competência(s) encontrada(s): " + ", ".join(
            sorted({item for item in itens if item not in faltantes})
        )
    elif req.get("obrigatorio"):
        status = STATUS_PENDENTE
        motivo = "Competência(s) não localizada(s): " + ", ".join(faltantes)
    else:
        status = STATUS_NAO_ATENDIDO
        motivo = "Competência(s) diferencial(is) não localizada(s): " + ", ".join(
            faltantes
        )
    conf, atual, evidencia = _conf_atual(encontrados)
    return _resultado(
        req,
        status,
        motivo,
        confianca=conf,
        atualidade=atual,
        evidencia=evidencia,
        fatos=encontrados,
        detalhe={"itens": itens, "faltantes": faltantes, "operador": operador},
    )


def _avaliar_experiencia(req: dict, ctx: ContextoCandidato) -> ResultadoRequisito:
    regra = req.get("regra") or {}
    anos = _num(regra.get("anos"))
    meses = _num(regra.get("meses"))
    minimo = anos if anos is not None else (meses / 12 if meses is not None else None)
    if minimo is None:
        achado = re.search(
            r"(\d+(?:[.,]\d+)?)\s*(anos?|meses?)", _norm(req.get("descricao", ""))
        )
        if achado:
            valor = float(achado.group(1).replace(",", "."))
            minimo = valor if achado.group(2).startswith("ano") else valor / 12
    if minimo is None:
        return _resultado(req, _status_faltante(req), "Tempo mínimo não especificado")
    dominio = regra.get("dominio")
    if not dominio and "energia" in _norm(req.get("descricao", "")):
        dominio = "energia"
    experiencias = _experiencias(ctx, dominio)
    if not experiencias:
        if ctx.experiencias:
            status = STATUS_NAO_ATENDIDO
            motivo = (
                f"Nenhuma experiência em {dominio} localizada"
                if dominio
                else "Nenhuma experiência localizada"
            )
        else:
            status = _status_faltante(req)
            motivo = "Nenhuma experiência declarada"
        return _resultado(req, status, motivo, detalhe={"minimo_anos": minimo})
    if not any(fato.inicio and fato.fim for _, fato in experiencias):
        return _resultado(
            req,
            STATUS_PENDENTE,
            "Experiência sem período suficiente para calcular o tempo",
            fatos=experiencias,
            detalhe={"minimo_anos": minimo},
        )
    total = anos_intervalos([fato for _, fato in experiencias])
    atende = total + 0.01 >= minimo
    status = STATUS_ATENDIDO if atende else STATUS_NAO_ATENDIDO
    motivo = f"{total:.1f} ano(s) calculado(s) contra mínimo de {minimo:.1f}"
    conf, atual, evidencia = _conf_atual(experiencias)
    return _resultado(
        req,
        status,
        motivo,
        confianca=conf,
        atualidade=atual,
        evidencia=evidencia,
        fatos=experiencias,
        detalhe={"minimo_anos": minimo, "anos_calculados": round(total, 2), "dominio": dominio},
    )


def _avaliar_certificacao(req: dict, ctx: ContextoCandidato) -> ResultadoRequisito:
    regra = req.get("regra") or {}
    itens = [str(item) for item in (regra.get("itens") or []) if item]
    if not itens:
        return _resultado(req, _status_faltante(req), "Certificação sem item identificado")
    encontrados, faltantes = _encontrar_em(itens, _certificacoes(ctx), "nome")
    quantidade = regra.get("quantidade")
    if quantidade is not None:
        atende = len({id(fato) for _, fato in encontrados}) >= int(quantidade)
    else:
        atende = not faltantes
    if atende:
        status = STATUS_ATENDIDO
        motivo = "Certificação(ões) encontrada(s)"
    elif encontrados:
        status = STATUS_NAO_ATENDIDO
        motivo = "Certificação(ões) insuficiente(s): " + ", ".join(faltantes)
    else:
        status = _status_faltante(req)
        motivo = "Certificação(ões) não localizada(s): " + ", ".join(faltantes)
    if atende and regra.get("comprovante"):
        estados = [getattr(fato, "evidencia_estado", "declarado") for _, fato in encontrados]
        if not any(estado == "validado" for estado in estados):
            if any(estado in ("rejeitado", "vencido") for estado in estados):
                status = STATUS_NAO_ATENDIDO
                motivo = "Comprovante da certificação rejeitado ou vencido"
            else:
                status = STATUS_PENDENTE
                motivo = "Certificação declarada, mas comprovante ainda não validado"
    conf, atual, evidencia = _conf_atual(encontrados)
    return _resultado(
        req,
        status,
        motivo,
        confianca=conf,
        atualidade=atual,
        evidencia=evidencia,
        fatos=encontrados,
        detalhe={"itens": itens, "faltantes": faltantes, "quantidade": quantidade},
    )


def _fatos_para_comprovante(
    item: str, ctx: ContextoCandidato
) -> list[tuple[str, Any]]:
    achados: list[tuple[str, Any]] = []
    for tipo, fato in _certificacoes(ctx):
        if _match_nome(item, fato.nome):
            achados.append((tipo, fato))
    for tipo, fato in _formacoes(ctx):
        if _match_nome(item, fato.curso) or _match_nome(item, fato.grau):
            achados.append((tipo, fato))
    for tipo, fato in _experiencias(ctx):
        if _match_nome(item, fato.resumo) or _match_nome(item, fato.dominio):
            achados.append((tipo, fato))
    return achados


def _avaliar_comprovante(req: dict, ctx: ContextoCandidato) -> ResultadoRequisito:
    regra = req.get("regra") or {}
    itens = [str(item) for item in (regra.get("itens") or []) if item]
    if not itens:
        return _resultado(req, _status_faltante(req), "Comprovante sem item identificado")
    detalhes: list[dict] = []
    todos_fatos: list[tuple[str, Any]] = []
    confiancas: list[float] = []
    atualidades: list[float] = []
    falhou = False
    pendente = False
    for item in itens:
        fatos = _fatos_para_comprovante(item, ctx)
        todos_fatos.extend(fatos)
        estados = [getattr(fato, "evidencia_estado", "declarado") for _, fato in fatos]
        if not fatos:
            item_status = STATUS_PENDENTE
            motivo = "Fato correspondente não localizado"
            conf = 0.0
            atual = 0.0
        elif any(estado == "validado" for estado in estados):
            item_status = STATUS_ATENDIDO
            motivo = "Comprovante validado"
            conf = 100.0
            atual = 100.0
        elif any(estado == "enviado" for estado in estados):
            item_status = STATUS_PENDENTE
            motivo = "Comprovante enviado, aguardando análise"
            conf = 50.0
            atual = 50.0
        elif any(estado in ("rejeitado", "vencido") for estado in estados):
            item_status = STATUS_NAO_ATENDIDO
            motivo = "Comprovante rejeitado ou vencido"
            conf = 0.0
            atual = 0.0
        else:
            item_status = STATUS_PENDENTE
            motivo = "Fato declarado sem comprovante"
            conf, atual, _ = _conf_atual(fatos)
        if item_status == STATUS_NAO_ATENDIDO:
            falhou = True
        elif item_status == STATUS_PENDENTE:
            pendente = True
        confiancas.append(conf)
        atualidades.append(atual)
        detalhes.append(
            {
                "item": item,
                "status": item_status,
                "motivo": motivo,
                "fatos": _fatos_resumo(fatos),
            }
        )
    if falhou:
        status = STATUS_NAO_ATENDIDO
        motivo = "Há comprovante obrigatório rejeitado ou vencido"
    elif pendente:
        status = _status_faltante(req)
        motivo = "Comprovação pendente para pelo menos um item"
    else:
        status = STATUS_ATENDIDO
        motivo = "Todos os comprovantes exigidos estão válidos"
    return _resultado(
        req,
        status,
        motivo,
        confianca=_media(confiancas),
        atualidade=_media(atualidades),
        evidencia="validado" if status == STATUS_ATENDIDO else "declarado",
        fatos=todos_fatos,
        detalhe={"itens": detalhes},
    )


def _avaliar_disponibilidade(
    ctx: ContextoCandidato,
    *,
    dedicacao: int | None = None,
    duracao_meses: int | None = None,
) -> tuple[str, str, float, float, dict]:
    disp = ctx.disponibilidade or {}
    estado = disp.get("estado") or "desconhecido"
    frescura = disp.get("frescura") or "sem_confirmacao"
    detalhe = {
        "estado": estado,
        "frescura": frescura,
        "inicio": disp.get("inicio"),
        "fim": disp.get("fim"),
        "percentual": disp.get("percentual"),
        "dedicacao": dedicacao,
        "duracao_meses": duracao_meses,
    }
    if estado == "desconhecido":
        return STATUS_PENDENTE, "Disponibilidade não informada", 0.0, 0.0, detalhe
    if estado == "vencido" or frescura in ("vencido", "sem_confirmacao", "desatualizado"):
        return (
            STATUS_PENDENTE,
            "Disponibilidade antiga ou vencida; precisa de reconfirmação",
            0.0,
            FRESCURA_FATOR.get(frescura, 0.0) * 100,
            detalhe,
        )
    if estado == "declarado":
        return (
            STATUS_PENDENTE,
            "Disponibilidade declarada, mas não confirmada",
            50.0,
            50.0,
            detalhe,
        )
    percentual = disp.get("percentual")
    if percentual is not None and int(percentual) <= 0:
        return STATUS_NAO_ATENDIDO, "Disponibilidade zerada", 100.0, 100.0, detalhe
    if dedicacao is not None and percentual is not None and int(percentual) < int(dedicacao):
        return (
            STATUS_NAO_ATENDIDO,
            f"Disponibilidade de {percentual}% menor que a dedicação exigida de {dedicacao}%",
            100.0,
            100.0,
            detalhe,
        )
    inicio = _data(disp.get("inicio"))
    fim = _data(disp.get("fim"))
    if duracao_meses and inicio and fim:
        meses = (fim - inicio).days / 30.0
        detalhe["meses_disponiveis"] = round(meses, 2)
        if meses + 0.01 < duracao_meses:
            return (
                STATUS_NAO_ATENDIDO,
                f"Disponibilidade de {meses:.1f} mês(es) menor que {duracao_meses}",
                100.0,
                100.0,
                detalhe,
            )
    return STATUS_ATENDIDO, "Disponibilidade confirmada", 100.0, 100.0, detalhe


def _avaliar_periodo(req: dict, ctx: ContextoCandidato) -> ResultadoRequisito:
    regra = req.get("regra") or {}
    duracao = regra.get("duracao_meses")
    dedicacao = regra.get("dedicacao")
    status, motivo, conf, atual, detalhe = _avaliar_disponibilidade(
        ctx,
        dedicacao=int(dedicacao) if dedicacao is not None else None,
        duracao_meses=int(duracao) if duracao is not None else None,
    )
    return _resultado(
        req,
        status,
        motivo,
        confianca=conf,
        atualidade=atual,
        evidencia="declarado",
        detalhe=detalhe,
    )


def _nivel_idioma(nivel: str | None) -> float:
    return NIVEIS_IDIOMA.get(_norm(nivel), 0.2)


def _valor_formacao(grau: str | None, curso: str | None) -> float:
    texto = _norm(f"{grau or ''} {curso or ''}")
    if "doutorado" in texto or "phd" in texto:
        return 1.0
    if "mestrado" in texto or "master" in texto:
        return 0.9
    if any(item in texto for item in ("pos graduacao", "especializacao", "mba")):
        return 0.8
    if any(item in texto for item in ("superior", "graduacao", "bacharel", "licenciatura")):
        return 0.7
    if "tecnico" in texto:
        return 0.5
    if "medio" in texto:
        return 0.3
    return 0.2


def _alvo_textual(n: str, chaves: tuple[str, ...]) -> str | None:
    """Extrai o alvo de um critério sem depender da taxonomia fixa.

    Exemplos: "20 pontos para certificação PMP" -> "pmp";
    "10 pontos por competência em gestão de projetos" -> "gestao de projetos".
    """
    for chave in chaves:
        if chave not in n:
            continue
        resto = n.split(chave, 1)[1]
        resto = re.sub(
            r"^(?:cao|coes|obrigatoria|obrigatorias|desejavel|desejaveis|"
            r"valida|validas|em|de|do|da|para|com|:|\s)+",
            "",
            resto,
        )
        resto = re.split(
            r"\s+(?:de|com)\s+\d|\s+minim|\s+maxim|\s+valida\b", resto
        )[0]
        alvo = resto.strip(" .,:;")
        if alvo:
            return alvo
    return None


def _inferir_metrica(texto: str) -> tuple[str | None, str | None, float | None]:
    n = _norm(texto)
    if "experi" in n:
        dominio = "energia" if "energia" in n else None
        if not dominio:
            achado_dominio = re.search(r"\bem\s+([a-z][a-z ]+)$", n)
            if achado_dominio and not re.search(r"\d", achado_dominio.group(1)):
                dominio = achado_dominio.group(1).strip()
        achado = re.search(r"(\d+(?:[.,]\d+)?)\s*anos?", n)
        return (
            "experiencia",
            dominio,
            float(achado.group(1).replace(",", ".")) if achado else None,
        )
    if "certifica" in n:
        alvo = _alvo_textual(n, ("certificacoes", "certificacao", "certifica"))
        if alvo:
            return "certificacao", alvo, None
        for canonical, aliases in CERTIFICACOES.items():
            if any(_match_nome(alias, n) for alias in aliases):
                return "certificacao", canonical, None
    for canonical, aliases in CERTIFICACOES.items():
        if any(_match_nome(alias, n) for alias in aliases):
            return "certificacao", canonical, None
    if "competencia" in n or "conhecimento" in n:
        alvo = _alvo_textual(n, ("competencias", "competencia", "conhecimento"))
        if alvo:
            return "competencia", alvo, None
        for canonical, aliases in COMPETENCIAS.items():
            if any(_match_nome(alias, n) for alias in aliases):
                return "competencia", canonical, None
    for canonical, aliases in COMPETENCIAS.items():
        if any(_match_nome(alias, n) for alias in aliases):
            return "competencia", canonical, None
    if "ingles" in n or "idioma" in n:
        alvo = "Inglês" if "ingles" in n else None
        minimo = None
        if any(item in n for item in ("fluente", "nativo", "avancado")):
            minimo = 1.0
        elif "intermediario" in n:
            minimo = 0.6
        elif "basico" in n:
            minimo = 0.3
        return "idioma", alvo, minimo
    if any(item in n for item in ("formacao", "graduacao", "pos graduacao", "mba", "mestrado", "doutorado")):
        return "formacao", None, None
    if "disponibilidade" in n:
        return "disponibilidade", None, None
    return None, None, None


def _avaliar_metrica(
    metrica: str | None,
    alvo: str | None,
    minimo: float | None,
    ctx: ContextoCandidato,
) -> tuple[float | None, str, str, list[tuple[str, Any]], dict]:
    if metrica == "experiencia":
        fatos = _experiencias(ctx, alvo)
        if not fatos:
            if ctx.experiencias:
                return 0.0, STATUS_NAO_ATENDIDO, "Experiência não localizada", [], {}
            return None, STATUS_PENDENTE, "Nenhuma experiência declarada", [], {}
        total = anos_intervalos([fato for _, fato in fatos])
        if minimo and minimo > 0:
            atendimento = min(1.0, total / minimo)
            status = STATUS_ATENDIDO if total + 0.01 >= minimo else STATUS_NAO_ATENDIDO
            motivo = f"{total:.1f} ano(s) de experiência contra referência de {minimo:.1f}"
        else:
            atendimento = 1.0
            status = STATUS_ATENDIDO
            motivo = f"{total:.1f} ano(s) de experiência"
        return atendimento, status, motivo, fatos, {
            "anos": round(total, 2),
            "minimo": minimo,
            "dominio": alvo,
        }
    if metrica == "competencia":
        fatos = _competencias(ctx)
        if not fatos:
            return None, STATUS_PENDENTE, "Nenhuma competência declarada", [], {}
        achados = [
            (tipo, fato)
            for tipo, fato in fatos
            if alvo is None or _match_nome(alvo, fato.nome)
        ]
        if achados:
            return 1.0, STATUS_ATENDIDO, f"Competência {alvo or ''} localizada", achados, {}
        return 0.0, STATUS_NAO_ATENDIDO, f"Competência {alvo or ''} não localizada", [], {}
    if metrica == "certificacao":
        fatos = _certificacoes(ctx)
        if not fatos:
            return None, STATUS_PENDENTE, "Nenhuma certificação declarada", [], {}
        achados = [
            (tipo, fato)
            for tipo, fato in fatos
            if alvo is None or _match_nome(alvo, fato.nome)
        ]
        if achados:
            return 1.0, STATUS_ATENDIDO, f"Certificação {alvo or ''} localizada", achados, {}
        return 0.0, STATUS_NAO_ATENDIDO, f"Certificação {alvo or ''} não localizada", [], {}
    if metrica == "formacao":
        fatos = _formacoes(ctx)
        if not fatos:
            return None, STATUS_PENDENTE, "Nenhuma formação declarada", [], {}
        valores = [
            (_valor_formacao(fato.grau, fato.curso), tipo, fato)
            for tipo, fato in fatos
        ]
        valor, _, fato = max(valores, key=lambda item: item[0])
        status = STATUS_ATENDIDO if valor >= 0.5 else STATUS_NAO_ATENDIDO
        return valor, status, f"Formação compatível (valor {valor:.2f})", [("formacao", fato)], {}
    if metrica == "idioma":
        fatos = _idiomas(ctx)
        if not fatos:
            return None, STATUS_PENDENTE, "Nenhum idioma declarado", [], {}
        candidatos = [
            (tipo, fato)
            for tipo, fato in fatos
            if alvo is None or _match_nome(alvo, fato.idioma)
        ]
        if not candidatos:
            return 0.0, STATUS_NAO_ATENDIDO, f"Idioma {alvo or ''} não localizado", [], {}
        valor = max(_nivel_idioma(fato.nivel) for _, fato in candidatos)
        if minimo is not None:
            status = STATUS_ATENDIDO if valor + 0.01 >= minimo else STATUS_NAO_ATENDIDO
        else:
            status = STATUS_ATENDIDO if valor > 0.2 else STATUS_NAO_ATENDIDO
        return valor, status, f"Nível de idioma {valor:.2f}", candidatos, {}
    if metrica == "disponibilidade":
        status, motivo, conf, atual, detalhe = _avaliar_disponibilidade(ctx)
        if status == STATUS_PENDENTE:
            return None, status, motivo, [], detalhe
        return (1.0 if status == STATUS_ATENDIDO else 0.0), status, motivo, [], detalhe
    return None, STATUS_PENDENTE, "Critério de pontuação exige revisão manual", [], {}


def _avaliar_pontuacao(req: dict, ctx: ContextoCandidato) -> ResultadoRequisito:
    regra = req.get("regra") or {}
    pontos = _num(regra.get("pontos"))
    if pontos is None:
        return _resultado(
            req,
            STATUS_PENDENTE,
            "Pontuação não especificada no requisito",
            detalhe={"criterio": regra.get("criterio") or req.get("descricao")},
        )
    criterio = str(regra.get("criterio") or req.get("descricao") or "")
    metrica = regra.get("metrica")
    alvo = regra.get("alvo")
    minimo = _num(regra.get("minimo"))
    if not metrica:
        metrica, alvo_inferido, minimo_inferido = _inferir_metrica(criterio)
        alvo = alvo or alvo_inferido
        minimo = minimo if minimo is not None else minimo_inferido
    atendimento, status, motivo, fatos, detalhe = _avaliar_metrica(
        metrica, alvo, minimo, ctx
    )
    teto = _num(regra.get("teto"))
    pontos_obtidos = 0.0
    if atendimento is not None:
        pontos_obtidos = round(pontos * max(0.0, min(1.0, atendimento)), 2)
        if teto is not None:
            pontos_obtidos = min(pontos_obtidos, teto)
    conf, atual, evidencia = _conf_atual(fatos)
    if status == STATUS_PENDENTE:
        motivo = f"{motivo}; pontuação não atribuída"
    elif status == STATUS_NAO_ATENDIDO:
        motivo = f"{motivo}; 0 de {pontos:.2f} ponto(s)"
    else:
        motivo = f"{motivo}; {pontos_obtidos:.2f} de {pontos:.2f} ponto(s)"
    return _resultado(
        req,
        status,
        motivo,
        pontos_possiveis=pontos,
        pontos_obtidos=pontos_obtidos,
        atendimento=atendimento,
        confianca=conf,
        atualidade=atual,
        evidencia=evidencia,
        fatos=fatos,
        detalhe={
            "criterio": criterio,
            "metrica": metrica,
            "alvo": alvo,
            "minimo": minimo,
            "teto": teto,
            **detalhe,
        },
    )


def _base_eliminatoria(
    req: dict, ctx: ContextoCandidato
) -> tuple[bool | None, str, list[tuple[str, Any]], dict]:
    regra = req.get("regra") or {}
    texto = f"{req.get('descricao', '')} {regra.get('texto', '')}"
    n = _norm(texto)
    tipo = regra.get("tipo")
    if tipo == "elegivel_brasil" or ("brasil" in n and "elegivel" in n):
        if ctx.elegivel_brasil == "sim":
            return True, "Elegibilidade para o Brasil confirmada", [], {}
        if ctx.elegivel_brasil == "nao":
            return False, "Candidato não elegível para o Brasil", [], {}
        return None, "Elegibilidade para o Brasil não informada", [], {}
    if tipo == "experiencia_minima" or ("experiencia" in n and re.search(r"\d", n)):
        resultado = _avaliar_experiencia(req, ctx)
        return (
            resultado.status == STATUS_ATENDIDO,
            resultado.motivo,
            [],
            resultado.detalhe,
        )
    if tipo in ("certificacao", "competencia"):
        itens = regra.get("itens") or ([regra["alvo"]] if regra.get("alvo") else [])
        if not itens:
            return None, "Critério sem item identificado", [], {}
        fatos = _certificacoes(ctx) if tipo == "certificacao" else _competencias(ctx)
        atributo = "nome"
        encontrados, faltantes = _encontrar_em(
            [str(item) for item in itens], fatos, atributo
        )
        if encontrados:
            return True, "Critério localizado no currículo", encontrados, {}
        if fatos:
            return False, "Critério não localizado no currículo", [], {}
        return None, "Currículo sem fatos suficientes para avaliar", [], {}
    if tipo == "idioma" or "ingles" in n or "idioma" in n:
        alvo = regra.get("alvo") or ("Inglês" if "ingles" in n else None)
        minimo = _num(regra.get("minimo"))
        if minimo is None:
            minimo = 1.0 if any(item in n for item in ("fluente", "avancado", "nativo")) else None
        valor, status, motivo, fatos, detalhe = _avaliar_metrica(
            "idioma", alvo, minimo, ctx
        )
        if status == STATUS_PENDENTE:
            return None, motivo, fatos, detalhe
        return valor is not None and valor > 0.2, motivo, fatos, detalhe
    if tipo == "formacao" or any(
        item in n for item in ("formacao", "graduacao", "pos graduacao", "mba", "mestrado", "doutorado")
    ):
        valor, status, motivo, fatos, detalhe = _avaliar_metrica(
            "formacao", None, None, ctx
        )
        if status == STATUS_PENDENTE:
            return None, motivo, fatos, detalhe
        return (valor or 0) >= 0.5, motivo, fatos, detalhe
    if tipo in ("disponibilidade", "viagens") or "disponibilidade" in n:
        if "viagem" in n or tipo == "viagens":
            return None, "Disponibilidade para viagens não é um fato estruturado; revisão manual necessária", [], {}
        status, motivo, _, _, detalhe = _avaliar_disponibilidade(ctx)
        if status == STATUS_PENDENTE:
            return None, motivo, [], detalhe
        return status == STATUS_ATENDIDO, motivo, [], detalhe
    if tipo == "manual":
        return None, "Critério marcado para avaliação manual", [], {}
    return None, "Critério eliminatório exige revisão manual", [], {}


def _avaliar_eliminatorio(req: dict, ctx: ContextoCandidato) -> ResultadoRequisito:
    regra = req.get("regra") or {}
    base, motivo, fatos, detalhe = _base_eliminatoria(req, ctx)
    negado = bool(regra.get("negado"))
    if base is None:
        status = STATUS_PENDENTE
    elif negado:
        status = STATUS_NAO_ATENDIDO if base else STATUS_ATENDIDO
    else:
        status = STATUS_ATENDIDO if base else STATUS_NAO_ATENDIDO
    conf, atual, evidencia = _conf_atual(fatos)
    return _resultado(
        req,
        status,
        motivo,
        confianca=conf,
        atualidade=atual,
        evidencia=evidencia,
        fatos=fatos,
        detalhe={**detalhe, "negado": negado},
    )


def _avaliar_coletivo(req: dict, ctx: ContextoCandidato) -> ResultadoRequisito:
    regra = req.get("regra") or {}
    tipo = req.get("tipo")
    if tipo == "regra_combinacao":
        motivo = "Regra de combinação avaliada na composição da equipe (Etapa 6)"
    elif req.get("escopo") == "empresa":
        motivo = "Critério de empresa; não pontua individualmente"
    elif req.get("escopo") == "equipe":
        motivo = "Critério de equipe; não pontua individualmente"
    else:
        motivo = "Requisito coletivo sem avaliação individual"
    return _resultado(
        req,
        STATUS_PENDENTE,
        motivo,
        detalhe={"coletivo": True, "regra": regra},
    )


def _avaliar_requisito(req: dict, ctx: ContextoCandidato) -> ResultadoRequisito:
    regra = req.get("regra") or {}
    manual = regra.get("resultado")
    if manual in (STATUS_ATENDIDO, STATUS_NAO_ATENDIDO, STATUS_PENDENTE):
        return _resultado(
            req,
            manual,
            regra.get("motivo") or "Resultado definido na revisão humana",
            detalhe={"manual": True},
        )
    if regra.get("tipo") == "dispensado":
        return _resultado(
            req,
            STATUS_ATENDIDO,
            "Requisito dispensado na revisão humana",
            detalhe={"manual": True, "dispensado": True},
        )
    tipo = req.get("tipo")
    if tipo == "competencia":
        return _avaliar_competencia(req, ctx)
    if tipo == "experiencia_minima":
        return _avaliar_experiencia(req, ctx)
    if tipo == "certificacao":
        return _avaliar_certificacao(req, ctx)
    if tipo == "comprovante":
        return _avaliar_comprovante(req, ctx)
    if tipo == "periodo_trabalho":
        return _avaliar_periodo(req, ctx)
    if tipo == "criterio_eliminatorio":
        return _avaliar_eliminatorio(req, ctx)
    if tipo == "pontuacao":
        return _avaliar_pontuacao(req, ctx)
    if tipo in TIPOS_INFORMATIVOS:
        return _resultado(req, STATUS_ATENDIDO, "Requisito informativo da demanda")
    return _resultado(
        req,
        STATUS_PENDENTE,
        "Tipo de requisito ainda não interpretado pelo motor",
        detalhe={"regra": req.get("regra") or {}},
    )


def avaliar_candidato_papel(
    ctx: ContextoCandidato, demanda: dict, papel: dict
) -> ResultadoDemanda:
    versao = demanda["versao"]
    requisitos: list[ResultadoRequisito] = []
    coletivos: list[ResultadoRequisito] = []
    for req in demanda["requisitos"]:
        if req["tipo"] in ("papel", "vaga") or not _aplicar_requisito(req, papel):
            continue
        if req["escopo"] != "individual":
            coletivos.append(_avaliar_coletivo(req, ctx))
            continue
        requisitos.append(_avaliar_requisito(req, ctx))

    bloqueantes = [
        item
        for item in requisitos
        if item.obrigatorio and item.tipo not in TIPOS_INFORMATIVOS
    ]
    if any(
        item.status in (STATUS_NAO_ATENDIDO, STATUS_ELIMINADO) for item in bloqueantes
    ):
        status = STATUS_ELIMINADO
    elif any(item.status == STATUS_PENDENTE for item in bloqueantes):
        status = STATUS_PENDENTE
    elif not bloqueantes:
        status = STATUS_PENDENTE
    else:
        status = "elegivel"

    nota_tecnica = round(
        sum(item.pontos_obtidos for item in requisitos if item.tipo == "pontuacao"), 2
    )
    pontuacao_maxima = round(
        sum(
            item.pontos_possiveis
            for item in requisitos
            if item.tipo == "pontuacao"
        ),
        2,
    )
    pesos = [
        max(1.0, item.pontos_possiveis)
        for item in requisitos
        if item.tipo not in TIPOS_INFORMATIVOS
    ]
    confianca = _media(
        [item.confianca for item in requisitos if item.tipo not in TIPOS_INFORMATIVOS],
        pesos,
    )
    atualidade = _media(
        [item.atualidade for item in requisitos if item.tipo not in TIPOS_INFORMATIVOS],
        pesos,
    )
    if bloqueantes:
        cobertura = 100 * sum(
            1 for item in bloqueantes if item.status == STATUS_ATENDIDO
        ) / len(bloqueantes)
    else:
        cobertura = 100.0
    score_interno = round(
        0.5 * cobertura + 0.3 * confianca + 0.2 * atualidade, 2
    )
    ancoras = [
        {
            "requisito_id": item.requisito_id,
            "descricao": item.descricao,
            "pontos": item.pontos_obtidos,
            "motivo": item.motivo,
        }
        for item in requisitos
        if item.tipo == "pontuacao" and item.pontos_obtidos > 0
    ]
    contagens = {
        "atendidos": sum(1 for item in requisitos if item.status == STATUS_ATENDIDO),
        "pendentes": sum(1 for item in requisitos if item.status == STATUS_PENDENTE),
        "nao_atendidos": sum(
            1 for item in requisitos if item.status == STATUS_NAO_ATENDIDO
        ),
        "obrigatorios": len(bloqueantes),
        "obrigatorios_atendidos": sum(
            1 for item in bloqueantes if item.status == STATUS_ATENDIDO
        ),
        "obrigatorios_pendentes": sum(
            1 for item in bloqueantes if item.status == STATUS_PENDENTE
        ),
        "obrigatorios_nao_atendidos": sum(
            1
            for item in bloqueantes
            if item.status in (STATUS_NAO_ATENDIDO, STATUS_ELIMINADO)
        ),
    }
    detalhe = {
        "status": status,
        "status_gates": status,
        "nota_tecnica": nota_tecnica,
        "pontuacao_maxima": pontuacao_maxima,
        "score_interno_apoio": score_interno,
        "confianca": confianca,
        "atualidade": atualidade,
        "cobertura_obrigatorios": round(cobertura, 2),
        "contagens": contagens,
        "ancoras": ancoras,
        "coletivos": [item.como_dict() for item in coletivos],
        "edital_versao_id": demanda["versao_id"],
        "edital_numero": versao.get("numero"),
        "edital_nome": versao.get("edital_nome"),
        "data_referencia": (ctx.referencia or date.today()).isoformat(),
        "anos_experiencia": round(ctx.anos_experiencia, 2),
        "evidencia_max": ctx.evidencia_max,
        "fonte_nota": "edital",
        "confianca_heuristica": (
            "confiança heurística; não é probabilidade estatística"
        ),
        "formula": (
            "nota_tecnica = soma dos pontos do edital; "
            "score_interno_apoio = 50% cobertura obrigatória + "
            "30% confiança + 20% atualidade"
        ),
    }
    return ResultadoDemanda(
        candidato_id=ctx.candidato_id,
        curriculo_id=ctx.curriculo_id,
        edital_versao_id=demanda["versao_id"],
        papel_id=papel["papel_id"],
        papel=papel["sigla"],
        papel_nome=papel["nome"],
        vaga=papel.get("vagas"),
        nota_tecnica=nota_tecnica,
        pontuacao_maxima=pontuacao_maxima,
        score_interno_apoio=score_interno,
        confianca=confianca,
        atualidade=atualidade,
        status=status,
        requisitos=requisitos,
        coletivos=coletivos,
        detalhe=detalhe,
    )


def upsert_pontuacao_demanda(con, resultado: ResultadoDemanda, disputa_id: int) -> None:
    detalhe = dict(resultado.detalhe)
    detalhe.update(
        {
            "candidato_id": resultado.candidato_id,
            "curriculo_id": resultado.curriculo_id,
            "edital_versao_id": resultado.edital_versao_id,
            "papel_id": resultado.papel_id,
            "papel": resultado.papel,
            "papel_nome": resultado.papel_nome,
            "vaga": resultado.vaga,
            "nota_tecnica": round(resultado.nota_tecnica, 2),
            "pontuacao_maxima": round(resultado.pontuacao_maxima, 2),
            "score_interno_apoio": round(resultado.score_interno_apoio, 2),
            "confianca": round(resultado.confianca, 2),
            "atualidade": round(resultado.atualidade, 2),
            "status": resultado.status,
            "requisitos": [item.como_dict() for item in resultado.requisitos],
            "coletivos": [item.como_dict() for item in resultado.coletivos],
        }
    )
    con.execute(
        """
        INSERT INTO pontuacoes_demanda (
            disputa_id, candidato_id, papel_id, edital_versao_id,
            curriculo_versao_id, nota_tecnica, score_interno_apoio,
            confianca, atualidade, status, detalhe_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (disputa_id, candidato_id, papel_id, edital_versao_id,
                     curriculo_versao_id)
        DO UPDATE SET
            nota_tecnica = excluded.nota_tecnica,
            score_interno_apoio = excluded.score_interno_apoio,
            confianca = excluded.confianca,
            atualidade = excluded.atualidade,
            status = excluded.status,
            detalhe_json = excluded.detalhe_json,
            criado_em = datetime('now')
        """,
        (
            disputa_id,
            resultado.candidato_id,
            resultado.papel_id,
            resultado.edital_versao_id,
            resultado.curriculo_id,
            resultado.nota_tecnica,
            resultado.score_interno_apoio,
            resultado.confianca,
            resultado.atualidade,
            resultado.status,
            json.dumps(detalhe, ensure_ascii=False),
        ),
    )


def _ultimo_curriculo(con, candidato_id: int):
    return con.execute(
        "SELECT * FROM curriculos WHERE candidato_id = ?"
        " ORDER BY versao DESC, id DESC LIMIT 1",
        (candidato_id,),
    ).fetchone()


def recalcular_candidato_demanda(
    con,
    candidato_id: int,
    disputa_id: int | None = None,
    *,
    ator: str = "ui",
    commit: bool = True,
) -> list[ResultadoDemanda]:
    if disputa_id is None:
        row = con.execute(
            "SELECT * FROM disputas WHERE edital_versao_id IS NOT NULL"
            " ORDER BY CASE status WHEN 'aberta' THEN 0 ELSE 1 END, id DESC"
            " LIMIT 1"
        ).fetchone()
    else:
        row = con.execute(
            "SELECT * FROM disputas WHERE id = ?", (disputa_id,)
        ).fetchone()
    if row is None or row["edital_versao_id"] is None:
        return []
    curriculo = _ultimo_curriculo(con, candidato_id)
    if curriculo is None:
        return []
    demanda = carregar_demanda(con, int(row["edital_versao_id"]))
    ctx = carregar_contexto(con, int(curriculo["id"]))
    resultados = [
        avaliar_candidato_papel(ctx, demanda, papel) for papel in demanda["papeis"]
    ]
    for resultado in resultados:
        upsert_pontuacao_demanda(con, resultado, int(row["id"]))
    registrar(
        con,
        acao="recalcular_score_demanda_candidato",
        ator=ator,
        entidade="candidatos",
        entidade_id=candidato_id,
        detalhes={
            "disputa_id": int(row["id"]),
            "edital_versao_id": int(row["edital_versao_id"]),
            "curriculo_id": int(curriculo["id"]),
            "papeis": [resultado.papel for resultado in resultados],
        },
        commit=False,
    )
    if commit:
        con.commit()
    return resultados


def recalcular_disputa_demanda(
    con,
    disputa_id: int,
    *,
    edital_versao_id: int | None = None,
    ator: str = "ui",
    commit: bool = True,
) -> dict:
    disputa = con.execute(
        "SELECT * FROM disputas WHERE id = ?", (disputa_id,)
    ).fetchone()
    if disputa is None:
        raise KeyError("disputa não encontrada")
    versao_id = edital_versao_id or disputa["edital_versao_id"]
    if versao_id is None:
        raise ValueError("disputa sem versão de edital")
    demanda = carregar_demanda(con, int(versao_id))
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
        papel["sigla"]: {"total": 0, "elegivel": 0, "pendente": 0, "eliminado": 0}
        for papel in demanda["papeis"]
    }
    gravadas = 0
    contextos = carregar_contextos(
        con, [int(candidato["curriculo_id"]) for candidato in candidatos]
    )
    for candidato in candidatos:
        ctx = contextos.get(int(candidato["curriculo_id"]))
        if ctx is None:
            continue
        for papel in demanda["papeis"]:
            resultado = avaliar_candidato_papel(ctx, demanda, papel)
            upsert_pontuacao_demanda(con, resultado, disputa_id)
            resumo[papel["sigla"]]["total"] += 1
            resumo[papel["sigla"]][resultado.status] += 1
            gravadas += 1
    registrar(
        con,
        acao="calcular_pontuacoes_demanda",
        ator=ator,
        entidade="disputas",
        entidade_id=disputa_id,
        detalhes={
            "edital_versao_id": int(versao_id),
            "pontuacoes": gravadas,
            "por_papel": resumo,
        },
        commit=False,
    )
    if commit:
        con.commit()
    return {
        "disputa_id": disputa_id,
        "edital_versao_id": int(versao_id),
        "pontuacoes": gravadas,
        "por_papel": resumo,
    }


def listar_resultados_demanda(
    con,
    disputa_id: int,
    edital_versao_id: int | None = None,
    *,
    candidato_id: int | None = None,
) -> list[dict]:
    if edital_versao_id is None:
        row = con.execute(
            "SELECT edital_versao_id FROM disputas WHERE id = ?", (disputa_id,)
        ).fetchone()
        edital_versao_id = row["edital_versao_id"] if row else None
    if edital_versao_id is None:
        return []
    where = [
        "pd.disputa_id = ?",
        "pd.edital_versao_id = ?",
        "pd.curriculo_versao_id = ("
        " SELECT MAX(id) FROM curriculos WHERE candidato_id = pd.candidato_id"
        ")",
    ]
    params: list[Any] = [disputa_id, edital_versao_id]
    if candidato_id is not None:
        where.append("pd.candidato_id = ?")
        params.append(candidato_id)
    rows = con.execute(
        "SELECT pd.*, p.sigla AS papel, p.nome AS papel_nome,"
        " c.nome_pseudonimo, c.status_curacao, cur.versao AS curriculo_versao,"
        " cur.data_ingestao"
        " FROM pontuacoes_demanda pd"
        " JOIN papeis p ON p.id = pd.papel_id"
        " JOIN candidatos c ON c.id = pd.candidato_id"
        " JOIN curriculos cur ON cur.id = pd.curriculo_versao_id"
        " WHERE " + " AND ".join(where) +
        " ORDER BY pd.nota_tecnica DESC, pd.score_interno_apoio DESC, pd.id",
        params,
    ).fetchall()
    resultado: list[dict] = []
    for row in rows:
        item = dict(row)
        detalhe = json.loads(row["detalhe_json"] or "{}")
        if isinstance(detalhe.get("detalhe"), dict):
            # compatibilidade com o primeiro formato gravado na Etapa 5
            interno = detalhe.pop("detalhe")
            detalhe = {**interno, **detalhe}
        item["detalhe"] = detalhe
        resultado.append(item)
    return resultado


def obter_resultado_demanda(
    con,
    disputa_id: int,
    candidato_id: int,
    edital_versao_id: int | None = None,
) -> list[dict]:
    return listar_resultados_demanda(
        con, disputa_id, edital_versao_id, candidato_id=candidato_id
    )
