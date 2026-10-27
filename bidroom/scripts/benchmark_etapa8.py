"""Benchmark da Etapa 8 — carregamento de contexto em volumes crescentes.

Compara o caminho antigo (uma varredura global por candidato) com o caminho
em lote (varredura global única). Não usa `data/demo.db`; o banco é sintético
e temporário.

Uso:
  .venv\\Scripts\\python scripts/benchmark_etapa8.py
  .venv\\Scripts\\python scripts/benchmark_etapa8.py --repeticoes 5
"""
from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ingestion.db import conectar, criar_schema
from src.scoring.contexto import carregar_contexto, carregar_contextos


def _povoar(con, n_candidatos: int, fatos_por_candidato: int) -> list[int]:
    curriculos: list[int] = []
    for i in range(n_candidatos):
        candidato_id = con.execute(
            "INSERT INTO candidatos (nome_pseudonimo, status_curacao)"
            " VALUES (?, 'curado')",
            (f"Candidato {i:04d}",),
        ).lastrowid
        curriculo_id = con.execute(
            "INSERT INTO curriculos (candidato_id, nome_arquivo, caminho_cache,"
            " checksum, versao, status_processamento)"
            " VALUES (?, ?, ?, ?, 1, 'processado')",
            (
                candidato_id,
                f"cv-{i}.docx",
                f"cache/cv-{i}.docx",
                f"checksum-{i}",
            ),
        ).lastrowid
        curriculos.append(int(curriculo_id))
        for j in range(fatos_por_candidato):
            cert_id = con.execute(
                "INSERT INTO certificacoes (curriculo_id, nome, ano)"
                " VALUES (?, ?, 2026)",
                (curriculo_id, f"Certificação {j}"),
            ).lastrowid
            con.execute(
                "INSERT INTO evidencias (tipo_fato, fato_id, trecho, pagina,"
                " nivel, confianca) VALUES ('certificacao', ?, ?, 1, 4, 100)",
                (cert_id, f"evidência {j}"),
            )
            con.execute(
                "INSERT INTO atualidade_fatos (tipo_fato, fato_id, fonte,"
                " ultima_confirmacao, valido_ate, estado)"
                " VALUES ('certificacao', ?, 'teste', '2026-01-01',"
                " '2030-12-31', 'validado')",
                (cert_id,),
            )
            con.execute(
                "INSERT INTO evidencias_documentais (tipo_fato, fato_id,"
                " nome_arquivo, caminho, checksum, status, valido_ate)"
                " VALUES ('certificacao', ?, 'prova.docx', 'prova.docx', ?,"
                " 'validado', '2030-12-31')",
                (cert_id, f"ev-{cert_id}"),
            )
        con.execute(
            "INSERT INTO disponibilidades (candidato_id, inicio, fim,"
            " percentual, fonte, responsavel, confirmado_em, valido_ate, estado)"
            " VALUES (?, '2026-01-01', '2027-01-01', 100, 'teste', 'teste',"
            " '2026-01-01', '2027-01-01', 'confirmado')",
            (candidato_id,),
        )
    con.commit()
    return curriculos


def _medir(funcao):
    tracemalloc.start()
    inicio = time.perf_counter()
    resultado = funcao()
    duracao = time.perf_counter() - inicio
    _, pico = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return resultado, duracao, pico


def _executar(n_candidatos: int, fatos: int, repeticoes: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="bidroom-bench-") as pasta:
        db = Path(pasta) / "bench.db"
        con = conectar(db)
        try:
            criar_schema(con)
            curriculos = _povoar(con, n_candidatos, fatos)

            antigo_tempos = []
            antigo_memoria = []
            for _ in range(repeticoes):
                _, duracao, pico = _medir(
                    lambda: [carregar_contexto(con, cid) for cid in curriculos]
                )
                antigo_tempos.append(duracao)
                antigo_memoria.append(pico)

            novo_tempos = []
            novo_memoria = []
            for _ in range(repeticoes):
                _, duracao, pico = _medir(
                    lambda: carregar_contextos(con, curriculos)
                )
                novo_tempos.append(duracao)
                novo_memoria.append(pico)
            antigo = statistics.median(antigo_tempos)
            novo = statistics.median(novo_tempos)
            return {
                "candidatos": n_candidatos,
                "fatos_por_candidato": fatos,
                "registros_globais": n_candidatos * fatos * 3,
                "antigo_ms": antigo * 1000,
                "novo_ms": novo * 1000,
                "speedup": antigo / novo if novo else 0.0,
                "memoria_antiga_kb": max(antigo_memoria) / 1024,
                "memoria_nova_kb": max(novo_memoria) / 1024,
            }
        finally:
            con.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeticoes", type=int, default=3)
    parser.add_argument(
        "--volumes",
        default="50,100,200",
        help="lista de N candidatos separada por vírgula",
    )
    args = parser.parse_args()
    volumes = [int(item) for item in args.volumes.split(",") if item.strip()]
    resultados = [
        _executar(n, fatos=3, repeticoes=max(1, args.repeticoes))
        for n in volumes
    ]
    print(
        "| Candidatos | Fatos/candidato | Registros globais | Antigo (ms) | "
        "Lote (ms) | Speedup | Pico antigo (KB) | Pico lote (KB) |"
    )
    print("|---:|---:|---:|---:|---:|---:|---:|---:|")
    for item in resultados:
        print(
            f"| {item['candidatos']} | {item['fatos_por_candidato']} | "
            f"{item['registros_globais']} | {item['antigo_ms']:.1f} | "
            f"{item['novo_ms']:.1f} | {item['speedup']:.2f}x | "
            f"{item['memoria_antiga_kb']:.1f} | "
            f"{item['memoria_nova_kb']:.1f} |"
        )
    print()
    print(
        "Caminho antigo: O(N × (E + A + D)) — três varreduras globais por "
        "candidato. Caminho em lote: O(E + A + D + N × fatos_do_curriculo)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
