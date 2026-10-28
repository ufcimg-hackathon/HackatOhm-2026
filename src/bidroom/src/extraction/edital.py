"""Etapa 4 — leitura de edital `.docx` por regras, com rastreabilidade.

O parser não decide a demanda: ele propõe requisitos e marca o que é ambíguo.
Cada proposta guarda trecho, localização, página estimada, escopo e a regra
interpretada. O responsável revisa antes da ativação.

Formatos: `.docx` no MVP. Documento sem texto extraível (digitalizado) não é
silenciado: vira requisito de `tratamento_documento` para revisão/OCR.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from src.extraction.parser_docx import CHARS_POR_PAGINA, pagina_estimada
from src.extraction.regras import CERTIFICACOES, COMPETENCIAS

TIPOS = {
    "papel",
    "vaga",
    "competencia",
    "experiencia_minima",
    "certificacao",
    "comprovante",
    "periodo_trabalho",
    "criterio_eliminatorio",
    "pontuacao",
    "limite",
    "regra_combinacao",
    "tratamento_documento",
    "outro",
}

TERMOS_AMBIGUOS = (
    "a definir",
    "a criterio",
    "conforme necessidade",
    "conforme aplicavel",
    "quando aplicavel",
    "se houver",
    "relevante",
    "equivalente",
    "similar",
    "etc",
    "a combinar",
    "nao especificado",
    "subjetiv",
    "qualitativ",
    "preferencialmente",
    "podera",
)

PALAVRAS_EMPRESA = (
    "empresa",
    "contratada",
    "fornecedor",
    "licitante",
    "proponente",
    "organizacao",
)
PALAVRAS_EQUIPE = ("equipe", "time", "coletivo", "profissionais certificados")
PALAVRAS_INDIVIDUAL = ("individual", "candidato", "profissional")


@dataclass
class RequisitoExtraido:
    tipo: str
    escopo: str
    chave: str | None
    descricao: str
    regra: dict
    trecho: str
    localizacao: str
    pagina: int
    obrigatorio: bool = True
    ambiguidade: bool = False
    ambiguidade_motivo: str | None = None


@dataclass
class EditalExtraido:
    texto: str
    n_paginas: int
    requisitos: list[RequisitoExtraido] = field(default_factory=list)
    alertas: list[str] = field(default_factory=list)


@dataclass
class _Bloco:
    tipo: str  # paragrafo | tabela
    indice: int
    texto: str
    posicao: int
    linhas: list[list[str]] | None = None


def _norm(texto: str) -> str:
    return (
        unicodedata.normalize("NFKD", texto)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
        .strip()
    )


def _slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _norm(texto)).strip("_") or "requisito"


def _alias_map(origem: dict[str, list[str]]) -> dict[str, str]:
    mapa: dict[str, str] = {}
    for canonical, aliases in origem.items():
        mapa[_norm(canonical)] = canonical
        for alias in aliases:
            mapa[_norm(alias)] = canonical
    return mapa


ALIASES_COMPETENCIA = _alias_map(COMPETENCIAS)
ALIASES_CERTIFICACAO = _alias_map(CERTIFICACOES)


def _canonical(item: str, mapa: dict[str, str]) -> str:
    limpo = item.strip(" \t-–—|,:;.")
    if not limpo:
        return ""
    n = _norm(limpo)
    if n in mapa:
        return mapa[n]
    for alias in sorted(mapa, key=len, reverse=True):
        if re.search(r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])", n):
            return mapa[alias]
    return limpo[:160]


def _split_itens(texto: str) -> list[str]:
    itens: list[str] = []
    for parte in re.split(r"[;|]|,\s*|\s+e\s+|\s*/\s*", texto):
        item = parte.strip(" \t-–—|,:;.")
        if not item:
            continue
        if _norm(item) in {"comprovacao", "comprovante", "documento"}:
            continue
        itens.append(item[:160])
    vistos: set[str] = set()
    resultado: list[str] = []
    for item in itens:
        chave = _norm(item)
        if chave and chave not in vistos:
            vistos.add(chave)
            resultado.append(item)
    return resultado


def _escopo(texto: str, secao: str = "individual") -> str:
    n = _norm(texto)
    if any(palavra in n for palavra in PALAVRAS_EMPRESA):
        return "empresa"
    if any(palavra in n for palavra in PALAVRAS_EQUIPE):
        return "equipe"
    if any(palavra in n for palavra in PALAVRAS_INDIVIDUAL):
        return "individual"
    return secao


def _secao_heading(linha: str) -> str | None:
    if ":" in linha and linha.split(":", 1)[1].strip():
        return None
    n = _norm(linha).strip(" :.-")
    if len(linha) > 140:
        return None
    if not re.match(
        r"^(?:\d+[.)]\s*)?(?:requisitos?|criterios?|secao|disposicoes|objeto|"
        r"qualificacoes?|anexos?|tabela|papeis|perfis|cargos|vagas)\b",
        n,
    ):
        return None
    if any(palavra in n for palavra in PALAVRAS_EMPRESA):
        return "empresa"
    if any(palavra in n for palavra in PALAVRAS_EQUIPE):
        return "equipe"
    if any(palavra in n for palavra in PALAVRAS_INDIVIDUAL):
        return "individual"
    return "individual"


def _ambiguidade(texto: str, tipo: str, regra: dict) -> str | None:
    n = _norm(texto)
    for termo in TERMOS_AMBIGUOS:
        if termo in n:
            return f"termo aberto: {termo}"
    if tipo == "experiencia_minima" and regra.get("anos") is None and regra.get("meses") is None:
        return "valor numérico de experiência não especificado"
    if tipo in ("vaga", "limite") and regra.get("quantidade") is None and regra.get("valor") is None:
        return "quantidade não especificada"
    if tipo == "pontuacao" and regra.get("pontos") is None:
        return "pontuação não especificada"
    if tipo == "papel" and not regra.get("nome"):
        return "papel não identificado"
    return None


def _adicionar(
    requisitos: list[RequisitoExtraido],
    vistos: set[tuple],
    requisito: RequisitoExtraido,
) -> bool:
    chave = (
        requisito.tipo,
        requisito.escopo,
        requisito.chave or "",
        _norm(requisito.descricao)[:160],
    )
    if chave in vistos:
        return False
    vistos.add(chave)
    requisitos.append(requisito)
    return True


def _novo(
    *,
    tipo: str,
    escopo: str,
    chave: str | None,
    descricao: str,
    regra: dict,
    trecho: str,
    localizacao: str,
    pagina: int,
    obrigatorio: bool = True,
    ambiguidade: bool = False,
    ambiguidade_motivo: str | None = None,
) -> RequisitoExtraido:
    return RequisitoExtraido(
        tipo=tipo,
        escopo=escopo,
        chave=chave,
        descricao=descricao.strip()[:500],
        regra=regra,
        trecho=trecho.strip()[:500],
        localizacao=localizacao,
        pagina=pagina,
        obrigatorio=obrigatorio,
        ambiguidade=ambiguidade,
        ambiguidade_motivo=ambiguidade_motivo,
    )


def _parse_vagas(texto: str) -> int | None:
    n = _norm(texto)
    m = re.fullmatch(r"\s*(\d+)\s*", n)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)\s*vagas?\b", n)
    if m:
        return int(m.group(1))
    palavras = {
        "uma": 1,
        "um": 1,
        "duas": 2,
        "dois": 2,
        "tres": 3,
        "quatro": 4,
        "cinco": 5,
    }
    m = re.search(r"\b(" + "|".join(palavras) + r")\s+vaga", n)
    if m:
        return palavras[m.group(1)]
    if "vaga" in n:
        return None
    return None


def _limpar_nome_papel(nome: str) -> str:
    nome = re.split(r"[;|]", nome)[0].strip()
    nome = re.sub(
        r"\s*[-–—]\s*\d+\s*vagas?.*$", "", nome, flags=re.IGNORECASE
    ).strip()
    nome = re.sub(r"\s+(?:com|e)\s+\d+\s*vagas?.*$", "", nome, flags=re.I).strip()
    return nome.strip(" \t-–—:.,")[:120]


def _parece_papel(nome: str) -> bool:
    n = _norm(nome)
    return bool(
        re.search(
            r"\b(pm|scrum|ba|bsa|java|bi|data|analista|gerente|desenvolvedor|"
            r"arquiteto|qa|teste|product owner|tech lead|cientista|engenheiro)\b",
            n,
        )
    )


def _slug_papel(nome: str) -> str:
    n = _norm(nome)
    if re.search(r"\b(pm|scrum|gerente de projetos|project manager)\b", n):
        return "pm_scrum"
    if re.search(r"\b(ba|bsa|business analyst|analista de negocios)\b", n):
        return "ba_bsa"
    if re.search(r"\bjava\b", n):
        return "java"
    if re.search(r"\b(bi|data|analista de dados|data analyst)\b", n):
        return "bi_data"
    return _slug(nome)


ROLE_PREFIX_RE = re.compile(
    r"(?:papel|perfil|cargo|fun[cç][aã]o)\s*[:\-–]\s*(.+)", re.IGNORECASE
)
ROLE_LINE_RE = re.compile(
    r"^(?P<nome>[A-Za-zÀ-ÿ0-9/ .+&-]{3,80}?)\s*[-–—:]\s*"
    r"(?P<vagas>\d+)\s*vagas?\b",
    re.IGNORECASE,
)
EXP_MIN_RE = re.compile(
    r"(?:experi[eê]ncia)[^.;\n]{0,50}?(?:m[ií]nim[ao])?"
    r"[^.;\n]{0,30}?(\d+(?:[.,]\d+)?)\s*(anos?|meses?)",
    re.IGNORECASE,
)
MIN_EXP_RE = re.compile(
    r"m[ií]nim[oa]\s+de\s+(\d+(?:[.,]\d+)?)\s*(anos?|meses?)"
    r"[^.;\n]{0,30}?(?:experi[eê]ncia)?",
    re.IGNORECASE,
)
CERT_RE = re.compile(
    r"(?:certifica[cç][aã]o|certifica[cç][oõ]es|certificad[oa]s?|credencial)\s*"
    r"(obrigat[óo]ri[ao]|desej[áa]vel|diferencial|preferencial)?"
    r"\s*[:\-–]\s*(.+)",
    re.IGNORECASE,
)
CERT_KEYWORD_RE = re.compile(
    r"certifica[cç][aã]o|certifica[cç][oõ]es|certificad[oa]s?",
    re.IGNORECASE,
)
COMP_RE = re.compile(
    r"(?:compet[eê]ncias?|conhecimentos?|habilidades?|requisitos?\s+t[eé]cnicos?)"
    r"\s*(obrigat[óo]ri[ao]s?|desej[áa]veis?|diferenciais?)?"
    r"\s*[:\-–]?\s*(.+)",
    re.IGNORECASE,
)
COMPROVANTE_RE = re.compile(
    r"(?:comprova[cç][aã]o|comprovante|documento\s+comprobat[óo]rio|"
    r"documenta[cç][aã]o\s+exigida)\s*[:\-–]?\s*(.+)",
    re.IGNORECASE,
)
PERIODO_RE = re.compile(
    r"(?:per[ií]odo|dura[cç][aã]o|prazo)\s*"
    r"(?:de\s+trabalho|da\s+aloca[cç][aã]o|do\s+contrato)?"
    r"\s*[:\-–]?\s*(.+)",
    re.IGNORECASE,
)
ELIMINATORIO_RE = re.compile(
    r"(?:crit[eé]rio\s+eliminat[óo]rio|eliminat[óo]rio|elimina[cç][aã]o|"
    r"desclassifica[cç][aã]o|impedimento|vedado|n[ãa]o\s+ser[áa]\s+aceito)"
    r"\s*[:\-–]?\s*(.+)",
    re.IGNORECASE,
)
PONTUACAO_RE = re.compile(
    r"(?:pontua[cç][aã]o|pontos?|nota|peso)\s*"
    r"(?:m[áa]xim[ao]|m[íi]nim[ao]|total)?\s*[:\-–]?\s*(.+)",
    re.IGNORECASE,
)
LIMITE_RE = re.compile(
    r"(?:limite|m[áa]ximo(?:\s+de)?|m[íi]nimo\s+de\s+candidatos|"
    r"quantidade\s+m[áa]xima|teto)\s*[:\-–]?\s*(.+)",
    re.IGNORECASE,
)
COMBINACAO_RE = re.compile(
    r"(?:regra\s+de\s+combina[cç][aã]o|combina[cç][aã]o|"
    r"complementaridade|perfil\s+complementar)\s*[:\-–]?\s*(.+)",
    re.IGNORECASE,
)


def _parse_papel(
    linha: str,
    n: str,
    escopo: str,
    localizacao: str,
    pagina: int,
    requisitos: list[RequisitoExtraido],
    vistos: set[tuple],
) -> bool:
    nome = ""
    m = ROLE_PREFIX_RE.search(linha)
    if m:
        nome = _limpar_nome_papel(m.group(1))
    else:
        m = ROLE_LINE_RE.search(linha)
        if m and _parece_papel(m.group("nome")):
            nome = _limpar_nome_papel(m.group("nome"))
    if not nome or not _parece_papel(nome):
        if "vaga" in n and re.match(
            r"^(?:vagas?|quantidade\s+de\s+vagas|n[úu]mero\s+de\s+vagas)\b", n
        ):
            vagas = _parse_vagas(linha)
            motivo = _ambiguidade(linha, "vaga", {"quantidade": vagas})
            _adicionar(
                requisitos,
                vistos,
                _novo(
                    tipo="vaga",
                    escopo=escopo,
                    chave="vaga",
                    descricao=linha,
                    regra={"quantidade": vagas, "texto": linha[:300]},
                    trecho=linha,
                    localizacao=localizacao,
                    pagina=pagina,
                    obrigatorio=True,
                    ambiguidade=motivo is not None,
                    ambiguidade_motivo=motivo,
                ),
            )
            return True
        return False

    vagas = _parse_vagas(linha)
    sigla = _slug_papel(nome)
    motivo = _ambiguidade(linha, "papel", {"nome": nome}) if vagas is None else None
    _adicionar(
        requisitos,
        vistos,
        _novo(
            tipo="papel",
            escopo=escopo,
            chave=sigla,
            descricao=(
                f"Papel {nome}"
                + (f" — {vagas} vaga(s)" if vagas is not None else " — vagas a definir")
            ),
            regra={"nome": nome, "sigla": sigla, "vagas": vagas},
            trecho=linha,
            localizacao=localizacao,
            pagina=pagina,
            obrigatorio=True,
            ambiguidade=motivo is not None,
            ambiguidade_motivo=motivo,
        ),
    )
    return True


def _parse_experiencia(
    linha: str,
    n: str,
    escopo: str,
    localizacao: str,
    pagina: int,
    requisitos: list[RequisitoExtraido],
    vistos: set[tuple],
) -> bool:
    if "experiencia" not in n:
        return False
    if n.startswith(("comprovacao", "documento comprobatorio")):
        return False
    tem_minimo = (
        "minim" in n
        or n.startswith("experiencia")
        or "experiencia minima" in n
    )
    tem_numero = bool(re.search(r"\d+(?:[.,]\d+)?\s*(?:anos?|meses?)", n))
    if not (tem_minimo or tem_numero):
        return False
    m = EXP_MIN_RE.search(linha) or MIN_EXP_RE.search(linha)
    anos: float | None = None
    meses: float | None = None
    if m:
        valor = float(m.group(1).replace(",", "."))
        unidade = _norm(m.group(2))
        if unidade.startswith("mes"):
            meses = valor
        else:
            anos = valor
    abrangencia = (
        "papel"
        if any(k in n for k in ("no papel", "na funcao", "para o papel"))
        else "total"
    )
    regra = {
        "anos": anos,
        "meses": meses,
        "abrangencia": abrangencia,
        "texto": linha[:300],
    }
    motivo = _ambiguidade(linha, "experiencia_minima", regra)
    obrigatorio = not any(k in n for k in ("desej", "diferencial", "preferencial"))
    _adicionar(
        requisitos,
        vistos,
        _novo(
            tipo="experiencia_minima",
            escopo=escopo,
            chave=f"experiencia_{abrangencia}",
            descricao=linha,
            regra=regra,
            trecho=linha,
            localizacao=localizacao,
            pagina=pagina,
            obrigatorio=obrigatorio,
            ambiguidade=motivo is not None,
            ambiguidade_motivo=motivo,
        ),
    )
    return True


def _parse_certificacao(
    linha: str,
    n: str,
    escopo: str,
    localizacao: str,
    pagina: int,
    requisitos: list[RequisitoExtraido],
    vistos: set[tuple],
) -> bool:
    if n.startswith(
        ("comprovacao", "comprovante", "documento comprobatorio", "documentacao exigida")
    ):
        return False
    m = CERT_RE.search(linha)
    itens_texto = m.group(2) if m else ""
    if not m:
        palavra = CERT_KEYWORD_RE.search(linha)
        if palavra is None:
            return False
        itens_texto = linha[palavra.end() :].strip(" .:-–—")
        if not itens_texto:
            return False
    itens = [
        _canonical(item, ALIASES_CERTIFICACAO)
        for item in _split_itens(itens_texto)
        if "comprova" not in _norm(item)
    ]
    itens = [item for item in itens if item]
    motivo = None if itens else "certificação sem nome identificado"
    if not itens:
        itens = ["certificação não identificada"]
    obrigatorio = not any(k in n for k in ("desej", "diferencial", "preferencial"))
    quantidade = None
    m_qtd = re.search(r"(?:pelo menos|no m[ií]nimo|m[ií]nimo de)\s+(\d+)", n)
    if m_qtd:
        quantidade = int(m_qtd.group(1))
    regra = {
        "itens": itens,
        "quantidade": quantidade,
        "pontua": escopo == "individual",
        "comprovante": "comprova" in n,
        "texto": linha[:300],
    }
    _adicionar(
        requisitos,
        vistos,
        _novo(
            tipo="certificacao",
            escopo=escopo,
            chave="cert:" + ";".join(_slug(item) for item in itens),
            descricao=linha,
            regra=regra,
            trecho=linha,
            localizacao=localizacao,
            pagina=pagina,
            obrigatorio=obrigatorio,
            ambiguidade=motivo is not None,
            ambiguidade_motivo=motivo,
        ),
    )
    return True


def _parse_competencia(
    linha: str,
    n: str,
    escopo: str,
    localizacao: str,
    pagina: int,
    requisitos: list[RequisitoExtraido],
    vistos: set[tuple],
) -> bool:
    if any(k in n for k in ("certifica", "comprova", "eliminatorio", "pontuacao")):
        return False
    m = COMP_RE.search(linha)
    if not m:
        return False
    itens = [
        _canonical(item, ALIASES_COMPETENCIA)
        for item in _split_itens(m.group(2))
    ]
    itens = [item for item in itens if item]
    motivo = None if itens else "competências não identificadas"
    if not itens:
        itens = ["competência não identificada"]
    obrigatorio = not any(k in n for k in ("desej", "diferencial", "preferencial"))
    regra = {
        "itens": itens,
        "operador": "todas" if obrigatorio else "qualquer",
        "pontua": escopo == "individual",
        "texto": linha[:300],
    }
    _adicionar(
        requisitos,
        vistos,
        _novo(
            tipo="competencia",
            escopo=escopo,
            chave="comp:" + ";".join(_slug(item) for item in itens),
            descricao=linha,
            regra=regra,
            trecho=linha,
            localizacao=localizacao,
            pagina=pagina,
            obrigatorio=obrigatorio,
            ambiguidade=motivo is not None,
            ambiguidade_motivo=motivo,
        ),
    )
    return True


def _parse_comprovante(
    linha: str,
    n: str,
    escopo: str,
    localizacao: str,
    pagina: int,
    requisitos: list[RequisitoExtraido],
    vistos: set[tuple],
) -> bool:
    m = COMPROVANTE_RE.search(linha)
    if not m:
        return False
    itens = [
        _canonical(item, ALIASES_CERTIFICACAO)
        for item in _split_itens(m.group(1))
    ]
    itens = [item for item in itens if item]
    motivo = None if itens else "comprovante sem item identificado"
    regra = {
        "itens": itens or ["comprovante não identificado"],
        "formato": "documento",
        "pontua": False,
        "texto": linha[:300],
    }
    _adicionar(
        requisitos,
        vistos,
        _novo(
            tipo="comprovante",
            escopo=escopo,
            chave="comprovante:" + ";".join(_slug(item) for item in regra["itens"]),
            descricao=linha,
            regra=regra,
            trecho=linha,
            localizacao=localizacao,
            pagina=pagina,
            obrigatorio=True,
            ambiguidade=motivo is not None,
            ambiguidade_motivo=motivo,
        ),
    )
    return True


def _parse_periodo(
    linha: str,
    n: str,
    escopo: str,
    localizacao: str,
    pagina: int,
    requisitos: list[RequisitoExtraido],
    vistos: set[tuple],
) -> bool:
    m = PERIODO_RE.search(linha)
    if not m:
        return False
    texto = m.group(1)
    duracao = None
    m_dur = re.search(r"(\d+)\s*(meses?|anos?)", _norm(texto))
    if m_dur:
        valor = int(m_dur.group(1))
        duracao = valor if m_dur.group(2).startswith("mes") else valor * 12
    dedicacao = None
    if "100%" in texto or "integral" in _norm(texto):
        dedicacao = 100
    regra = {
        "duracao_meses": duracao,
        "dedicacao": dedicacao,
        "texto": texto[:300],
    }
    motivo = None
    if duracao is None:
        motivo = "duração não especificada"
    _adicionar(
        requisitos,
        vistos,
        _novo(
            tipo="periodo_trabalho",
            escopo=escopo,
            chave="periodo_trabalho",
            descricao=linha,
            regra=regra,
            trecho=linha,
            localizacao=localizacao,
            pagina=pagina,
            obrigatorio=True,
            ambiguidade=motivo is not None,
            ambiguidade_motivo=motivo,
        ),
    )
    return True


def _parse_eliminatorio(
    linha: str,
    n: str,
    escopo: str,
    localizacao: str,
    pagina: int,
    requisitos: list[RequisitoExtraido],
    vistos: set[tuple],
) -> bool:
    m = ELIMINATORIO_RE.search(linha)
    if not m:
        return False
    texto = m.group(1).strip()
    regra = {"texto": texto[:300], "pontua": False}
    motivo = _ambiguidade(linha, "criterio_eliminatorio", regra)
    _adicionar(
        requisitos,
        vistos,
        _novo(
            tipo="criterio_eliminatorio",
            escopo=escopo,
            chave="eliminatorio:" + _slug(texto),
            descricao=linha,
            regra=regra,
            trecho=linha,
            localizacao=localizacao,
            pagina=pagina,
            obrigatorio=True,
            ambiguidade=motivo is not None,
            ambiguidade_motivo=motivo,
        ),
    )
    return True


def _parse_pontuacao(
    linha: str,
    n: str,
    escopo: str,
    localizacao: str,
    pagina: int,
    requisitos: list[RequisitoExtraido],
    vistos: set[tuple],
) -> bool:
    m = PONTUACAO_RE.search(linha)
    if not m:
        return False
    texto = m.group(1).strip()
    m_pontos = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:pontos?|pts?)\b", _norm(texto))
    pontos = float(m_pontos.group(1).replace(",", ".")) if m_pontos else None
    regra = {
        "criterio": texto[:300],
        "pontos": pontos,
        "pontua": escopo != "empresa",
    }
    motivo = _ambiguidade(linha, "pontuacao", regra)
    _adicionar(
        requisitos,
        vistos,
        _novo(
            tipo="pontuacao",
            escopo=escopo,
            chave="pontuacao:" + _slug(texto),
            descricao=linha,
            regra=regra,
            trecho=linha,
            localizacao=localizacao,
            pagina=pagina,
            obrigatorio=False,
            ambiguidade=motivo is not None,
            ambiguidade_motivo=motivo,
        ),
    )
    return True


def _parse_limite(
    linha: str,
    n: str,
    escopo: str,
    localizacao: str,
    pagina: int,
    requisitos: list[RequisitoExtraido],
    vistos: set[tuple],
) -> bool:
    if any(chave in n for chave in ("pontuacao", "nota", "peso")):
        return False
    m = LIMITE_RE.search(linha)
    if not m:
        return False
    texto = m.group(1).strip()
    m_valor = re.search(r"(\d+)", _norm(texto))
    valor = int(m_valor.group(1)) if m_valor else None
    regra = {"texto": texto[:300], "valor": valor}
    motivo = _ambiguidade(linha, "limite", regra)
    _adicionar(
        requisitos,
        vistos,
        _novo(
            tipo="limite",
            escopo=escopo,
            chave="limite:" + _slug(texto),
            descricao=linha,
            regra=regra,
            trecho=linha,
            localizacao=localizacao,
            pagina=pagina,
            obrigatorio=True,
            ambiguidade=motivo is not None,
            ambiguidade_motivo=motivo,
        ),
    )
    return True


def _parse_combinacao(
    linha: str,
    n: str,
    escopo: str,
    localizacao: str,
    pagina: int,
    requisitos: list[RequisitoExtraido],
    vistos: set[tuple],
) -> bool:
    m = COMBINACAO_RE.search(linha)
    if not m:
        return False
    texto = m.group(1).strip()
    escopo_regra = "equipe" if escopo == "individual" else escopo
    regra = {"texto": texto[:300], "pontua": False}
    motivo = _ambiguidade(linha, "regra_combinacao", regra)
    _adicionar(
        requisitos,
        vistos,
        _novo(
            tipo="regra_combinacao",
            escopo=escopo_regra,
            chave="combinacao:" + _slug(texto),
            descricao=linha,
            regra=regra,
            trecho=linha,
            localizacao=localizacao,
            pagina=pagina,
            obrigatorio=True,
            ambiguidade=motivo is not None,
            ambiguidade_motivo=motivo,
        ),
    )
    return True


def _parse_linha(
    linha: str,
    localizacao: str,
    pagina: int,
    secao: str,
    requisitos: list[RequisitoExtraido],
    vistos: set[tuple],
) -> None:
    if not linha.strip():
        return
    n = _norm(linha)
    escopo = _escopo(linha, secao)
    criou = False
    for parser in (
        _parse_papel,
        _parse_experiencia,
        _parse_certificacao,
        _parse_competencia,
        _parse_comprovante,
        _parse_periodo,
        _parse_eliminatorio,
        _parse_pontuacao,
        _parse_limite,
        _parse_combinacao,
    ):
        if parser(linha, n, escopo, localizacao, pagina, requisitos, vistos):
            criou = True
    if criou:
        return
    obrigatorio = any(
        chave in n
        for chave in (
            "obrigat",
            "exig",
            "deve",
            "minim",
            "eliminat",
            "vedado",
            "imped",
            "nao sera aceito",
            "necessario",
            "maximo",
        )
    )
    opcional = any(chave in n for chave in ("desej", "diferencial", "preferencial"))
    if not (obrigatorio or opcional):
        return
    motivo = "tipo de requisito não reconhecido; revisar interpretação"
    _adicionar(
        requisitos,
        vistos,
        _novo(
            tipo="outro",
            escopo=escopo,
            chave="outro:" + _slug(linha),
            descricao=linha,
            regra={"texto": linha[:500], "pontua": False},
            trecho=linha,
            localizacao=localizacao,
            pagina=pagina,
            obrigatorio=obrigatorio,
            ambiguidade=True,
            ambiguidade_motivo=motivo,
        ),
    )


def _parse_tabela(
    bloco: _Bloco,
    texto: str,
    n_paginas: int,
    secao: str,
    requisitos: list[RequisitoExtraido],
    vistos: set[tuple],
    alertas: list[str],
) -> None:
    linhas = [[celula.strip() for celula in linha] for linha in (bloco.linhas or [])]
    if not linhas:
        return
    cabecalho = [_norm(celula) for celula in linhas[0]]
    pagina = pagina_estimada(texto, bloco.posicao, n_paginas)
    escopo_tabela = _escopo(bloco.texto, secao)

    def achar(*termos: str) -> int | None:
        for indice, celula in enumerate(cabecalho):
            if any(termo in celula for termo in termos):
                return indice
        return None

    papel_idx = achar("papel", "perfil", "cargo", "funcao")
    vaga_idx = achar("vaga", "quantidade", "qtd")
    comp_idx = achar("compet", "conhec", "habil")
    criterio_idx = achar("criterio", "requisito")
    pontos_idx = achar("ponto", "peso", "nota")

    if papel_idx is not None and vaga_idx is not None:
        for numero_linha, linha in enumerate(linhas[1:], start=2):
            if papel_idx >= len(linha):
                continue
            nome = _limpar_nome_papel(linha[papel_idx])
            if not nome:
                continue
            vagas = _parse_vagas(
                linha[vaga_idx] if vaga_idx < len(linha) else " | ".join(linha)
            )
            localizacao = f"tabela {bloco.indice}, linha {numero_linha}"
            sigla = _slug_papel(nome)
            motivo = (
                _ambiguidade(nome, "papel", {"nome": nome})
                if vagas is None
                else None
            )
            _adicionar(
                requisitos,
                vistos,
                _novo(
                    tipo="papel",
                    escopo=escopo_tabela,
                    chave=sigla,
                    descricao=(
                        f"Papel {nome}"
                        + (
                            f" — {vagas} vaga(s)"
                            if vagas is not None
                            else " — vagas a definir"
                        )
                    ),
                    regra={"nome": nome, "sigla": sigla, "vagas": vagas},
                    trecho=" | ".join(linha),
                    localizacao=localizacao,
                    pagina=pagina,
                    obrigatorio=True,
                    ambiguidade=motivo is not None,
                    ambiguidade_motivo=motivo,
                ),
            )
            if comp_idx is not None and comp_idx < len(linha):
                itens = [
                    _canonical(item, ALIASES_COMPETENCIA)
                    for item in _split_itens(linha[comp_idx])
                ]
                itens = [item for item in itens if item]
                if itens:
                    _adicionar(
                        requisitos,
                        vistos,
                        _novo(
                            tipo="competencia",
                            escopo=escopo_tabela,
                            chave=f"comp:{sigla}:" + ";".join(
                                _slug(item) for item in itens
                            ),
                            descricao=(
                                f"Competências de {nome}: {', '.join(itens)}"
                            ),
                            regra={
                                "itens": itens,
                                "operador": "todas",
                                "papel": sigla,
                                "pontua": escopo_tabela == "individual",
                            },
                            trecho=" | ".join(linha),
                            localizacao=localizacao,
                            pagina=pagina,
                            obrigatorio=True,
                        ),
                    )
        return

    if criterio_idx is not None and pontos_idx is not None:
        for numero_linha, linha in enumerate(linhas[1:], start=2):
            if criterio_idx >= len(linha):
                continue
            criterio = linha[criterio_idx]
            pontos_texto = linha[pontos_idx] if pontos_idx < len(linha) else ""
            m_pontos = re.search(r"(\d+(?:[.,]\d+)?)", pontos_texto)
            pontos = (
                float(m_pontos.group(1).replace(",", ".")) if m_pontos else None
            )
            regra = {"criterio": criterio, "pontos": pontos, "pontua": True}
            motivo = None if pontos is not None else "pontuação não especificada"
            _adicionar(
                requisitos,
                vistos,
                _novo(
                    tipo="pontuacao",
                    escopo=escopo_tabela,
                    chave="pontuacao:" + _slug(criterio),
                    descricao=f"Pontuação: {criterio} — {pontos_texto}",
                    regra=regra,
                    trecho=" | ".join(linha),
                    localizacao=f"tabela {bloco.indice}, linha {numero_linha}",
                    pagina=pagina,
                    obrigatorio=False,
                    ambiguidade=motivo is not None,
                    ambiguidade_motivo=motivo,
                ),
            )
        return

    criou = False
    for numero_linha, linha in enumerate(linhas, start=1):
        antes = len(requisitos)
        _parse_linha(
            " | ".join(linha),
            f"tabela {bloco.indice}, linha {numero_linha}",
            pagina,
            secao,
            requisitos,
            vistos,
        )
        if len(requisitos) > antes:
            criou = True
    if not criou:
        alerta = f"Tabela {bloco.indice} não interpretada automaticamente"
        alertas.append(alerta)
        _adicionar(
            requisitos,
            vistos,
            _novo(
                tipo="tratamento_documento",
                escopo=escopo_tabela,
                chave=f"tabela_{bloco.indice}",
                descricao=alerta + "; revisar manualmente ou aplicar OCR.",
                regra={"texto": bloco.texto[:500], "tratamento": "revisao_manual"},
                trecho=bloco.texto,
                localizacao=f"tabela {bloco.indice}",
                pagina=pagina,
                obrigatorio=True,
                ambiguidade=True,
                ambiguidade_motivo="tabela difícil; interpretação humana necessária",
            ),
        )


def _iter_blocos(doc: Document) -> list[_Bloco]:
    blocos: list[_Bloco] = []
    posicao = 0
    indice_paragrafo = 0
    indice_tabela = 0
    for filho in doc.element.body.iterchildren():
        tag = filho.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragrafo = Paragraph(filho, doc)
            texto = paragrafo.text.strip()
            if not texto:
                continue
            indice_paragrafo += 1
            blocos.append(
                _Bloco(
                    tipo="paragrafo",
                    indice=indice_paragrafo,
                    texto=texto,
                    posicao=posicao,
                )
            )
            posicao += len(texto) + 1
        elif tag == "tbl":
            tabela = Table(filho, doc)
            indice_tabela += 1
            linhas = [
                [celula.text.strip() for celula in linha.cells]
                for linha in tabela.rows
            ]
            texto = "\n".join(
                " | ".join(celula for celula in linha if celula)
                for linha in linhas
                if any(linha)
            )
            if not texto:
                continue
            blocos.append(
                _Bloco(
                    tipo="tabela",
                    indice=indice_tabela,
                    texto=texto,
                    posicao=posicao,
                    linhas=linhas,
                )
            )
            posicao += len(texto) + 1
    return blocos


def extrair_edital(caminho: str | Path) -> EditalExtraido:
    doc = Document(str(caminho))
    blocos = _iter_blocos(doc)
    texto = "\n".join(bloco.texto for bloco in blocos)
    n_paginas = max(1, math.ceil(len(texto) / CHARS_POR_PAGINA))
    requisitos: list[RequisitoExtraido] = []
    vistos: set[tuple] = set()
    alertas: list[str] = []
    secao = "individual"
    for bloco in blocos:
        if bloco.tipo == "paragrafo":
            nova_secao = _secao_heading(bloco.texto)
            if nova_secao:
                secao = nova_secao
                continue
            _parse_linha(
                bloco.texto,
                f"parágrafo {bloco.indice}",
                pagina_estimada(texto, bloco.posicao, n_paginas),
                secao,
                requisitos,
                vistos,
            )
        else:
            _parse_tabela(
                bloco,
                texto,
                n_paginas,
                secao,
                requisitos,
                vistos,
                alertas,
            )
    if not texto.strip():
        alertas.append("Documento sem texto extraível")
        _adicionar(
            requisitos,
            vistos,
            _novo(
                tipo="tratamento_documento",
                escopo="individual",
                chave="documento_sem_texto",
                descricao=(
                    "Documento sem texto extraível; aplicar OCR ou revisão "
                    "manual antes de ativar a demanda."
                ),
                regra={"tratamento": "ocr_ou_revisao_manual"},
                trecho="(documento sem texto)",
                localizacao="documento",
                pagina=1,
                obrigatorio=True,
                ambiguidade=True,
                ambiguidade_motivo="texto não extraído do documento",
            ),
        )
    return EditalExtraido(
        texto=texto,
        n_paginas=n_paginas,
        requisitos=requisitos,
        alertas=alertas,
    )
