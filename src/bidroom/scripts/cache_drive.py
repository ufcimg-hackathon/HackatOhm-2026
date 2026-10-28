"""Fase 0 — baixa os currículos do inventário para o cache local.

Idempotente por checksum no NÍVEL DE FASE 0: pula o download se o arquivo de
destino já existe e é um .docx válido (não-html). O checksum definitivo entra
na Fase 1 (`src/ingestion`).

Uso:
  python scripts/cache_drive.py --csv data/inventario_curriculos.csv
"""
from __future__ import annotations

import argparse
import csv
import time
import sys
from pathlib import Path

import requests

# bootstrap: rodar tanto via `python scripts/cache_drive.py` quanto como pacote
# em testes (`scripts.*` funciona como namespace).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.nome_arquivo import sanitizar


def baixar(link: str, destino: Path, tentativas: int = 2) -> str:
    """Baixa um arquivo; devolve o status para o CSV ('ok' ou descrição de erro)."""
    for i in range(1, tentativas + 1):
        try:
            resp = requests.get(link, stream=True, timeout=60)
            if resp.status_code != 200:
                return f"erro http {resp.status_code}"
            content_type = resp.headers.get("content-type", "").split(";")[0].lower()
            if "text/html" in content_type:
                # página de aviso de permissão/limite — não salvar como docx
                return "erro html (permissão/limite)"
            # defesa dupla: o caminho veio do CSV, mas sanitiza também no runtime
            destino = destino.parent / sanitizar(destino.name)
            destino.parent.mkdir(parents=True, exist_ok=True)
            with destino.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 16):
                    f.write(chunk)
            return "ok"
        except requests.RequestException:
            if i == tentativas:
                return "erro de rede"
            time.sleep(2 * i)
    return "erro de rede"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", default="data/inventario_curriculos.csv")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        linhas = list(csv.DictReader(f))
    if not linhas:
        raise SystemExit("CSV de controle vazio")

    ok = falha = pulados = 0
    for n, linha in enumerate(linhas, 1):
        destino = Path(linha["caminho_cache"])
        if destino.exists() and destino.suffix.lower() == ".docx":
            status = "ok (cache)"
            pulados += 1
        else:
            status = baixar(linha["link_individual"], destino)
        if status.startswith("ok"):
            ok += 1
        else:
            falha += 1
        linha["status_download"] = status
        if n % 25 == 0 or n == len(linhas):
            print(f"[{n}/{len(linhas)}] ok={ok} falha={falha} cache={pulados}")

    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        gravador = csv.DictWriter(f, fieldnames=list(linhas[0].keys()))
        gravador.writeheader()
        gravador.writerows(linhas)

    print(f"RESUMO: ok={ok} falha={falha} cache={pulados} total={len(linhas)}")
    return 0 if falha == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
