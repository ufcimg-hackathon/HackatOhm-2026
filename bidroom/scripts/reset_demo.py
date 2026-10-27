"""Fase 8 — checkpoint e reset reprodutível do banco da demo.

Uso:
  python scripts/reset_demo.py --db data/demo.db --criar
  python scripts/reset_demo.py --db data/demo.db --force

O checkpoint é uma cópia do banco no estado de abertura da demo. O reset
substitui o banco pelo checkpoint; sem `--force`, ele recusa sobrescrever.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

CHECKPOINT_PADRAO = Path("data/demo_checkpoint.db")


def criar_checkpoint(db: str | Path, checkpoint: str | Path) -> Path:
    origem = Path(db)
    destino = Path(checkpoint)
    if not origem.exists():
        raise FileNotFoundError(f"banco não encontrado: {origem}")
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origem, destino)
    return destino


def reset(db: str | Path, checkpoint: str | Path, force: bool = False) -> Path:
    origem = Path(checkpoint)
    destino = Path(db)
    if not origem.exists():
        raise FileNotFoundError(f"checkpoint não encontrado: {origem}")
    if destino.exists() and not force:
        raise FileExistsError(
            f"o banco {destino} já existe; use --force para restaurar o checkpoint"
        )
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(origem, destino)
    return destino


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/demo.db")
    parser.add_argument("--checkpoint", default=str(CHECKPOINT_PADRAO))
    parser.add_argument(
        "--criar",
        action="store_true",
        help="cria/atualiza o checkpoint a partir do banco atual",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="autoriza sobrescrever o banco no reset",
    )
    args = parser.parse_args(argv)
    try:
        if args.criar:
            caminho = criar_checkpoint(args.db, args.checkpoint)
            print(f"Checkpoint criado em {caminho}.")
        else:
            caminho = reset(args.db, args.checkpoint, force=args.force)
            print(f"Banco restaurado do checkpoint em {caminho}.")
    except Exception as exc:  # noqa: BLE001 - CLI reporta a causa
        print(f"Falha no reset: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
