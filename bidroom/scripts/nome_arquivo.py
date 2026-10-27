"""Nomes de arquivo seguros para o filesystem local (Windows).

Regra de Fase 0: `nome_arquivo` (CSV) mantém a identidade original do Drive;
`caminho_cache` é sanitizado e determinístico. Checksum definitivo entra na
Fase 1 (`src/ingestion`).
"""
from __future__ import annotations

import re

# Caracteres proibidos em nomes no Windows
PROIBIDOS_WIN = '<>:"/\\|?*'


def sanitizar(nome: str) -> str:
    """Converte um nome vindo do Drive em nome de arquivo seguro.

    - desfaz o escape `\\_` -> `_` do dump;
    - substitui proibidos do Windows por "-" e colapsa repetições;
    - descarta espaços/pontos na borda final (Windows rejeita;
      é a causa do crash com `Adhi Gopalam \\- SM.docx`);
    - preserva a extensão e nomes internos (ex.: `Vivek.BSA.docx` fica igual).
    """
    s = nome.replace("\\_", "_")
    for c in PROIBIDOS_WIN:
        s = s.replace(c, "-")
    s = re.sub(r"-{2,}", "-", s)
    s = re.sub(r"[.\s]+$", "", s)
    if not s:
        s = "sem_nome.docx"
    return s
