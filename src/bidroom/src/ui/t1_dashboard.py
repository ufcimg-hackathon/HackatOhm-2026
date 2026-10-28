"""T1 — dashboard de impacto."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.ui import dados
from src.ui.comum import cabecalho, formatar_data, status_curacao


def render(con) -> None:
    dash = dados.carregar_dashboard(con)
    cabecalho(
        "Dashboard de impacto",
        "Do inventário à proposta: números lidos do banco em tempo real, sem PII.",
    )

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Currículos ingeridos", dash["total_curriculos"])
    col2.metric("Candidatos únicos", dash["total_candidatos"])
    col3.metric(
        "Curados",
        dash["curados_ou_alocados"],
        help="Inclui os já alocados; a lista humana de 24–30 entra na Fase 8.",
    )
    col4.metric("Alocados", dash["alocados"], help="Vagas preenchidas na T4.")

    st.markdown("#### Funil da demo")
    f1, f2, f3 = st.columns([1, 1, 1])
    f1.metric("1. Ingestão", dash["total_curriculos"], "currículos fictícios")
    f2.metric(
        "2. Curadoria",
        dash["curados_ou_alocados"],
        "meta 24–30 (CSV do time)",
    )
    f3.metric("3. Alocação", dash["alocados"], "meta 5 (T4)")

    if dash["total_curriculos"] and not dash["curados_ou_alocados"]:
        st.caption(
            "A curadoria ainda não foi carregada: o mecanismo existe, mas a lista "
            "dos 24–30 é humana e será aplicada na Fase 8, sem invenção do Build."
        )

    st.divider()
    esquerda, direita = st.columns([1, 1])
    with esquerda:
        st.subheader("Status de curadoria")
        tabela_status = pd.DataFrame(
            [
                {
                    "Status": status_curacao(chave),
                    "Candidatos": valor,
                }
                for chave, valor in dash["status"].items()
            ]
        )
        st.dataframe(tabela_status, hide_index=True, width="stretch")
        st.caption(
            f"Última ingestão: {formatar_data(dash['ultima_ingestao'])}"
            f" · Currículos com erro: {dash['curriculos_erro']}"
        )
    with direita:
        st.subheader("Faixa de pontuação")
        faixa = dash["faixa"]
        m1, m2, m3 = st.columns(3)
        m1.metric("Mínima", f"{faixa['min']:.2f}")
        m2.metric("Mediana", f"{faixa['mediana']:.2f}")
        m3.metric("Máxima", f"{faixa['max']:.2f}")
        st.caption(
            "Scores baixos são esperados nesta base: quase todos os fatos são N1; "
            "a calibração de evidência entra na Fase 7."
        )

    scores = dash["scores"]
    if not scores.empty:
        st.subheader("Distribuição dos scores atuais")
        faixas = [f"{i}–{i + 9}" for i in range(0, 100, 10)]
        cortes = pd.cut(
            scores["score"],
            bins=range(0, 101, 10),
            right=False,
            include_lowest=True,
        )
        contagem = cortes.value_counts().sort_index()
        grafico = pd.DataFrame(
            {"Faixa": faixas, "Candidato-papel": contagem.to_numpy()}
        )
        st.bar_chart(grafico, x="Faixa", y="Candidato-papel", height=260)

        st.subheader("Cobertura por papel")
        st.dataframe(
            pd.DataFrame(dash["por_papel"]).rename(
                columns={
                    "papel": "Papel",
                    "papel_nome": "Nome",
                    "total": "Avaliados",
                    "elegivel": "Elegíveis",
                    "revisar": "Revisar",
                    "eliminado": "Eliminados",
                    "score_max": "Score máx.",
                    "score_medio": "Score médio",
                }
            )[
                [
                    "Papel",
                    "Nome",
                    "Avaliados",
                    "Elegíveis",
                    "Revisar",
                    "Eliminados",
                    "Score máx.",
                    "Score médio",
                ]
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.info(
            "Ainda não há pontuações. Rode `python scripts/score.py --db data/demo.db` "
            "para popular a disputa demo."
        )
