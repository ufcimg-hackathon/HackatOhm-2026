"""Fase 3 — motor de score.

Fórmulas (todas as parcelas 0–100):

    pontuacao_ponderada = Σ (pontuacao_categoria × peso) / 100
    confianca           = Σ (nivel_evidencia_categoria × peso) / 100
    pontuacao_final     = pontuacao_ponderada × (piso + (1 - piso) × confianca/100)

Pesos e gates vêm da versão do preset (`pesos_json`/`gates_json`). Nenhuma
nota de candidato é hardcoded: o motor só transforma fatos em categorias.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from src.scoring.catalogo import PerfilPapel
from src.scoring.contexto import ContextoCandidato
from src.scoring.gates import ResultadoGate, avaliar_gates, status_geral

CATEGORIAS = (
    "aderencia",
    "dominio_energia",
    "certificacoes",
    "experiencia",
    "idioma",
    "educacao",
    "ferramentas",
)

NIVEIS_IDIOMA_ALTO = {"fluente", "fluent", "avançado", "avancado", "advanced", "nativo", "native"}
NIVEIS_IDIOMA_MEDIO = {"intermediário", "intermediario", "intermediate"}
NIVEIS_IDIOMA_BAIXO = {"básico", "basico", "basic"}


@dataclass
class ResultadoScore:
    papel: str
    pontuacao_ponderada: float
    confianca: float
    pontuacao_final: float
    status_gates: str
    gates: list[ResultadoGate]
    detalhe: dict = field(default_factory=dict)


def _media(valores: list[int]) -> float:
    return sum(valores) / len(valores) if valores else 0.0


def _score_cobertura(ctx: ContextoCandidato, nomes: tuple[str, ...]) -> tuple[float, float, dict]:
    valores = [(nome, ctx.competencia(nome)) for nome in nomes]
    presentes = [(nome, conf) for nome, conf in valores if conf is not None]
    score = 100.0 * len(presentes) / len(nomes)
    confianca = _media([conf for _nome, conf in presentes]) if presentes else 0.0
    detalhe = {
        "presentes": [nome for nome, _conf in presentes],
        "faltantes": [nome for nome, conf in valores if conf is None],
    }
    return score, confianca, detalhe


def _score_dominio(ctx: ContextoCandidato) -> tuple[float, float, dict]:
    energias = [e for e in ctx.experiencias if e.dominio == "energia"]
    if not energias:
        return 0.0, 0.0, {"experiencias_energia": 0}
    score = 40.0
    if len(energias) >= 2:
        score += 30.0
    if any(e.confianca >= 50 for e in energias):
        score += 30.0
    return min(100.0, score), _media([e.confianca for e in energias]), {
        "experiencias_energia": len(energias)
    }


def _score_certificacoes(
    ctx: ContextoCandidato, perfil: PerfilPapel
) -> tuple[float, float, dict]:
    total = 0.0
    confs: list[int] = []
    encontradas: list[dict] = []
    for esperada in perfil.certificacoes:
        conf = ctx.certificacao(esperada.nome)
        if conf is None:
            continue
        pontos = esperada.peso * (conf / 100)
        total += pontos
        confs.append(conf)
        encontradas.append(
            {
                "nome": esperada.nome,
                "confianca": conf,
                "pontos": round(pontos, 2),
                "nivel": f"N{conf // 25}",
            }
        )
    return min(100.0, total), _media(confs), {"encontradas": encontradas}


def _score_experiencia(ctx: ContextoCandidato) -> tuple[float, float, dict]:
    if not ctx.experiencias:
        return 0.0, 0.0, {"anos": 0.0}
    score = min(100.0, 100.0 * ctx.anos_experiencia / 10.0)
    confianca = _media([e.confianca for e in ctx.experiencias])
    return score, confianca, {"anos": ctx.anos_experiencia}


def _valor_idioma(nivel: str | None) -> float:
    if not nivel:
        return 0.0
    baixo = nivel.lower()
    if baixo in NIVEIS_IDIOMA_ALTO:
        return 100.0
    if baixo in NIVEIS_IDIOMA_MEDIO:
        return 60.0
    if baixo in NIVEIS_IDIOMA_BAIXO:
        return 30.0
    return 0.0


def _score_idioma(ctx: ContextoCandidato) -> tuple[float, float, dict]:
    portugues = ctx.idioma("Português")
    ingles = ctx.idioma("Inglês")
    pt_valor = _valor_idioma(portugues.nivel) if portugues else 0.0
    en_valor = _valor_idioma(ingles.nivel) if ingles else 0.0
    score = 0.6 * pt_valor + 0.4 * en_valor
    confianca = 0.6 * (portugues.confianca if portugues else 0) + 0.4 * (
        ingles.confianca if ingles else 0
    )
    return score, confianca, {
        "portugues": portugues.nivel if portugues else None,
        "ingles": ingles.nivel if ingles else None,
    }


def _valor_graduacao(fato) -> float:
    texto = f"{fato.grau or ''} {fato.curso or ''}".lower()
    if any(k in texto for k in ("doutorado", "phd", "doutor")):
        return 100.0
    if any(k in texto for k in ("mestrado", "master of", "msc", "m.sc", "mestre")):
        return 100.0
    if any(
        k in texto
        for k in (
            "mba",
            "pós-graduação",
            "pos-graduacao",
            "pós graduação",
            "especialização",
            "especializacao",
            "postgraduate",
        )
    ):
        return 100.0
    if any(
        k in texto
        for k in (
            "bacharelado",
            "graduação",
            "graduacao",
            "bachelor",
            "b.tech",
            "btech",
            "b.sc",
            "bsc",
            "licenciatura",
            "degree",
        )
    ):
        return 70.0
    if "técnico" in texto or "tecnico" in texto:
        return 40.0
    if any(k in texto for k in ("university", "universidade", "college", "faculdade")):
        return 40.0
    return 0.0


def _score_educacao(ctx: ContextoCandidato) -> tuple[float, float, dict]:
    if not ctx.formacoes:
        return 0.0, 0.0, {"grau_maximo": None}
    melhor = max(ctx.formacoes, key=_valor_graduacao)
    valor = _valor_graduacao(melhor)
    return valor, _media([f.confianca for f in ctx.formacoes]), {
        "grau_maximo": (melhor.grau or melhor.curso),
        "valor": valor,
    }


def _ancoras_pm(
    ctx: ContextoCandidato, perfil: PerfilPapel, categorias: dict[str, tuple[float, float, dict]]
) -> list[dict]:
    if perfil.sigla != "pm_scrum":
        return []
    pmp = ctx.certificacao("PMP")
    dominio_score = categorias["dominio_energia"][0]
    educacao_score = categorias["educacao"][0]
    experiencia_score = categorias["experiencia"][0]
    return [
        {
            "nome": "PMP",
            "peso": 10,
            "categoria": "certificacoes",
            "declarada": pmp is not None,
            "confianca": pmp or 0,
            "contribuicao": round(categorias["certificacoes"][0] / 100 * 10, 2),
        },
        {
            "nome": "Energia",
            "peso": 8,
            "categoria": "dominio_energia",
            "atingida": dominio_score > 0,
            "contribuicao": round(dominio_score / 100 * 8, 2),
        },
        {
            "nome": "Pós-graduação",
            "peso": 5,
            "categoria": "educacao",
            "atingida": educacao_score >= 100,
            "contribuicao": round(educacao_score / 100 * 5, 2),
        },
        {
            "nome": "10+ anos",
            "peso": 5,
            "categoria": "experiencia",
            "atingida": ctx.anos_experiencia >= 10,
            "contribuicao": round(experiencia_score / 100 * 5, 2),
        },
    ]


def pontuar(
    ctx: ContextoCandidato,
    perfil: PerfilPapel,
    pesos: dict,
    gates_config: dict | None = None,
) -> ResultadoScore:
    """Calcula as 7 categorias, a confiança, os gates e o score final."""
    categorias_config = pesos.get("categorias", pesos)
    pesos_papel = categorias_config.get(perfil.sigla)
    if not pesos_papel:
        raise ValueError(f"pesos ausentes para o papel {perfil.sigla}")
    faltando = [c for c in CATEGORIAS if c not in pesos_papel]
    if faltando:
        raise ValueError(f"categorias sem peso em {perfil.sigla}: {faltando}")
    soma_pesos = sum(float(pesos_papel[c]) for c in CATEGORIAS)
    if abs(soma_pesos - 100) > 0.01:
        raise ValueError(
            f"pesos de {perfil.sigla} somam {soma_pesos}, esperado 100"
        )

    calculos: dict[str, tuple[float, float, dict]] = {
        "aderencia": _score_cobertura(ctx, perfil.core),
        "dominio_energia": _score_dominio(ctx),
        "certificacoes": _score_certificacoes(ctx, perfil),
        "experiencia": _score_experiencia(ctx),
        "idioma": _score_idioma(ctx),
        "educacao": _score_educacao(ctx),
        "ferramentas": _score_cobertura(ctx, perfil.ferramentas),
    }

    ponderada = 0.0
    confianca = 0.0
    detalhes_categorias: list[dict] = []
    for categoria in CATEGORIAS:
        score, conf, detalhe = calculos[categoria]
        peso = float(pesos_papel[categoria])
        contribuicao = score * peso / 100
        ponderada += contribuicao
        confianca += conf * peso / 100
        detalhes_categorias.append(
            {
                "categoria": categoria,
                "score": round(score, 2),
                "peso": peso,
                "confianca": round(conf, 2),
                "contribuicao": round(contribuicao, 2),
                "detalhe": detalhe,
            }
        )

    piso = float(pesos.get("piso_confianca", 0.6))
    piso = min(1.0, max(0.0, piso))
    final = ponderada * (piso + (1 - piso) * confianca / 100)

    gates = avaliar_gates(ctx, perfil, gates_config, elegivel_brasil=ctx.elegivel_brasil)
    ancoras = _ancoras_pm(ctx, perfil, calculos)

    detalhe = {
        "categorias": detalhes_categorias,
        "gates": [g.como_dict() for g in gates],
        "status_gates": status_geral(gates),
        "ancoras_pm": ancoras,
        "ancoras_nota": "âncoras são subconjunto das categorias; não somam por cima",
        "anos_experiencia": ctx.anos_experiencia,
        "piso_confianca": piso,
        "formula": (
            "final = ponderada * (piso + (1 - piso) * confianca / 100)"
        ),
    }
    return ResultadoScore(
        papel=perfil.sigla,
        pontuacao_ponderada=round(ponderada, 2),
        confianca=round(confianca, 2),
        pontuacao_final=round(final, 2),
        status_gates=status_geral(gates),
        gates=gates,
        detalhe=detalhe,
    )
