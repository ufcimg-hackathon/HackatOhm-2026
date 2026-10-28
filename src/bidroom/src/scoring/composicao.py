"""Etapa 6 — composição viável do time para uma demanda com edital.

A recomendação não é apenas "os maiores scores por vaga". Ela resolve uma
atribuição com vagas, exclusividade de candidato, limites por papel e
requisitos coletivos (equipe/empresa). O objetivo é explícito:

    objetivo = soma da nota técnica
             + bônus por requisito coletivo atendido
             - penalidade por requisito coletivo não atendido

Requisitos coletivos sem fato estruturado ficam pendentes e não são escondidos.
Candidatos eliminados ou pendentes não preenchem vagas automaticamente.
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from src.extraction.regras import CERTIFICACOES, COMPETENCIAS
from src.scoring.contexto import carregar_contextos
from src.scoring.demanda import carregar_demanda, listar_resultados_demanda

PESO_COLETIVO = 10.0
LIMITE_BUSCA = 200_000
LIMITE_CANDIDATOS_PAPEL = 14


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


def _inteiro(texto: str) -> int | None:
    palavras = {"um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3}
    if texto in palavras:
        return palavras[texto]
    try:
        return int(texto)
    except ValueError:
        return None


def _minimo_coletivo(texto: str) -> int:
    n = _norm(texto)
    achado = re.search(
        r"(?:pelo menos|minimo de|no minimo)\s+(\d+|um|uma|dois|duas|tres)\b",
        n,
    )
    if achado:
        return _inteiro(achado.group(1)) or 1
    return 1


def _alvos_coletivos(texto: str) -> list[tuple[str, str]]:
    """Detecta certificações/competências citadas em uma regra coletiva."""
    n = _norm(texto)
    alvos: list[tuple[str, str]] = []
    for canonical, aliases in CERTIFICACOES.items():
        if any(_match_nome(alias, n) for alias in aliases):
            alvos.append(("certificacao", canonical))
    for canonical, aliases in COMPETENCIAS.items():
        if any(_match_nome(alias, n) for alias in aliases):
            alvos.append(("competencia", canonical))
    if alvos:
        vistos: set[tuple[str, str]] = set()
        return [item for item in alvos if not (item in vistos or vistos.add(item))]
    for chave, tipo in (
        ("certificacao", "certificacao"),
        ("certificado", "certificacao"),
        ("competencia", "competencia"),
        ("conhecimento", "competencia"),
    ):
        if chave not in n:
            continue
        resto = n.split(chave, 1)[1]
        resto = re.sub(
            r"^(?:cao|coes|obrigatoria|obrigatorias|desejavel|desejaveis|"
            r"em|de|do|da|para|com|:|\s)+",
            "",
            resto,
        )
        resto = re.split(r"\s+(?:de|com)\s+\d|\s+minim|\s+maxim", resto)[0]
        alvo = resto.strip(" .,:;")
        if alvo:
            return [(tipo, alvo)]
    return []


@dataclass
class VagaDemanda:
    papel_id: int
    papel: str
    papel_nome: str
    vaga: int
    quantidade: int


@dataclass
class CandidatoComposicao:
    candidato_id: int
    nome: str
    curriculo_id: int
    papel_id: int
    papel: str
    papel_nome: str
    nota_tecnica: float
    score_interno_apoio: float
    confianca: float
    atualidade: float
    status: str
    detalhe: dict = field(default_factory=dict)
    certificacoes: set[str] = field(default_factory=set)
    competencias: set[str] = field(default_factory=set)
    conflito_disputa_id: int | None = None

    def como_dict(self) -> dict:
        return {
            "candidato_id": self.candidato_id,
            "nome": self.nome,
            "curriculo_id": self.curriculo_id,
            "papel_id": self.papel_id,
            "papel": self.papel,
            "papel_nome": self.papel_nome,
            "nota_tecnica": self.nota_tecnica,
            "score_interno_apoio": self.score_interno_apoio,
            "confianca": self.confianca,
            "atualidade": self.atualidade,
            "status": self.status,
            "ancoras": self.detalhe.get("ancoras", []),
            "conflito_disputa_id": self.conflito_disputa_id,
        }


@dataclass
class RequisitoColetivo:
    requisito_id: int
    tipo: str
    alvo: str | None
    minimo: int
    texto: str
    escopo: str
    peso: float = PESO_COLETIVO


@dataclass
class Composicao:
    disputa_id: int
    edital_versao_id: int
    referencia: str
    vagas: list[VagaDemanda]
    slots: list[dict]
    nota_total: float
    bonus_coletivo: float
    penalidade_coletiva: float
    objetivo: float
    objetivo_guloso: float
    diferenca_guloso: float
    cobertura: float
    status: str
    requisitos_coletivos: list[dict]
    pendencias: list[str]
    riscos: list[dict]
    alternativas: dict[str, list[dict]]
    candidatos: list[CandidatoComposicao]
    busca_completa: bool = True

    def como_dict(self) -> dict:
        return {
            "disputa_id": self.disputa_id,
            "edital_versao_id": self.edital_versao_id,
            "referencia": self.referencia,
            "vagas": [vars(vaga) for vaga in self.vagas],
            "slots": self.slots,
            "nota_total": self.nota_total,
            "bonus_coletivo": self.bonus_coletivo,
            "penalidade_coletiva": self.penalidade_coletiva,
            "objetivo": self.objetivo,
            "objetivo_guloso": self.objetivo_guloso,
            "diferenca_guloso": self.diferenca_guloso,
            "cobertura": self.cobertura,
            "status": self.status,
            "requisitos_coletivos": self.requisitos_coletivos,
            "pendencias": self.pendencias,
            "riscos": self.riscos,
            "alternativas": self.alternativas,
            "candidatos": [c.como_dict() for c in self.candidatos],
            "busca_completa": self.busca_completa,
        }


def _conflito(con: sqlite3.Connection, candidato_id: int, disputa_id: int) -> int | None:
    row = con.execute(
        "SELECT a.disputa_id FROM alocacoes a"
        " JOIN disputas d ON d.id = a.disputa_id"
        " WHERE a.candidato_id = ? AND a.status = 'alocado'"
        " AND a.disputa_id != ? AND d.status = 'aberta'"
        " ORDER BY a.id DESC LIMIT 1",
        (candidato_id, disputa_id),
    ).fetchone()
    return int(row["disputa_id"]) if row else None


def _conflitos_por_candidato(
    con: sqlite3.Connection, disputa_id: int, candidato_ids: list[int]
) -> dict[int, int]:
    ids = list(dict.fromkeys(int(item) for item in candidato_ids))
    if not ids:
        return {}
    marcadores = ",".join("?" for _ in ids)
    rows = con.execute(
        "SELECT a.candidato_id, a.disputa_id FROM alocacoes a"
        " JOIN disputas d ON d.id = a.disputa_id"
        f" WHERE a.candidato_id IN ({marcadores})"
        " AND a.status = 'alocado' AND a.disputa_id != ?"
        " AND d.status = 'aberta'"
        " ORDER BY a.id DESC",
        [*ids, disputa_id],
    ).fetchall()
    conflitos: dict[int, int] = {}
    for row in rows:
        conflitos.setdefault(int(row["candidato_id"]), int(row["disputa_id"]))
    return conflitos


def _carregar_candidatos(
    con: sqlite3.Connection, disputa_id: int, edital_versao_id: int
) -> list[CandidatoComposicao]:
    rows = listar_resultados_demanda(con, disputa_id, edital_versao_id)
    if not rows:
        return []
    contextos = carregar_contextos(
        con, [int(row["curriculo_versao_id"]) for row in rows]
    )
    conflitos = _conflitos_por_candidato(
        con, disputa_id, [int(row["candidato_id"]) for row in rows]
    )
    candidatos: list[CandidatoComposicao] = []
    for row in rows:
        detalhe = row.get("detalhe") or {}
        ctx = contextos.get(int(row["curriculo_versao_id"]))
        certificacoes = {_norm(fato.nome) for fato in ctx.certificacoes} if ctx else set()
        competencias = {_norm(fato.nome) for fato in ctx.competencias} if ctx else set()
        candidatos.append(
            CandidatoComposicao(
                candidato_id=int(row["candidato_id"]),
                nome=str(row["nome_pseudonimo"]),
                curriculo_id=int(row["curriculo_versao_id"]),
                papel_id=int(row["papel_id"]),
                papel=str(row["papel"]),
                papel_nome=str(row["papel_nome"]),
                nota_tecnica=float(row["nota_tecnica"] or 0),
                score_interno_apoio=float(row["score_interno_apoio"] or 0),
                confianca=float(row["confianca"] or 0),
                atualidade=float(row["atualidade"] or 0),
                status=str(row["status"]),
                detalhe=detalhe,
                certificacoes=certificacoes,
                competencias=competencias,
                conflito_disputa_id=conflitos.get(int(row["candidato_id"])),
            )
        )
    return candidatos


def _vagas_demanda(demanda: dict) -> tuple[list[VagaDemanda], list[str]]:
    vagas: list[VagaDemanda] = []
    pendencias: list[str] = []
    for papel in demanda["papeis"]:
        quantidade = papel.get("vagas")
        if quantidade is None:
            quantidade = 1
            pendencias.append(
                f"Vagas de {papel['nome']} não definidas no edital; assumida 1."
            )
        quantidade = max(0, int(quantidade))
        for vaga in range(1, quantidade + 1):
            vagas.append(
                VagaDemanda(
                    papel_id=int(papel["papel_id"]),
                    papel=str(papel["sigla"]),
                    papel_nome=str(papel["nome"]),
                    vaga=vaga,
                    quantidade=quantidade,
                )
            )
    return vagas, pendencias


def _requisitos_coletivos(
    demanda: dict,
) -> tuple[list[RequisitoColetivo], dict[str, int], list[str]]:
    coletivos: list[RequisitoColetivo] = []
    limites: dict[str, int] = {}
    pendencias: list[str] = []
    for req in demanda["requisitos"]:
        regra = req.get("regra") or {}
        # O parser costuma repetir a linha em `descricao` e em `regra.texto`.
        # Preferir o texto estruturado evita duplicar o alvo da regra.
        texto = str(regra.get("texto") or req.get("descricao") or "").strip()
        n = _norm(texto)
        if req.get("tipo") == "limite":
            valor = regra.get("valor")
            if valor is None:
                achado = re.search(r"(\d+)", n)
                valor = int(achado.group(1)) if achado else None
            if valor is not None and "por papel" in n:
                limites["*"] = max(1, int(valor))
            continue
        if req.get("escopo") not in ("equipe", "empresa"):
            if req.get("tipo") != "regra_combinacao":
                continue
        if req.get("escopo") == "empresa":
            coletivos.append(
                RequisitoColetivo(
                    requisito_id=int(req["id"]),
                    tipo="manual",
                    alvo=None,
                    minimo=0,
                    texto=texto,
                    escopo="empresa",
                )
            )
            pendencias.append(
                "Requisito da empresa não verificável no acervo individual: "
                + req.get("descricao", "")
            )
            continue
        alvos = _alvos_coletivos(texto)
        if not alvos:
            coletivos.append(
                RequisitoColetivo(
                    requisito_id=int(req["id"]),
                    tipo="manual",
                    alvo=None,
                    minimo=0,
                    texto=texto,
                    escopo=req.get("escopo", "equipe"),
                )
            )
            pendencias.append(
                "Regra coletiva exige revisão manual: " + req.get("descricao", "")
            )
            continue
        minimo = _minimo_coletivo(texto)
        for tipo, alvo in alvos:
            coletivos.append(
                RequisitoColetivo(
                    requisito_id=int(req["id"]),
                    tipo=tipo,
                    alvo=alvo,
                    minimo=minimo,
                    texto=texto,
                    escopo=req.get("escopo", "equipe"),
                )
            )
    return coletivos, limites, pendencias


def _avaliar_coletivos(
    requisitos: list[RequisitoColetivo],
    selecionados: list[CandidatoComposicao],
) -> list[dict]:
    resultado: list[dict] = []
    for req in requisitos:
        if req.tipo == "manual":
            resultado.append(
                {
                    "requisito_id": req.requisito_id,
                    "tipo": "manual",
                    "alvo": None,
                    "minimo": 0,
                    "atendidos": 0,
                    "status": "pendente",
                    "motivo": "Requisito coletivo sem fato estruturado",
                    "texto": req.texto,
                    "escopo": req.escopo,
                    "peso": req.peso,
                }
            )
            continue
        if req.tipo == "certificacao":
            atendidos = sum(
                1
                for candidato in selecionados
                if any(_match_nome(req.alvo, cert) for cert in candidato.certificacoes)
            )
        elif req.tipo == "competencia":
            atendidos = sum(
                1
                for candidato in selecionados
                if any(_match_nome(req.alvo, comp) for comp in candidato.competencias)
            )
        else:
            atendidos = 0
        status = "atendido" if atendidos >= req.minimo else "nao_atendido"
        resultado.append(
            {
                "requisito_id": req.requisito_id,
                "tipo": req.tipo,
                "alvo": req.alvo,
                "minimo": req.minimo,
                "atendidos": atendidos,
                "status": status,
                "motivo": f"{atendidos} de {req.minimo} exigido(s)",
                "texto": req.texto,
                "escopo": req.escopo,
                "peso": req.peso,
            }
        )
    return resultado


def _limite_papel(limites: dict[str, int], papel: str) -> int:
    return int(limites.get("*", limites.get(papel, 999)))


def _avaliar_selecao(
    *,
    disputa_id: int,
    edital_versao_id: int,
    referencia: str,
    vagas: list[VagaDemanda],
    escolhidos: list[CandidatoComposicao | None],
    coletivos: list[RequisitoColetivo],
    limites: dict[str, int],
    candidatos: list[CandidatoComposicao],
    busca_completa: bool = True,
) -> Composicao:
    selecionados = [c for c in escolhidos if c is not None]
    nota_total = round(sum(c.nota_tecnica for c in selecionados), 2)
    coletivos_avaliados = _avaliar_coletivos(coletivos, selecionados)
    bonus = round(
        sum(item["peso"] for item in coletivos_avaliados if item["status"] == "atendido"),
        2,
    )
    penalidade = round(
        sum(
            item["peso"]
            for item in coletivos_avaliados
            if item["status"] == "nao_atendido"
        ),
        2,
    )
    objetivo = round(nota_total + bonus - penalidade, 2)
    total_vagas = len(vagas)
    cobertura = len(selecionados) / total_vagas if total_vagas else 0.0

    slots: list[dict] = []
    for vaga, candidato in zip(vagas, escolhidos):
        slots.append(
            {
                "papel_id": vaga.papel_id,
                "papel": vaga.papel,
                "papel_nome": vaga.papel_nome,
                "vaga": vaga.vaga,
                "candidato": candidato.como_dict() if candidato else None,
            }
        )

    pendencias: list[str] = []
    if len(selecionados) < total_vagas:
        pendencias.append(
            f"{total_vagas - len(selecionados)} vaga(s) descoberta(s) sem candidato elegível."
        )
    for item in coletivos_avaliados:
        if item["status"] == "pendente":
            pendencias.append(item["texto"] or "Requisito coletivo pendente.")
        elif item["status"] == "nao_atendido":
            pendencias.append(
                f"Requisito coletivo não atendido: {item['texto']} "
                f"({item['motivo']})."
            )
    if "*" in limites:
        quantidade = int(limites["*"])
        contagem: dict[str, int] = {}
        for candidato in selecionados:
            contagem[candidato.papel] = contagem.get(candidato.papel, 0) + 1
        for papel, usados in contagem.items():
            if usados > quantidade:
                pendencias.append(
                    f"Limite de {quantidade} por {papel} excedido."
                )
    for papel, quantidade in limites.items():
        if papel == "*":
            continue
        usados = sum(1 for c in selecionados if c.papel == papel)
        if usados > quantidade:
            pendencias.append(f"Limite de {quantidade} por {papel} excedido.")
    if not busca_completa:
        pendencias.append(
            "Busca limitada por tamanho; a solução é a melhor encontrada no pool."
        )

    riscos: list[dict] = []
    for candidato in selecionados:
        if candidato.status != "elegivel":
            riscos.append(
                {
                    "tipo": "candidato_nao_elegivel",
                    "candidato_id": candidato.candidato_id,
                    "mensagem": (
                        f"{candidato.nome} está {candidato.status}; "
                        "não deveria compor a equipe."
                    ),
                }
            )
        if candidato.confianca < 30:
            riscos.append(
                {
                    "tipo": "confianca_baixa",
                    "candidato_id": candidato.candidato_id,
                    "mensagem": f"Confiança baixa para {candidato.nome}.",
                }
            )
        if candidato.atualidade < 50:
            riscos.append(
                {
                    "tipo": "atualidade_baixa",
                    "candidato_id": candidato.candidato_id,
                    "mensagem": f"Atualidade baixa para {candidato.nome}.",
                }
            )
        if candidato.conflito_disputa_id is not None:
            riscos.append(
                {
                    "tipo": "conflito_alocacao",
                    "candidato_id": candidato.candidato_id,
                    "mensagem": (
                        f"{candidato.nome} já está alocado na disputa "
                        f"{candidato.conflito_disputa_id}."
                    ),
                }
            )
    for item in coletivos_avaliados:
        if item["status"] == "nao_atendido":
            riscos.append(
                {
                    "tipo": "coletivo_nao_atendido",
                    "mensagem": f"Requisito coletivo não atendido: {item['texto']}.",
                }
            )
    if len(selecionados) < total_vagas:
        riscos.append(
            {
                "tipo": "cobertura_incompleta",
                "mensagem": f"Cobertura {len(selecionados)}/{total_vagas} vagas.",
            }
        )

    status = "viavel"
    if len(selecionados) < total_vagas:
        status = "incompleta"
    elif any(item["status"] != "atendido" for item in coletivos_avaliados):
        status = "pendente"

    return Composicao(
        disputa_id=disputa_id,
        edital_versao_id=edital_versao_id,
        referencia=referencia,
        vagas=vagas,
        slots=slots,
        nota_total=nota_total,
        bonus_coletivo=bonus,
        penalidade_coletiva=penalidade,
        objetivo=objetivo,
        objetivo_guloso=objetivo,
        diferenca_guloso=0.0,
        cobertura=round(cobertura, 4),
        status=status,
        requisitos_coletivos=coletivos_avaliados,
        pendencias=pendencias,
        riscos=riscos,
        alternativas={},
        candidatos=candidatos,
        busca_completa=busca_completa,
    )


def _pool_por_papel(
    candidatos: list[CandidatoComposicao],
    vagas: list[VagaDemanda],
    coletivos: list[RequisitoColetivo],
    limite: int = LIMITE_CANDIDATOS_PAPEL,
) -> dict[str, list[CandidatoComposicao]]:
    papeis = {vaga.papel for vaga in vagas}
    pool: dict[str, list[CandidatoComposicao]] = {}
    for papel in papeis:
        elegiveis = [
            c
            for c in candidatos
            if c.papel == papel
            and c.status == "elegivel"
            and c.conflito_disputa_id is None
        ]
        elegiveis.sort(
            key=lambda c: (c.nota_tecnica, c.score_interno_apoio), reverse=True
        )
        if len(elegiveis) <= limite:
            pool[papel] = elegiveis
            continue
        # Mantém os melhores por nota e também especialistas que possam atender
        # requisitos coletivos, para não perder uma combinação viável.
        escolhidos = {c.candidato_id: c for c in elegiveis[:limite]}
        for req in coletivos:
            if req.tipo == "manual" or not req.alvo:
                continue
            for candidato in elegiveis:
                if req.tipo == "certificacao":
                    atende = any(
                        _match_nome(req.alvo, cert)
                        for cert in candidato.certificacoes
                    )
                else:
                    atende = any(
                        _match_nome(req.alvo, comp)
                        for comp in candidato.competencias
                    )
                if atende:
                    escolhidos.setdefault(candidato.candidato_id, candidato)
                    break
        pool[papel] = list(escolhidos.values())
    return pool


def _guloso(
    vagas: list[VagaDemanda],
    pool: dict[str, list[CandidatoComposicao]],
    limites: dict[str, int],
) -> list[CandidatoComposicao | None]:
    usados: set[int] = set()
    contagem: dict[str, int] = {}
    escolhidos: list[CandidatoComposicao | None] = []
    for vaga in vagas:
        limite = _limite_papel(limites, vaga.papel)
        opcoes = [
            c
            for c in pool.get(vaga.papel, [])
            if c.candidato_id not in usados
            and contagem.get(vaga.papel, 0) < limite
        ]
        if not opcoes:
            escolhidos.append(None)
            continue
        escolhido = max(
            opcoes, key=lambda c: (c.nota_tecnica, c.score_interno_apoio)
        )
        usados.add(escolhido.candidato_id)
        contagem[vaga.papel] = contagem.get(vaga.papel, 0) + 1
        escolhidos.append(escolhido)
    return escolhidos


def _otimizar(
    *,
    vagas: list[VagaDemanda],
    pool: dict[str, list[CandidatoComposicao]],
    coletivos: list[RequisitoColetivo],
    limites: dict[str, int],
    guloso: list[CandidatoComposicao | None],
    limite_nos: int = LIMITE_BUSCA,
) -> tuple[list[CandidatoComposicao | None], bool]:
    max_bonus = sum(req.peso for req in coletivos if req.tipo != "manual")
    melhor = list(guloso)
    melhor_objetivo = _objetivo_de(melhor, coletivos)
    nos = 0
    busca_completa = True
    ordenadas = sorted(
        enumerate(vagas),
        key=lambda item: len(pool.get(item[1].papel, [])),
    )

    def limite_atingido(papel: str, contagem: dict[str, int]) -> bool:
        return contagem.get(papel, 0) >= _limite_papel(limites, papel)

    def cota(
        indice: int,
        usados: set[int],
        contagem: dict[str, int],
        nota_atual: float,
    ) -> float:
        total = nota_atual
        for _, vaga in ordenadas[indice:]:
            if limite_atingido(vaga.papel, contagem):
                continue
            opcoes = [
                c.nota_tecnica
                for c in pool.get(vaga.papel, [])
                if c.candidato_id not in usados
            ]
            total += max(opcoes, default=0.0)
        return total + max_bonus

    def buscar(
        indice: int,
        escolhidos: list[CandidatoComposicao | None],
        usados: set[int],
        contagem: dict[str, int],
    ) -> None:
        nonlocal melhor, melhor_objetivo, nos, busca_completa
        nos += 1
        if nos > limite_nos:
            busca_completa = False
            return
        nota_atual = sum(
            c.nota_tecnica for c in escolhidos if c is not None
        )
        if cota(indice, usados, contagem, nota_atual) <= melhor_objetivo:
            return
        if indice == len(ordenadas):
            objetivo = _objetivo_de(escolhidos, coletivos)
            if objetivo > melhor_objetivo:
                melhor_objetivo = objetivo
                melhor = list(escolhidos)
            return
        posicao, vaga = ordenadas[indice]
        opcoes = [
            c
            for c in pool.get(vaga.papel, [])
            if c.candidato_id not in usados
            and not limite_atingido(vaga.papel, contagem)
        ]
        opcoes.sort(
            key=lambda c: (c.nota_tecnica, c.score_interno_apoio), reverse=True
        )
        for candidato in opcoes:
            escolhidos[posicao] = candidato
            usados.add(candidato.candidato_id)
            contagem[vaga.papel] = contagem.get(vaga.papel, 0) + 1
            buscar(indice + 1, escolhidos, usados, contagem)
            contagem[vaga.papel] -= 1
            usados.remove(candidato.candidato_id)
            escolhidos[posicao] = None
        # Vaga descoberta: só é necessária quando não há opção viável ou quando
        # abrir mão dela permite usar o candidato em outro papel.
        escolhidos[posicao] = None
        buscar(indice + 1, escolhidos, usados, contagem)

    buscar(0, [None] * len(vagas), set(), {})
    return melhor, busca_completa


def _objetivo_de(
    escolhidos: list[CandidatoComposicao | None],
    coletivos: list[RequisitoColetivo],
) -> float:
    selecionados = [c for c in escolhidos if c is not None]
    avaliados = _avaliar_coletivos(coletivos, selecionados)
    bonus = sum(
        item["peso"] for item in avaliados if item["status"] == "atendido"
    )
    penalidade = sum(
        item["peso"] for item in avaliados if item["status"] == "nao_atendido"
    )
    return round(
        sum(c.nota_tecnica for c in selecionados) + bonus - penalidade,
        2,
    )


def recomendar_composicao(
    con: sqlite3.Connection,
    disputa_id: int,
    *,
    edital_versao_id: int | None = None,
    limite_candidatos_papel: int = LIMITE_CANDIDATOS_PAPEL,
) -> Composicao:
    disputa = con.execute(
        "SELECT * FROM disputas WHERE id = ?", (disputa_id,)
    ).fetchone()
    if disputa is None:
        raise KeyError("disputa não encontrada")
    versao_id = edital_versao_id or disputa["edital_versao_id"]
    if versao_id is None:
        raise ValueError("disputa sem edital para composição")
    demanda = carregar_demanda(con, int(versao_id))
    vagas, pendencias_vagas = _vagas_demanda(demanda)
    coletivos, limites, pendencias_coletivos = _requisitos_coletivos(demanda)
    candidatos = _carregar_candidatos(con, disputa_id, int(versao_id))
    pool = _pool_por_papel(
        candidatos, vagas, coletivos, limite=limite_candidatos_papel
    )
    guloso = _guloso(vagas, pool, limites)
    otimo, busca_completa = _otimizar(
        vagas=vagas,
        pool=pool,
        coletivos=coletivos,
        limites=limites,
        guloso=guloso,
    )
    referencia = date.today().isoformat()
    avaliacao = _avaliar_selecao(
        disputa_id=disputa_id,
        edital_versao_id=int(versao_id),
        referencia=referencia,
        vagas=vagas,
        escolhidos=otimo,
        coletivos=coletivos,
        limites=limites,
        candidatos=candidatos,
        busca_completa=busca_completa,
    )
    avaliacao_gulosa = _avaliar_selecao(
        disputa_id=disputa_id,
        edital_versao_id=int(versao_id),
        referencia=referencia,
        vagas=vagas,
        escolhidos=guloso,
        coletivos=coletivos,
        limites=limites,
        candidatos=candidatos,
        busca_completa=True,
    )
    avaliacao.objetivo_guloso = avaliacao_gulosa.objetivo
    avaliacao.diferenca_guloso = round(
        avaliacao.objetivo - avaliacao_gulosa.objetivo, 2
    )
    avaliacao.pendencias = (
        pendencias_vagas + pendencias_coletivos + avaliacao.pendencias
    )
    selecionados_ids = {
        slot["candidato"]["candidato_id"]
        for slot in avaliacao.slots
        if slot.get("candidato")
    }
    alternativas: dict[str, list[dict]] = {}
    for vaga in vagas:
        opcoes = [
            c
            for c in pool.get(vaga.papel, [])
            if c.candidato_id not in selecionados_ids
        ]
        opcoes.sort(
            key=lambda c: (c.nota_tecnica, c.score_interno_apoio), reverse=True
        )
        alternativas[f"{vaga.papel}:{vaga.vaga}"] = [
            c.como_dict() for c in opcoes[:3]
        ]
    avaliacao.alternativas = alternativas
    return avaliacao


def avaliar_composicao(
    con: sqlite3.Connection,
    disputa_id: int,
    selecoes: list[dict],
    *,
    edital_versao_id: int | None = None,
) -> Composicao:
    """Avalia a seleção manual do gestor, sem otimizar por cima dela."""
    disputa = con.execute(
        "SELECT * FROM disputas WHERE id = ?", (disputa_id,)
    ).fetchone()
    if disputa is None:
        raise KeyError("disputa não encontrada")
    versao_id = edital_versao_id or disputa["edital_versao_id"]
    if versao_id is None:
        raise ValueError("disputa sem edital para composição")
    demanda = carregar_demanda(con, int(versao_id))
    vagas, pendencias_vagas = _vagas_demanda(demanda)
    coletivos, limites, pendencias_coletivos = _requisitos_coletivos(demanda)
    candidatos = _carregar_candidatos(con, disputa_id, int(versao_id))
    por_chave = {
        (c.papel_id, c.candidato_id): c for c in candidatos
    }
    escolhidos: list[CandidatoComposicao | None] = []
    for vaga in vagas:
        selecao = next(
            (
                item
                for item in selecoes
                if int(item.get("papel_id", -1)) == vaga.papel_id
                and int(item.get("vaga", -1)) == vaga.vaga
            ),
            None,
        )
        candidato_id = selecao.get("candidato_id") if selecao else None
        escolhidos.append(
            por_chave.get((vaga.papel_id, int(candidato_id)))
            if candidato_id is not None
            else None
        )
    avaliacao = _avaliar_selecao(
        disputa_id=disputa_id,
        edital_versao_id=int(versao_id),
        referencia=date.today().isoformat(),
        vagas=vagas,
        escolhidos=escolhidos,
        coletivos=coletivos,
        limites=limites,
        candidatos=candidatos,
    )
    avaliacao.pendencias = (
        pendencias_vagas + pendencias_coletivos + avaliacao.pendencias
    )
    return avaliacao


def validar_selecao_demanda(
    con: sqlite3.Connection, disputa_id: int, selecoes: list[dict]
) -> None:
    """Impede salvar alocação com candidato eliminado, pendente ou em conflito."""
    disputa = con.execute(
        "SELECT edital_versao_id FROM disputas WHERE id = ?", (disputa_id,)
    ).fetchone()
    if disputa is None or disputa["edital_versao_id"] is None:
        return
    rows = listar_resultados_demanda(
        con, disputa_id, int(disputa["edital_versao_id"])
    )
    por_chave = {
        (int(row["papel_id"]), int(row["candidato_id"])): row for row in rows
    }
    for selecao in selecoes:
        papel_id = int(selecao["papel_id"])
        candidato_id = int(selecao["candidato_id"])
        row = por_chave.get((papel_id, candidato_id))
        if row is None:
            raise ValueError(
                f"Candidato {candidato_id} não tem pontuação para o papel "
                f"{papel_id} nesta demanda."
            )
        if row["status"] != "elegivel":
            raise ValueError(
                f"Candidato {candidato_id} está {row['status']} na demanda; "
                "apenas elegíveis podem ser alocados."
            )
        conflito = _conflito(con, candidato_id, disputa_id)
        if conflito is not None:
            raise ValueError(
                f"Candidato {candidato_id} já está alocado na disputa {conflito}."
            )

    # A validação de viabilidade também fica no serviço, não só na tela:
    # requisitos coletivos pendentes/ausentes e limite por papel impedem uma
    # alocação "viável" de ser persistida por um chamador direto.
    demanda = carregar_demanda(con, int(disputa["edital_versao_id"]))
    coletivos, limites, _ = _requisitos_coletivos(demanda)
    candidatos = _carregar_candidatos(
        con, disputa_id, int(disputa["edital_versao_id"])
    )
    selecionados: list[CandidatoComposicao] = []
    for selecao in selecoes:
        papel_id = int(selecao["papel_id"])
        candidato_id = int(selecao["candidato_id"])
        candidato = next(
            (
                item
                for item in candidatos
                if item.papel_id == papel_id
                and item.candidato_id == candidato_id
            ),
            None,
        )
        if candidato is not None:
            selecionados.append(candidato)
    for item in _avaliar_coletivos(coletivos, selecionados):
        if item["status"] == "pendente":
            raise ValueError(
                "Requisito coletivo pendente: "
                f"{item['texto'] or 'sem fato estruturado'}."
            )
        if item["status"] == "nao_atendido":
            raise ValueError(
                "Requisito coletivo não atendido: "
                f"{item['texto']} ({item['motivo']})."
            )
    if "*" in limites:
        quantidade = int(limites["*"])
        contagem: dict[str, int] = {}
        for candidato in selecionados:
            contagem[candidato.papel] = contagem.get(candidato.papel, 0) + 1
        for papel, usados in contagem.items():
            if usados > quantidade:
                raise ValueError(
                    f"Limite de {quantidade} por {papel} excedido."
                )
    for papel, quantidade in limites.items():
        if papel == "*":
            continue
        usados = sum(1 for c in selecionados if c.papel == papel)
        if usados > quantidade:
            raise ValueError(f"Limite de {quantidade} por {papel} excedido.")


def listar_vagas_demanda(
    con: sqlite3.Connection, edital_versao_id: int
) -> list[VagaDemanda]:
    demanda = carregar_demanda(con, edital_versao_id)
    vagas, _ = _vagas_demanda(demanda)
    return vagas
