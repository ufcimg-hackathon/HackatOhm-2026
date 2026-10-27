"""Fase 4 — cache de leitura do Streamlit (ranking, perfil, candidatos)."""
from __future__ import annotations

import json
import pandas as pd
import streamlit as st

from src.ingestion.db import conectar
from src.scoring import preview as preview_service
from src.ui import dados


@st.cache_data(ttl=180, show_spinner=False)
def ranking(db_path: str, disputa_id: int, versao_preset_id: int) -> pd.DataFrame:
    con = conectar(db_path)
    try:
        return dados.carregar_ranking(con, disputa_id, versao_preset_id)
    finally:
        con.close()


@st.cache_data(ttl=180, show_spinner=False)
def candidatos(db_path: str) -> pd.DataFrame:
    con = conectar(db_path)
    try:
        return dados.listar_candidatos(con)
    finally:
        con.close()


@st.cache_data(ttl=180, show_spinner=False)
def perfil(db_path: str, candidato_id: int) -> dict | None:
    con = conectar(db_path)
    try:
        return dados.carregar_perfil(con, candidato_id)
    finally:
        con.close()


@st.cache_data(ttl=180, show_spinner=False)
def preview_papel(
    db_path: str,
    disputa_id: int,
    versao_base_id: int,
    papel: str,
    pesos_json: str,
    gates_json: str,
    limite: int = 10,
) -> dict:
    con = conectar(db_path)
    try:
        return preview_service.preview_papel(
            con,
            disputa_id,
            versao_base_id,
            papel,
            json.loads(pesos_json),
            json.loads(gates_json),
            limite,
        )
    finally:
        con.close()


def limpar() -> None:
    st.cache_data.clear()
