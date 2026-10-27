"""Fase 3 — contexto de fatos de um currículo para o motor de score.

Lê os fatos já estruturados (o preset NUNCA reextrai) e resolve a evidência
de cada fato pela tabela polimórfica `evidencias`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime

from src.governance.atualidade import classificar

ANOS_TETO = 10


@dataclass(kw_only=True)
class _FatoBase:
    """Metadados comuns de atualidade e comprovação de um fato."""

    fato_id: int | None = None
    frescura: str = "sem_confirmacao"
    estado: str = "declarado"
    evidencia_estado: str = "declarado"
    valido_ate: str | None = None
    ultima_confirmacao: str | None = None


@dataclass
class CompetenciaFato(_FatoBase):
    nome: str = ""
    confianca: int = 0


@dataclass
class ExperienciaFato(_FatoBase):
    dominio: str | None = None
    confianca: int = 0
    inicio: str | None = None
    fim: str | None = None
    resumo: str | None = None


@dataclass
class CertificacaoFato(_FatoBase):
    nome: str = ""
    confianca: int = 0


@dataclass
class FormacaoFato(_FatoBase):
    grau: str | None = None
    curso: str | None = None
    confianca: int = 0


@dataclass
class IdiomaFato(_FatoBase):
    idioma: str = ""
    nivel: str | None = None
    confianca: int = 0


@dataclass
class ContextoCandidato:
    candidato_id: int
    curriculo_id: int
    elegivel_brasil: str = "nao_informado"
    competencias: list[CompetenciaFato] = field(default_factory=list)
    experiencias: list[ExperienciaFato] = field(default_factory=list)
    certificacoes: list[CertificacaoFato] = field(default_factory=list)
    formacoes: list[FormacaoFato] = field(default_factory=list)
    idiomas: list[IdiomaFato] = field(default_factory=list)
    anos_experiencia: float = 0.0
    disponibilidade: dict | None = None
    referencia: date | None = None
    evidencia_max: int = 0

    def competencia(self, nome: str) -> int | None:
        for fato in self.competencias:
            if fato.nome == nome:
                return fato.confianca
        return None

    def certificacao(self, nome: str) -> int | None:
        for fato in self.certificacoes:
            if fato.nome == nome:
                return fato.confianca
        return None

    def idioma(self, nome: str) -> IdiomaFato | None:
        for fato in self.idiomas:
            if fato.idioma == nome:
                return fato
        return None


def _ano(texto: str | None) -> int | None:
    if not texto:
        return None
    baixo = texto.lower()
    if any(p in baixo for p in ("present", "atual", "hoje", "current")):
        return datetime.now().year
    achado = re.search(r"(?:19|20)\d{2}", texto)
    if achado:
        return int(achado.group(0))
    achado = re.search(r"\b(\d{2})\b", texto)
    if achado:
        yy = int(achado.group(1))
        return 2000 + yy if yy <= 50 else 1900 + yy
    return None


def anos_intervalos(experiencias: list[ExperienciaFato]) -> float:
    """Soma das durações, fundindo períodos sobrepostos (precisão de ano)."""
    intervalos: list[tuple[int, int]] = []
    for exp in experiencias:
        inicio = _ano(exp.inicio)
        fim = _ano(exp.fim)
        if inicio is not None and fim is not None and fim >= inicio:
            intervalos.append((inicio, fim))
    if not intervalos:
        return 0.0
    intervalos.sort()
    total = 0
    inicio_atual, fim_atual = intervalos[0]
    for inicio, fim in intervalos[1:]:
        if inicio <= fim_atual:
            fim_atual = max(fim_atual, fim)
        else:
            total += fim_atual - inicio_atual
            inicio_atual, fim_atual = inicio, fim
    total += fim_atual - inicio_atual
    return float(total)


def _data_iso(valor: str | date | datetime | None) -> date | None:
    if valor is None:
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def _melhor_evidencia(
    linhas: list[dict], referencia: date
) -> tuple[str, str | None]:
    """Resume as evidências documentais de um fato na data de referência."""
    if not linhas:
        return "declarado", None
    validos = [
        linha
        for linha in linhas
        if linha.get("status") == "validado"
        and (
            _data_iso(linha.get("valido_ate")) is None
            or referencia <= _data_iso(linha.get("valido_ate"))
        )
    ]
    if validos:
        validades = [_data_iso(linha.get("valido_ate")) for linha in validos]
        validades = [validade for validade in validades if validade is not None]
        return "validado", (max(validades).isoformat() if validades else None)
    if any(linha.get("status") == "enviado" for linha in linhas):
        return "enviado", None
    if any(linha.get("status") == "vencido" for linha in linhas):
        return "vencido", None
    if any(linha.get("status") == "rejeitado" for linha in linhas):
        return "rejeitado", None
    return "declarado", None


@dataclass
class _MapasContexto:
    """Índices globais carregados uma vez para vários currículos."""

    confiancas: dict[tuple[str, int], int]
    atualidade: dict[tuple[str, int], dict]
    documentos: dict[tuple[str, int], list[dict]]


def _carregar_mapas(con) -> _MapasContexto:
    confiancas: dict[tuple[str, int], int] = {}
    for ev in con.execute(
        "SELECT tipo_fato, fato_id, confianca FROM evidencias"
    ):
        confiancas[(ev["tipo_fato"], ev["fato_id"])] = ev["confianca"]

    atualidade: dict[tuple[str, int], dict] = {}
    for row in con.execute("SELECT * FROM atualidade_fatos"):
        atualidade[(row["tipo_fato"], row["fato_id"])] = dict(row)

    documentos: dict[tuple[str, int], list[dict]] = {}
    for row in con.execute("SELECT * FROM evidencias_documentais ORDER BY id DESC"):
        documentos.setdefault((row["tipo_fato"], row["fato_id"]), []).append(dict(row))
    return _MapasContexto(
        confiancas=confiancas,
        atualidade=atualidade,
        documentos=documentos,
    )


def carregar_contexto(
    con, curriculo_id: int, referencia: date | None = None
) -> ContextoCandidato:
    return _contexto_de(
        con,
        curriculo_id,
        referencia or date.today(),
        _carregar_mapas(con),
    )


def carregar_contextos(
    con,
    curriculo_ids: list[int] | tuple[int, ...],
    referencia: date | None = None,
) -> dict[int, ContextoCandidato]:
    """Carrega vários currículos reutilizando os índices globais.

    O carregamento individual fazia três varreduras globais por candidato,
    tornando o custo O(N × (E + A + D)). Aqui as varreduras são O(E + A + D)
    uma única vez; o restante é O(N × fatos_do_curriculo).
    """
    ids = list(dict.fromkeys(int(item) for item in curriculo_ids))
    if not ids:
        return {}
    ref = referencia or date.today()
    mapas = _carregar_mapas(con)
    resultado: dict[int, ContextoCandidato] = {}
    for curriculo_id in ids:
        try:
            resultado[curriculo_id] = _contexto_de(
                con, curriculo_id, ref, mapas
            )
        except KeyError:
            continue
    return resultado


def _contexto_de(
    con,
    curriculo_id: int,
    ref: date,
    mapas: _MapasContexto,
) -> ContextoCandidato:
    curriculo = con.execute(
        "SELECT cur.candidato_id AS candidato_id, c.elegivel_brasil"
        " FROM curriculos cur JOIN candidatos c ON c.id = cur.candidato_id"
        " WHERE cur.id = ?",
        (curriculo_id,),
    ).fetchone()
    if curriculo is None:
        raise KeyError(f"currículo {curriculo_id} não encontrado")

    confiancas = mapas.confiancas
    atualidade = mapas.atualidade
    documentos = mapas.documentos

    def conf(tipo: str, fato_id: int) -> int:
        return int(confiancas.get((tipo, fato_id), 0))

    def info(tipo: str, fato_id: int) -> dict:
        atual = atualidade.get((tipo, fato_id))
        ultima = atual.get("ultima_confirmacao") if atual else None
        validade = atual.get("valido_ate") if atual else None
        estado = atual.get("estado") if atual else "declarado"
        evidencia, ev_validade = _melhor_evidencia(
            documentos.get((tipo, fato_id), []), ref
        )
        return {
            "fato_id": fato_id,
            "frescura": classificar(ultima, validade, ref),
            "estado": estado,
            "evidencia_estado": evidencia,
            "valido_ate": ev_validade or validade,
            "ultima_confirmacao": ultima,
        }

    ctx = ContextoCandidato(
        candidato_id=int(curriculo["candidato_id"]),
        curriculo_id=curriculo_id,
        elegivel_brasil=curriculo["elegivel_brasil"],
        referencia=ref,
    )

    for row in con.execute(
        "SELECT id, nome FROM competencias WHERE curriculo_id = ?", (curriculo_id,)
    ):
        ctx.competencias.append(
            CompetenciaFato(
                row["nome"], conf("competencia", row["id"]), **info("competencia", row["id"])
            )
        )
    for row in con.execute(
        "SELECT id, dominio, data_inicio, data_fim, resumo FROM experiencias"
        " WHERE curriculo_id = ?",
        (curriculo_id,),
    ):
        ctx.experiencias.append(
            ExperienciaFato(
                row["dominio"],
                conf("experiencia", row["id"]),
                row["data_inicio"],
                row["data_fim"],
                row["resumo"],
                **info("experiencia", row["id"]),
            )
        )
    for row in con.execute(
        "SELECT id, nome FROM certificacoes WHERE curriculo_id = ?", (curriculo_id,)
    ):
        ctx.certificacoes.append(
            CertificacaoFato(
                row["nome"],
                conf("certificacao", row["id"]),
                **info("certificacao", row["id"]),
            )
        )
    for row in con.execute(
        "SELECT id, grau, curso FROM formacoes WHERE curriculo_id = ?", (curriculo_id,)
    ):
        ctx.formacoes.append(
            FormacaoFato(
                row["grau"],
                row["curso"],
                conf("formacao", row["id"]),
                **info("formacao", row["id"]),
            )
        )
    for row in con.execute(
        "SELECT id, idioma, nivel FROM idiomas WHERE curriculo_id = ?", (curriculo_id,)
    ):
        ctx.idiomas.append(
            IdiomaFato(
                row["idioma"],
                row["nivel"],
                conf("idioma", row["id"]),
                **info("idioma", row["id"]),
            )
        )

    disponibilidade = con.execute(
        "SELECT * FROM disponibilidades WHERE candidato_id = ?"
        " ORDER BY id DESC LIMIT 1",
        (ctx.candidato_id,),
    ).fetchone()
    if disponibilidade:
        ctx.disponibilidade = dict(disponibilidade)
        ctx.disponibilidade["frescura"] = classificar(
            ctx.disponibilidade.get("confirmado_em"),
            ctx.disponibilidade.get("valido_ate"),
            ref,
        )
    else:
        ctx.disponibilidade = {
            "estado": "desconhecido",
            "fonte": "nao_informado",
            "confirmado_em": None,
            "valido_ate": None,
            "inicio": None,
            "fim": None,
            "percentual": None,
            "frescura": "sem_confirmacao",
        }

    ctx.anos_experiencia = anos_intervalos(ctx.experiencias)
    confiancas_fatos = [
        fato.confianca
        for grupo in (
            ctx.competencias,
            ctx.experiencias,
            ctx.certificacoes,
            ctx.formacoes,
            ctx.idiomas,
        )
        for fato in grupo
    ]
    ctx.evidencia_max = min(4, max(confiancas_fatos, default=0) // 25)
    return ctx
