"""T2 — Bid Room: ranking por papel com explicabilidade (handoff §7)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.ui import cache, dados
from src.ui.comum import cabecalho, caminho_db, navegar, status_curacao, status_gate


def render(con) -> None:
    disputa = dados.carregar_disputa(con)
    if not disputa or not disputa.get("preset_versao_id"):
        st.warning("Nenhuma disputa com preset publicado. Rode o script de score.")
        return

    ranking = cache.ranking(
        str(caminho_db()), int(disputa["id"]), int(disputa["preset_versao_id"])
    )
    cabecalho(
        "Bid Room — ranking",
        f"{disputa['nome_ficticio']} · preset v{disputa.get('preset_numero')} "
        f"({disputa.get('preset_status')})",
    )
    if ranking.empty:
        st.info("Sem pontuações para a disputa. Rode `python scripts/score.py`.")
        return

    ordem_papeis = {"pm_scrum": 0, "ba_bsa": 1, "java": 2, "bi_data": 3}
    papeis = (
        ranking[["papel", "papel_nome"]]
        .drop_duplicates()
        .assign(ordem=lambda df: df["papel"].map(ordem_papeis).fillna(99))
        .sort_values("ordem")
        .to_dict("records")
    )
    nomes = {p["papel"]: f"{p['papel_nome']} ({p['papel']})" for p in papeis}

    c1, c2, c3, c4 = st.columns([1.4, 1.1, 0.9, 1.3])
    with c1:
        papel = st.selectbox(
            "Papel",
            [p["papel"] for p in papeis],
            format_func=lambda valor: nomes.get(valor, valor),
        )
    with c2:
        filtro_status = st.selectbox(
            "Triagem",
            ["Não eliminados", "Elegível", "Revisar", "Eliminado", "Todos"],
            index=0,
        )
    with c3:
        evidencia_min = st.selectbox(
            "Evidência mínima", [0, 1, 2, 3, 4], index=0
        )
    with c4:
        busca = st.text_input("Buscar pseudônimo", "")

    max_score = float(ranking["score"].max()) if not ranking.empty else 100.0
    score_min = st.slider(
        "Score final mínimo",
        min_value=0.0,
        max_value=max(100.0, round(max_score + 0.01, 2)),
        value=0.0,
        step=1.0,
    )

    filtrado = ranking[ranking["papel"] == papel].copy()
    if filtro_status == "Não eliminados":
        filtrado = filtrado[filtrado["status_gates"] != "eliminado"]
    elif filtro_status != "Todos":
        status_alvo = {
            "Elegível": "elegivel",
            "Revisar": "revisar",
            "Eliminado": "eliminado",
        }[filtro_status]
        filtrado = filtrado[filtrado["status_gates"] == status_alvo]
    filtrado = filtrado[filtrado["evidencia_max"] >= int(evidencia_min)]
    filtrado = filtrado[filtrado["score"] >= float(score_min)]
    if busca.strip():
        filtrado = filtrado[
            filtrado["nome"].str.contains(busca.strip(), case=False, na=False)
        ]
    filtrado = filtrado.sort_values("score", ascending=False).reset_index(drop=True)

    st.caption(
        f"{len(filtrado)} candidato(s) neste corte · ordenado por score final · "
        "clique em uma linha para abrir a ficha (T3)."
    )

    if filtrado.empty:
        st.info("Nenhum candidato atende aos filtros atuais.")
        return

    view = filtrado.rename(
        columns={
            "nome": "Candidato",
            "status_curacao": "Curadoria",
            "score": "Score final",
            "ponderada": "Ponderada",
            "confianca": "Confiança",
            "evidencia_max": "Evid. máx.",
            "status_gates": "Triagem",
            "gates_passou": "Gates ok",
            "gates_revisar": "Gates a revisar",
            "gates_ok": "Gates aprovados",
            "ancoras": "Âncoras",
            "anos": "Anos",
            "aderencia": "Aderência",
            "dominio_energia": "Energia",
            "certificacoes": "Certificações",
            "experiencia": "Experiência",
            "idioma": "Idioma",
            "educacao": "Educação",
            "ferramentas": "Ferramentas",
        }
    )
    view["Triagem"] = view["Triagem"].map(status_gate)
    view["Curadoria"] = view["Curadoria"].map(status_curacao)

    nonce = st.session_state.get("ranking_nonce", 0)
    chave = (
        f"ranking_{papel}_{filtro_status}_{evidencia_min}_{score_min}_{busca}_{nonce}"
    )
    evento = st.dataframe(
        view,
        hide_index=True,
        width="stretch",
        column_order=[
            "Candidato",
            "Score final",
            "Ponderada",
            "Confiança",
            "Evid. máx.",
            "Triagem",
            "Gates aprovados",
            "Gates a revisar",
            "Âncoras",
            "Anos",
            "Aderência",
            "Energia",
            "Certificações",
            "Experiência",
            "Idioma",
            "Educação",
            "Ferramentas",
            "Curadoria",
        ],
        on_select="rerun",
        selection_mode="single-row",
        key=chave,
    )
    if evento.selection.rows:
        posicao = evento.selection.rows[0]
        if 0 <= posicao < len(filtrado):
            candidato_id = int(filtrado.iloc[posicao]["candidato_id"])
            st.session_state["ranking_nonce"] = nonce + 1
            navegar("Perfil", candidato_id)

    with st.expander("Como ler este ranking"):
        st.markdown(
            """
- **Score final** = pontuação ponderada × fator de confiança
  (`0,6 + 0,4 × confiança/100`).
- **Triagem** mostra o pior status dos gates G1–G6; `Revisar` significa
  informação faltante, não eliminação.
- **Âncoras** são o subconjunto do case (PMP, energia, pós, 10+ anos) e não
  somam por cima das categorias.
- **Energia** é o score da categoria de domínio; zero não elimina, mas entra
  como lacuna no risco do time.
"""
        )
