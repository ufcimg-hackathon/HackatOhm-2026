"""Fase 2 — extração por regras (offline-first; handoff §5.2).

Nenhum LLM: listas de termos + aliases, patterns de período e palavras-chave.
Cada fato extraído carrega evidência (trecho + página + nível + confiança).

Níveis vigentes na v1 (escala §5):
- certificações nascem **N0** (nenhuma da base tem anexo);
- demais fatos nascem **N1** (autodeclarado com datas/empresa).
N2+ exigem projeto/cliente nomeado ou documento — refinamento na Fase 3.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.extraction.parser_docx import pagina_estimada

# --- taxonomia v1 ---

COMPETENCIAS: dict[str, list[str]] = {
    "Java": ["java", "j2ee"],
    "Spring": ["spring", "spring boot"],
    "APIs REST": ["api", "apis", "rest", "web service", "webservice"],
    "SQL": ["sql", "pl/sql"],
    "Python": ["python"],
    "Power BI": ["power bi", "powerbi"],
    "Tableau": ["tableau"],
    "Excel": ["excel", "planilhas"],
    "Scrum": ["scrum"],
    "Kanban": ["kanban"],
    "Gestão de projetos": [
        "gestão de projetos",
        "gestao de projetos",
        "gerenciamento de projetos",
        "project management",
        "program management",
        "project manager",
        "program manager",
        "agile project manager",
        "delivery manager",
        "project delivery",
        "project planning",
        "managed projects",
        "pmo",
    ],
    "ETL": ["etl"],
    "Spark": ["spark"],
    "Kafka": ["kafka"],
    "AWS": ["aws", "amazon web services"],
    "Azure": ["azure"],
    "GCP": ["gcp", "google cloud"],
    "Docker": ["docker"],
    "Kubernetes": ["kubernetes", "k8s"],
    "Git": ["git", "github", "gitlab"],
}

CERTIFICACOES: dict[str, list[str]] = {
    "PMP": ["pmp"],
    "CSM": ["csm", "certified scrum master"],
    "PSM": ["psm"],
    "CBAP": ["cbap"],
    "CCBA": ["ccba"],
    "AWS Certified": ["aws certified"],
    "OCP": ["ocp", "oracle certified"],
    "ITIL": ["itil"],
    "PMI-ACP": ["pmi-acp"],
}

ENERGIA = [
    "energia", "energy", "utility", "utilities", "pg&e", "ge energy",
    "wind", "solar", "smart grid", "transmissão", "distribuição",
    "óleo e gás", "oil & gas", "eólica", "eletricidade",
]

FORMACAO_KW = [
    "bacharelado", "licenciatura", "graduação", "graduacao", "mba", "mestrado",
    "master", "doutorado", "phd", "pós-graduação", "pos-graduacao",
    "especialização", "especializacao", "universidade", "university", "college",
    "faculdade", "b.tech", "btech", "b.e.", "b.sc", "bsc", "bachelor", "degree",
    "education",
]

TITULOS_SECAO = {
    "experience", "professional experience", "work experience",
    "employment history", "career history", "experiência", "experiencia",
    "experiência profissional", "experiencia profissional",
    "histórico profissional", "historico profissional",
}

PROJETO_CLIENTE_RE = re.compile(
    r"(?:client|cliente|project|projeto)\s*:\s*[A-Z0-9]", re.IGNORECASE
)

IDIOMAS_KW = {
    "Português": ["português", "portugues", "portuguese"],
    "Inglês": ["inglês", "ingles", "english"],
    "Espanhol": ["espanhol", "spanish"],
    "Francês": ["francês", "frances", "french"],
}

NIVEIS_IDIOMA = [
    "fluente", "fluent", "avançado", "avancado", "advanced", "intermediário",
    "intermediario", "intermediate", "básico", "basico", "basic", "nativo",
    "native",
]

MES_RE = (
    r"(?:jan(?:uary)?|fev(?:ereiro)?|feb(?:ruary)?|mar(?:ch|ço)?|abr(?:il)?"
    r"|apr(?:il)?|mai(?:o)?|may|jun(?:e|ho)?|jul(?:y|ho)?|ago(?:sto)?"
    r"|aug(?:ust)?|set(?:embro)?|sep(?:t(?:ember)?)?|out(?:ubro)?|oct(?:ober)?"
    r"|nov(?:embro|ember)?|dez(?:embro)?|dec(?:ember)?)"
)
ANO_RE = r"(?:19|20)\d{2}"
DATA_RE = rf"(?:{MES_RE}\s*'?\s*\d{{2}}(?:\d{{2}})?|{ANO_RE}|\d{{1,2}}/\d{{4}})"
PERIODO_RE = re.compile(
    rf"({DATA_RE})\s*(?:-|–|—|a|até|to)\s*"
    rf"({DATA_RE}|atual|presente|present|current|hoje)",
    re.IGNORECASE,
)


def _limites_linha(texto: str, posicao: int) -> tuple[int, int]:
    inicio = texto.rfind("\n", 0, posicao) + 1
    fim = texto.find("\n", posicao)
    if fim == -1:
        fim = len(texto)
    return inicio, fim


def _trecho(texto: str, posicao: int) -> str:
    inicio, fim = _limites_linha(texto, posicao)
    return texto[inicio:fim].strip()[:400]


def _buscar_termos(texto: str, posicao: int, aliases: list[str]) -> bool:
    for alias in aliases:
        padrao = re.compile(
            r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
        if padrao.search(texto, posicao, posicao + 80):
            return True
    return False


@dataclass
class FatoExtraido:
    tipo: str  # competencia | experiencia | certificacao | formacao | idioma
    dados: dict
    trecho: str
    pagina: int
    nivel: int
    confianca: int


@dataclass
class Extracao:
    competencias: list[FatoExtraido] = field(default_factory=list)
    experiencias: list[FatoExtraido] = field(default_factory=list)
    certificacoes: list[FatoExtraido] = field(default_factory=list)
    formacoes: list[FatoExtraido] = field(default_factory=list)
    idiomas: list[FatoExtraido] = field(default_factory=list)
    residual: list[str] = field(default_factory=list)

    def evidencias(self) -> list[FatoExtraido]:
        return (
            self.competencias
            + self.experiencias
            + self.certificacoes
            + self.formacoes
            + self.idiomas
        )


def _extrair_competencias(texto: str, n_paginas: int) -> list[FatoExtraido]:
    achados: list[FatoExtraido] = []
    vistos: set[str] = set()
    for nome, aliases in COMPETENCIAS.items():
        for alias in aliases:
            padrao = re.compile(
                r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])",
                re.IGNORECASE,
            )
            achado = padrao.search(texto)
            if achado and nome not in vistos:
                vistos.add(nome)
                pos = achado.start()
                achados.append(
                    FatoExtraido(
                        tipo="competencia",
                        dados={"nome": nome, "anos": None, "familia": None},
                        trecho=_trecho(texto, pos),
                        pagina=pagina_estimada(texto, pos, n_paginas),
                        nivel=1,
                        confianca=25,
                    )
                )
                break
    return achados


def _bloco_experiencia(texto: str, inicio_linha: int) -> str:
    """Linhas seguintes até a próxima linha com período (ou 8 linhas)."""
    linhas = texto[inicio_linha:].splitlines()
    bloco: list[str] = []
    for i, linha in enumerate(linhas):
        if i > 0 and PERIODO_RE.search(linha):
            break
        bloco.append(linha)
        if len(bloco) >= 8:
            break
    return "\n".join(bloco)


def _linha_anterior(texto: str, posicao_linha: int) -> str | None:
    """Última linha não vazia antes da posição, ignorando títulos de seção."""
    linhas = [linha.strip() for linha in texto[:posicao_linha].splitlines()]
    for linha in reversed([l for l in linhas if l]):
        if linha.lower().strip(" :") in TITULOS_SECAO:
            continue
        return linha[:200]
    return None


RUIDO_RE = re.compile(
    r"^(?:client|location|project|from|to|role|designation|organization|"
    r"technologies|environment)\s*[:\-]",
    re.IGNORECASE,
)


def _limpar_rotulo(valor: str | None) -> str | None:
    """Descarta rótulos de projeto/seção e meses soltos como papel/empresa."""
    if not valor:
        return None
    valor = valor.strip(" \t-–—|,:;")
    valor = re.sub(r"\s+(?:from|to|at)$", "", valor, flags=re.IGNORECASE).strip()
    if not valor or valor.lower() in {"from", "to", "at", "client", "location"}:
        return None
    if RUIDO_RE.match(valor):
        return None
    if re.match(rf"^(?:{MES_RE})\b", valor, re.IGNORECASE) and len(valor) <= 12:
        return None
    return valor[:160].rstrip()


def _extrair_experiencias(texto: str, n_paginas: int) -> list[FatoExtraido]:
    achados: list[FatoExtraido] = []
    vistos: set[tuple[str | None, str]] = set()
    for achado in PERIODO_RE.finditer(texto):
        pos = achado.start()
        inicio, fim_linha = _limites_linha(texto, pos)
        linha = texto[inicio : min(fim_linha, inicio + 400)]
        # linha de formação também tem período — não é experiência profissional
        if any(kw in linha.lower() for kw in FORMACAO_KW):
            continue
        prefixo = texto[inicio : achado.start()].strip(" \t-–—|,:(").strip()
        sufixo = texto[achado.end() : fim_linha].strip(" \t-–—|,:()").strip()
        papel: str | None = None
        empresa: str | None = None
        if prefixo and (
            "client:" in prefixo.lower() or "location:" in prefixo.lower()
        ):
            # cabeçalho de projeto: "Client: X  Location: Y  <período>"
            cliente = re.search(r"client:\s*([^\t|]+)", prefixo, re.IGNORECASE)
            if cliente:
                empresa = cliente.group(1).strip(" \t-–—|,")
        elif prefixo:
            partes = re.split(r"\s+(?:at|em)\s+", prefixo, maxsplit=1, flags=re.I)
            if len(partes) == 2:
                papel, empresa = partes[0].strip(), partes[1].strip()
            elif " - " in prefixo:
                papel, empresa = (p.strip() for p in prefixo.split(" - ", 1))
            else:
                papel = prefixo
        papel = _limpar_rotulo(papel)
        empresa = _limpar_rotulo(empresa)
        chave = (empresa, achado.group(0))
        if chave in vistos:
            continue
        vistos.add(chave)
        if not papel:
            anterior = _linha_anterior(texto, inicio)
            if anterior and not any(
                kw in anterior.lower() for kw in FORMACAO_KW
            ):
                papel = _limpar_rotulo(anterior)
        if sufixo and not empresa:
            empresa = _limpar_rotulo(sufixo)
        bloco = _bloco_experiencia(texto, inicio)
        anteriores = [
            linha.strip() for linha in texto[:inicio].splitlines() if linha.strip()
        ][-2:]
        contexto = "\n".join([*anteriores, bloco])
        tem_projeto_cliente = bool(PROJETO_CLIENTE_RE.search(contexto))
        dominio = "energia" if any(
            kw.lower() in bloco.lower() for kw in ENERGIA
        ) else None
        tecnologias = [
            nome
            for nome, aliases in COMPETENCIAS.items()
            if any(
                re.search(
                    r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])",
                    contexto,
                    re.IGNORECASE,
                )
                for alias in aliases
            )
        ]
        achados.append(
            FatoExtraido(
                tipo="experiencia",
                dados={
                    "empresa": empresa,
                    "papel": papel,
                    "dominio": dominio,
                    "periodo": achado.group(0),
                    "tecnologias": tecnologias,
                    "resumo": bloco[:400],
                },
                trecho=_trecho(texto, pos),
                pagina=pagina_estimada(texto, pos, n_paginas),
                nivel=2 if tem_projeto_cliente else 1,
                confianca=50 if tem_projeto_cliente else 25,
            )
        )
    return achados


def _extrair_certificacoes(texto: str, n_paginas: int) -> list[FatoExtraido]:
    achados: list[FatoExtraido] = []
    vistos: set[str] = set()
    for nome, aliases in CERTIFICACOES.items():
        for alias in aliases:
            padrao = re.compile(
                r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])",
                re.IGNORECASE,
            )
            achado = padrao.search(texto)
            if achado and nome not in vistos:
                vistos.add(nome)
                pos = achado.start()
                achados.append(
                    FatoExtraido(
                        tipo="certificacao",
                        dados={"nome": nome, "emissor": None, "ano": None},
                        trecho=_trecho(texto, pos),
                        pagina=pagina_estimada(texto, pos, n_paginas),
                        nivel=0,
                        confianca=0,
                    )
                )
                break
    return achados


def _extrair_formacoes(texto: str, n_paginas: int) -> list[FatoExtraido]:
    achados: list[FatoExtraido] = []
    vistos: set[str] = set()
    for linha_inicio, linha in _linhas(texto):
        baixa = linha.lower()
        for kw in FORMACAO_KW:
            if kw in baixa:
                chave = f"{kw}:{linha[:60]}"
                if chave in vistos:
                    break
                vistos.add(chave)
                achados.append(
                    FatoExtraido(
                        tipo="formacao",
                        dados={"grau": kw, "curso": linha[:200], "instituicao": None},
                        trecho=linha[:400],
                        pagina=pagina_estimada(texto, linha_inicio, n_paginas),
                        nivel=1,
                        confianca=25,
                    )
                )
                break
    return achados


def _extrair_idiomas(texto: str, n_paginas: int) -> list[FatoExtraido]:
    achados: list[FatoExtraido] = []
    vistos: set[str] = set()
    for idioma, aliases in IDIOMAS_KW.items():
        for alias in aliases:
            padrao = re.compile(
                r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])",
                re.IGNORECASE,
            )
            achado = padrao.search(texto)
            if achado and idioma not in vistos:
                vistos.add(idioma)
                pos = achado.start()
                janela = texto[pos : pos + 80].lower()
                nivel = next((n for n in NIVEIS_IDIOMA if n in janela), None)
                achados.append(
                    FatoExtraido(
                        tipo="idioma",
                        dados={"idioma": idioma, "nivel": nivel},
                        trecho=_trecho(texto, pos),
                        pagina=pagina_estimada(texto, pos, n_paginas),
                        nivel=1,
                        confianca=25,
                    )
                )
                break
    return achados


def _extrair_residual(texto: str) -> list[str]:
    """Linhas com termos de curso/certificação não mapeados (não pontuam)."""
    residuais: list[str] = []
    for _inicio, linha in _linhas(texto):
        baixa = linha.lower()
        if ("curso" in baixa or "certifica" in baixa or "training" in baixa) and len(
            linha
        ) > 10:
            if linha[:400] not in residuais:
                residuais.append(linha[:400])
    return residuais[:20]


def _linhas(texto: str):
    posicao = 0
    for linha in texto.splitlines():
        yield posicao, linha.strip()
        posicao += len(linha) + 1


def extrair(texto: str, n_paginas: int) -> Extracao:
    """Roda todas as regras v1 sobre o texto do currículo."""
    competencias = _extrair_competencias(texto, n_paginas)
    experiencias = _extrair_experiencias(texto, n_paginas)
    # N2: competência usada em experiência com cliente/projeto nomeado
    nomes_n2 = {
        tecnologia
        for experiencia in experiencias
        if experiencia.nivel >= 2
        for tecnologia in experiencia.dados.get("tecnologias", [])
    }
    for competencia in competencias:
        if competencia.dados["nome"] in nomes_n2:
            competencia.nivel = 2
            competencia.confianca = 50
    return Extracao(
        competencias=competencias,
        experiencias=experiencias,
        certificacoes=_extrair_certificacoes(texto, n_paginas),
        formacoes=_extrair_formacoes(texto, n_paginas),
        idiomas=_extrair_idiomas(texto, n_paginas),
        residual=_extrair_residual(texto),
    )
