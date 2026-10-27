"""Fase 4 — resumo do perfil pronto para proposta (T3).

Regras, não LLM: o texto só repete fatos e scores que já existem no banco.
Nada de PII, atributo protegido ou adjetivo não comprovado.
"""
from __future__ import annotations

from src.scoring.contexto import ContextoCandidato
from src.scoring.engine import ResultadoScore

ROTULO_STATUS = {
    "elegivel": "elegível",
    "revisar": "revisar",
    "eliminado": "eliminado",
}


def _categoria(resultado: ResultadoScore, nome: str) -> dict:
    for categoria in resultado.detalhe.get("categorias", []):
        if categoria.get("categoria") == nome:
            return categoria
    return {}


def gerar_resumo_proposta(
    nome: str,
    perfil_nome: str,
    resultado: ResultadoScore,
    contexto: ContextoCandidato,
) -> list[str]:
    aderencia = _categoria(resultado, "aderencia")
    aderencia_detalhe = aderencia.get("detalhe", {})
    presentes = aderencia_detalhe.get("presentes", [])
    faltantes = aderencia_detalhe.get("faltantes", [])
    dominio = _categoria(resultado, "dominio_energia")
    certificacoes = _categoria(resultado, "certificacoes")
    educacao = _categoria(resultado, "educacao")

    frases = [
        (
            f"{nome} tem {contexto.anos_experiencia:.0f} anos de experiência "
            f"e aderência de {aderencia.get('score', 0):.0f}/100 ao papel "
            f"{perfil_nome}."
        )
    ]

    total_core = len(presentes) + len(faltantes)
    if faltantes:
        frases.append(
            f"Cobre {len(presentes)} de {total_core} competências core"
            f" ({', '.join(presentes) if presentes else 'nenhuma'});"
            f" lacunas declaradas: {', '.join(faltantes)}."
        )
    elif presentes:
        frases.append(
            "Cobre todas as competências core do papel: "
            f"{', '.join(presentes)}."
        )

    if dominio.get("score", 0) > 0:
        frases.append(
            "Tem vivência no setor de energia registrada em "
            f"{dominio.get('detalhe', {}).get('experiencias_energia', 0)} "
            "experiência(s)."
        )
    else:
        frases.append(
            "Não há evidência de vivência no setor de energia no currículo."
        )

    certificados = [
        item["nome"]
        for item in certificacoes.get("detalhe", {}).get("encontradas", [])
    ]
    if certificados:
        frases.append(
            "Certificações declaradas: "
            f"{', '.join(certificados)}; sem comprovante anexo (N0), não "
            "pontuam."
        )
    elif educacao.get("score", 0) >= 100:
        frases.append("Pós-graduação identificada no currículo.")

    pendencias = [
        f"{g.get('gate')} {ROTULO_STATUS.get(g.get('status'), g.get('status'))}"
        for g in resultado.detalhe.get("gates", [])
        if g.get("status") != "passou"
    ]
    triagem = ROTULO_STATUS.get(resultado.status_gates, resultado.status_gates)
    if pendencias:
        frases.append(
            f"Triagem {triagem}; pendências: {', '.join(pendencias)}. "
            f"Score final {resultado.pontuacao_final:.2f}"
            f" (ponderada {resultado.pontuacao_ponderada:.2f};"
            f" confiança {resultado.confianca:.1f}/100)."
        )
    else:
        frases.append(
            f"Triagem {triagem}, sem pendências de gates. "
            f"Score final {resultado.pontuacao_final:.2f}"
            f" (ponderada {resultado.pontuacao_ponderada:.2f};"
            f" confiança {resultado.confianca:.1f}/100)."
        )
    return frases[:5]
