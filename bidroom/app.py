"""Bid Room — aplicação Streamlit (T1–T4 da Fase 4).

Execute:
  streamlit run app.py
"""
from __future__ import annotations

import streamlit as st

from src.ingestion.db import conectar
from src.ui import t1_dashboard, t2_ranking, t3_perfil, t4_alocacao, t5_preset
from src.ui import dados
from src.ui.comum import caminho_db, formatar_data

st.set_page_config(
    page_title="Bid Room — Case B",
    page_icon="🗂️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .block-container {padding-top: 1.6rem; padding-bottom: 2rem;}
    [data-testid="stMetricValue"] {font-size: 1.6rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

PAGINAS = ["Dashboard", "Bid Room", "Perfil", "Alocação", "Preset", "Governança"]
db = caminho_db()
if not db.exists():
    st.error(
        f"Banco não encontrado em `{db}`. Rode `python scripts/build_db.py` e "
        "`python scripts/ingest.py` antes de abrir a demo."
    )
    st.stop()

con = conectar(db)
try:
    if "destino" in st.session_state:
        st.session_state["pagina"] = st.session_state.pop("destino")
    if st.session_state.get("pagina") not in PAGINAS:
        st.session_state["pagina"] = "Dashboard"

    with st.sidebar:
        st.markdown("## Bid Room")
        st.caption("Plataforma de gestão inteligente de currículos")
        pagina = st.radio("Navegação", PAGINAS, key="pagina")
        st.divider()
        disputa = dados.carregar_disputa(con)
        if disputa:
            st.markdown(f"**{disputa['nome_ficticio']}**")
            if disputa.get("preset_nome"):
                st.caption(
                    f"Preset: {disputa['preset_nome']} · "
                    f"v{disputa.get('preset_numero')} "
                    f"({disputa.get('preset_status')})"
                )
            st.caption(
                f"Última ingestão: {formatar_data(dados.carregar_dashboard(con)['ultima_ingestao'])}"
            )
        else:
            st.caption("Nenhuma disputa criada ainda.")
        st.divider()
        st.caption("Dados fictícios · PII no cofre · LGPD by design")
        st.caption("MVP A · Fase 6")

    if pagina == "Dashboard":
        t1_dashboard.render(con)
    elif pagina == "Bid Room":
        t2_ranking.render(con)
    elif pagina == "Perfil":
        t3_perfil.render(con)
    elif pagina == "Alocação":
        t4_alocacao.render(con)
    elif pagina == "Preset":
        t5_preset.render(con)
    elif pagina == "Governança":
        try:
            from src.ui import t6_governanca
            t6_governanca.render(con)
        except Exception:
            st.info("Página Governança — em construção.")
finally:
    con.close()
