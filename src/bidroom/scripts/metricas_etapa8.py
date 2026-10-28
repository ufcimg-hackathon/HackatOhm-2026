"""Métricas da jornada da Etapa 8 em banco sintético e temporário.

Mede, no ambiente local:
- tempo para anexar e validar um comprovante;
- tempo para recalcular o ranking dinâmico;
- tempo para obter uma composição viável;
- objetivo automático vs. uma escolha humana simples (top score por papel);
- atualidade e pendências de comprovação observadas.

Não usa `data/demo.db` e não representa um piloto com usuários reais.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document

from src.governance import comprovacao, editais
from src.governance.atualidade import resumo_candidato
from src.governance.comprovacao import listar_pendencias_comprovacao
from src.ingestion.db import conectar, criar_schema
from src.scoring.composicao import recomendar_composicao
from src.scoring.demanda import recalcular_disputa_demanda

LINHAS = [
    "EDITAL DE TESTE — MÉTRICAS",
    "Papel: PM/Scrum Sênior — 1 vaga",
    "Certificação obrigatória: PMP",
    "Período de trabalho: 12 meses",
    "Regra de combinação: a equipe deve ter pelo menos um PMP",
]


def _docx(pasta: Path) -> Path:
    caminho = pasta / "edital.docx"
    doc = Document()
    for linha in LINHAS:
        doc.add_paragraph(linha)
    doc.save(caminho)
    return caminho


def _candidato(con, pasta: Path, nome: str) -> int:
    candidato_id = con.execute(
        "INSERT INTO candidatos (nome_pseudonimo, status_curacao)"
        " VALUES (?, 'curado')",
        (nome,),
    ).lastrowid
    curriculo_id = con.execute(
        "INSERT INTO curriculos (candidato_id, nome_arquivo, caminho_cache,"
        " checksum, versao, status_processamento)"
        " VALUES (?, ?, ?, ?, 1, 'processado')",
        (candidato_id, f"{nome}.docx", f"cache/{nome}.docx", f"cur-{nome}"),
    ).lastrowid
    cert_id = con.execute(
        "INSERT INTO certificacoes (curriculo_id, nome, ano, data_conclusao)"
        " VALUES (?, 'PMP', 2026, '2026-01-15')",
        (curriculo_id,),
    ).lastrowid
    con.execute(
        "INSERT INTO disponibilidades (candidato_id, inicio, fim, percentual,"
        " fonte, responsavel, confirmado_em, valido_ate, estado)"
        " VALUES (?, '2026-01-01', '2027-01-01', 100, 'teste', 'rh',"
        " '2026-01-01', '2027-01-01', 'confirmado')",
        (candidato_id,),
    )
    con.commit()
    return int(cert_id)


def _processar(con, pasta: Path) -> int:
    edital_id = editais.criar_edital(con, "Edital Métricas")
    caminho = _docx(pasta)
    recebido = editais.receber_versao(
        con,
        edital_id,
        nome_arquivo=caminho.name,
        conteudo=caminho.read_bytes(),
        ator="analista",
    )
    versao_id = int(recebido["versao_id"])
    for req in editais.listar_requisitos(con, versao_id):
        editais.revisar_requisito(
            con,
            int(req["id"]),
            revisor="analista",
            decisao="aprovar",
            observacao="amostra controlada",
        )
    editais.aprovar_versao(con, versao_id, revisor="analista")
    ativado = editais.ativar_versao(con, versao_id, ator="analista")
    return int(ativado["disputas"][0])


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="bidroom-metricas-") as tmp:
        pasta = Path(tmp)
        os.environ["BIDROOM_EDITAIS"] = str(pasta / "editais")
        os.environ["BIDROOM_EVIDENCIAS"] = str(pasta / "evidencias")
        con = conectar(pasta / "metricas.db")
        try:
            criar_schema(con)
            cert_a = _candidato(con, pasta, "Ana")
            _candidato(con, pasta, "Bia")
            disputa_id = _processar(con, pasta)

            inicio = time.perf_counter()
            anexo = comprovacao.anexar_evidencia(
                con,
                "certificacao",
                cert_a,
                nome_arquivo="pmp-ana.docx",
                conteudo=b"comprovante ana",
                ator="candidato",
                valido_ate="2030-12-31",
            )
            tempo_anexo = time.perf_counter() - inicio
            inicio = time.perf_counter()
            comprovacao.validar_evidencia(
                con,
                int(anexo["evidencia_id"]),
                revisor="rh",
                ator="rh",
                valido_ate="2030-12-31",
            )
            tempo_validacao = time.perf_counter() - inicio

            inicio = time.perf_counter()
            ranking = recalcular_disputa_demanda(con, disputa_id, ator="sistema")
            tempo_ranking = time.perf_counter() - inicio
            inicio = time.perf_counter()
            composicao = recomendar_composicao(con, disputa_id)
            tempo_composicao = time.perf_counter() - inicio

            atualidade = resumo_candidato(con, 1)
            pendencias = listar_pendencias_comprovacao(con)
            humano = composicao.objetivo_guloso
            automatico = composicao.objetivo
            print("| Métrica | Valor medido |")
            print("|---|---:|")
            print(f"| Anexar comprovante | {tempo_anexo * 1000:.1f} ms |")
            print(f"| Validar comprovante | {tempo_validacao * 1000:.1f} ms |")
            print(f"| Recalcular ranking | {tempo_ranking * 1000:.1f} ms |")
            print(f"| Obter composição | {tempo_composicao * 1000:.1f} ms |")
            print(f"| Pontuações recalculadas | {ranking['pontuacoes']} |")
            print(f"| Objetivo automático | {automatico:.2f} |")
            print(f"| Objetivo da gulosa (baseline humano) | {humano:.2f} |")
            print(f"| Diferença automático - gulosa | {automatico - humano:+.2f} |")
            print(f"| Fatos de Ana por status | {atualidade['por_status']} |")
            print(f"| Pendências de comprovação | {len(pendencias)} |")
            print(
                "| Cobertura da composição | "
                f"{composicao.cobertura * 100:.0f}% |"
            )
            print()
            print(
                "Amostra controlada: 2 perfis, 1 edital, 1 vaga. "
                "Não é estimativa de precisão em editais reais."
            )
            return 0
        finally:
            con.close()


if __name__ == "__main__":
    raise SystemExit(main())
