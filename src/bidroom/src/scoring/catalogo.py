"""Catálogo de critérios do preset "TI para o setor de energia" (v1).

Aqui ficam apenas os FATOS DE DOMÍNIO que o motor precisa para transformar
fatos em nota: competências core, ferramentas, certificações esperadas e
anos mínimos por papel. Pesos de categoria e gates vêm SEMPRE da versão do
preset no banco (`pesos_json` / `gates_json`) — nunca deste arquivo.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CertificadoEsperado:
    nome: str
    peso: int  # dentro da categoria de certificações (0–100)


@dataclass(frozen=True)
class PerfilPapel:
    sigla: str
    nome: str
    core: tuple[str, ...]
    ferramentas: tuple[str, ...]
    certificacoes: tuple[CertificadoEsperado, ...]
    anos_minimos: int = 5
    senior: bool = True


CATALOGO: dict[str, PerfilPapel] = {
    "pm_scrum": PerfilPapel(
        sigla="pm_scrum",
        nome="PM/Scrum Sênior",
        core=("Gestão de projetos", "Scrum", "Kanban", "Excel", "Power BI"),
        ferramentas=("Excel", "Power BI", "Tableau", "Git", "SQL"),
        certificacoes=(
            CertificadoEsperado("PMP", 100),
            CertificadoEsperado("CSM", 40),
            CertificadoEsperado("PSM", 30),
            CertificadoEsperado("PMI-ACP", 30),
        ),
        anos_minimos=5,
    ),
    "ba_bsa": PerfilPapel(
        sigla="ba_bsa",
        nome="BA/BSA Sênior",
        core=("Scrum", "APIs REST", "SQL", "Excel", "Power BI"),
        ferramentas=("Excel", "Power BI", "Tableau", "SQL", "Git"),
        certificacoes=(
            CertificadoEsperado("CBAP", 100),
            CertificadoEsperado("CCBA", 70),
            CertificadoEsperado("CSM", 30),
            CertificadoEsperado("PSM", 20),
        ),
        anos_minimos=5,
    ),
    "java": PerfilPapel(
        sigla="java",
        nome="Java Developer Sênior",
        core=("Java", "Spring", "APIs REST", "SQL", "Git"),
        ferramentas=("AWS", "Azure", "Docker", "Kubernetes", "Kafka"),
        certificacoes=(
            CertificadoEsperado("OCP", 100),
            CertificadoEsperado("AWS Certified", 70),
            CertificadoEsperado("CSM", 20),
        ),
        anos_minimos=5,
    ),
    "bi_data": PerfilPapel(
        sigla="bi_data",
        nome="BI/Data Analyst Sênior",
        core=("SQL", "Power BI", "Tableau", "Excel", "ETL"),
        ferramentas=("Python", "Spark", "Kafka", "AWS", "Azure"),
        certificacoes=(
            CertificadoEsperado("AWS Certified", 100),
            CertificadoEsperado("OCP", 40),
            CertificadoEsperado("CSM", 20),
        ),
        anos_minimos=5,
    ),
}
