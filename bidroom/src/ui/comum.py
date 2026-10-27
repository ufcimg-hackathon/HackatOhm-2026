"""Fase 4 — utilidades compartilhadas pelas telas Streamlit."""
from __future__ import annotations

import os
from pathlib import Path

import streamlit as st

from src.ui.dados import ROTULO_GATE, ROTULO_STATUS_CURACAO


def caminho_db() -> Path:
    return Path(os.environ.get("BIDROOM_DB", "data/demo.db")).resolve()


def caminho_inbox() -> Path:
    return Path(os.environ.get("BIDROOM_INBOX", "data/inbox")).resolve()


def navegar(pagina: str, candidato_id: int | None = None) -> None:
    """Troca de página no próximo rerun, sem violar o estado do widget."""
    st.session_state["destino"] = pagina
    if candidato_id is not None:
        st.session_state["candidato_id"] = int(candidato_id)
    st.rerun()


def cabecalho(titulo: str, subtitulo: str | None = None) -> None:
    st.title(titulo)
    if subtitulo:
        st.caption(subtitulo)


def formatar_data(texto: str | None) -> str:
    if not texto:
        return "—"
    try:
        data = texto.replace("T", " ")[:16]
        return data
    except Exception:
        return texto


def status_curacao(status: str) -> str:
    return ROTULO_STATUS_CURACAO.get(status, status)


def status_gate(status: str) -> str:
    return ROTULO_GATE.get(status, status)


def status_gate_resultado(status: str) -> str:
    """Traduz o status individual do gate (passou / revisar / elimina)."""
    return {
        "passou": "Passou",
        "revisar": "Revisar",
        "elimina": "Elimina",
    }.get(status, status)


def badge_gate(status: str) -> str:
    cores = {
        "elegivel": "#1b7f3a",
        "revisar": "#9a6700",
        "eliminado": "#b42318",
    }
    return (
        f"<span style='background:{cores.get(status, '#555')};color:white;"
        f"padding:2px 8px;border-radius:10px;font-size:0.8rem'>"
        f"{status_gate(status)}</span>"
    )
