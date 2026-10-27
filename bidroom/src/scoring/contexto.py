"""Fase 3 — contexto de fatos de um currículo para o motor de score.

Lê os fatos já estruturados (o preset NUNCA reextrai) e resolve a evidência
de cada fato pela tabela polimórfica `evidencias`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

ANOS_TETO = 10


@dataclass
class CompetenciaFato:
    nome: str
    confianca: int


@dataclass
class ExperienciaFato:
    dominio: str | None
    confianca: int
    inicio: str | None
    fim: str | None


@dataclass
class CertificacaoFato:
    nome: str
    confianca: int


@dataclass
class FormacaoFato:
    grau: str | None
    curso: str | None
    confianca: int


@dataclass
class IdiomaFato:
    idioma: str
    nivel: str | None
    confianca: int


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


def carregar_contexto(con, curriculo_id: int) -> ContextoCandidato:
    curriculo = con.execute(
        "SELECT cur.candidato_id AS candidato_id, c.elegivel_brasil"
        " FROM curriculos cur JOIN candidatos c ON c.id = cur.candidato_id"
        " WHERE cur.id = ?",
        (curriculo_id,),
    ).fetchone()
    if curriculo is None:
        raise KeyError(f"currículo {curriculo_id} não encontrado")

    confiancas: dict[tuple[str, int], int] = {}
    for ev in con.execute(
        "SELECT tipo_fato, fato_id, confianca FROM evidencias"
    ):
        confiancas[(ev["tipo_fato"], ev["fato_id"])] = ev["confianca"]

    def conf(tipo: str, fato_id: int) -> int:
        return int(confiancas.get((tipo, fato_id), 0))

    ctx = ContextoCandidato(
        candidato_id=int(curriculo["candidato_id"]),
        curriculo_id=curriculo_id,
        elegivel_brasil=curriculo["elegivel_brasil"],
    )

    for row in con.execute(
        "SELECT id, nome FROM competencias WHERE curriculo_id = ?", (curriculo_id,)
    ):
        ctx.competencias.append(
            CompetenciaFato(nome=row["nome"], confianca=conf("competencia", row["id"]))
        )
    for row in con.execute(
        "SELECT id, dominio, data_inicio, data_fim FROM experiencias"
        " WHERE curriculo_id = ?",
        (curriculo_id,),
    ):
        ctx.experiencias.append(
            ExperienciaFato(
                dominio=row["dominio"],
                confianca=conf("experiencia", row["id"]),
                inicio=row["data_inicio"],
                fim=row["data_fim"],
            )
        )
    for row in con.execute(
        "SELECT id, nome FROM certificacoes WHERE curriculo_id = ?", (curriculo_id,)
    ):
        ctx.certificacoes.append(
            CertificacaoFato(
                nome=row["nome"], confianca=conf("certificacao", row["id"])
            )
        )
    for row in con.execute(
        "SELECT id, grau, curso FROM formacoes WHERE curriculo_id = ?", (curriculo_id,)
    ):
        ctx.formacoes.append(
            FormacaoFato(
                grau=row["grau"], curso=row["curso"], confianca=conf("formacao", row["id"])
            )
        )
    for row in con.execute(
        "SELECT id, idioma, nivel FROM idiomas WHERE curriculo_id = ?", (curriculo_id,)
    ):
        ctx.idiomas.append(
            IdiomaFato(
                idioma=row["idioma"],
                nivel=row["nivel"],
                confianca=conf("idioma", row["id"]),
            )
        )

    ctx.anos_experiencia = anos_intervalos(ctx.experiencias)
    return ctx
