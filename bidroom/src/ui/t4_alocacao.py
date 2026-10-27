"""T4 — alocação do time e score do time (handoff §6.5 e §7)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.scoring.time import (
    SlotAlocado,
    calcular_score_time,
    carregar_alocacoes,
    listar_vagas,
    salvar_alocacao,
)
from src.ui import cache, dados
from src.ui.comum import cabecalho, caminho_db, status_gate


def _rotulo_opcao(linha: pd.Series | None) -> str:
    if linha is None:
        return "— vaga aberta —"
    return (
        f"{linha['nome']} · {linha['score']:.2f} · "
        f"{status_gate(linha['status_gates'])}"
    )


def render(con) -> None:
    disputa = dados.carregar_disputa(con)
    if not disputa or not disputa.get("preset_versao_id"):
        st.warning("Nenhuma disputa com preset publicado.")
        return
    ranking = cache.ranking(
        str(caminho_db()), int(disputa["id"]), int(disputa["preset_versao_id"])
    )
    vagas = listar_vagas(con)
    if not vagas:
        st.warning("O preset não define vagas por papel.")
        return
    if ranking.empty:
        st.info("Sem ranking calculado para alocar. Rode `python scripts/score.py`.")
        return

    cabecalho(
        "Alocação do time",
        f"{disputa['nome_ficticio']} · {len(vagas)} vagas · um candidato por vaga.",
    )
    flash = st.session_state.pop("flash_alocacao", None)
    if flash:
        st.success(flash)
    st.caption(
        "A sugestão inicial segue o topo do ranking, mas a decisão é sua: "
        "o score do time e os riscos são recalculados a cada troca."
    )

    alocacoes = carregar_alocacoes(con, int(disputa["id"]))
    por_papel: dict[str, list[dict]] = {}
    for alocacao in alocacoes:
        por_papel.setdefault(alocacao["papel"], []).append(alocacao)

    selecoes: list[dict] = []
    usados: set[int] = set()
    linhas_por_chave: dict[tuple[str, int], pd.Series] = {}
    for _, linha in ranking.iterrows():
        linhas_por_chave[(str(linha["papel"]), int(linha["candidato_id"]))] = linha

    st.subheader("Vagas")
    for vaga in vagas:
        papel = vaga["papel"]
        indice_vaga = int(vaga["vaga"]) - 1
        grupo = ranking[
            (ranking["papel"] == papel)
            & (ranking["status_gates"] != "eliminado")
        ].sort_values("score", ascending=False)
        if grupo.empty:
            grupo = ranking[ranking["papel"] == papel].sort_values(
                "score", ascending=False
            )
        ids = [int(valor) for valor in grupo["candidato_id"].tolist()]
        existentes = por_papel.get(papel, [])
        existente_id = (
            int(existentes[indice_vaga]["candidato_id"])
            if indice_vaga < len(existentes)
            else None
        )
        if existente_id is not None and existente_id not in ids:
            ids.append(existente_id)
        sugestao = existente_id
        if sugestao is None:
            sugestao = next((cid for cid in ids if cid not in usados), None)
        opcoes: list[int | None] = [None] + ids
        indice = opcoes.index(sugestao) if sugestao in opcoes else 0
        chave = f"alocacao_{disputa['id']}_{papel}_{vaga['vaga']}"
        escolha = st.selectbox(
            f"{vaga['papel_nome']} · vaga {vaga['vaga']}",
            opcoes,
            index=indice,
            format_func=lambda valor, papel=papel: _rotulo_opcao(
                linhas_por_chave.get((papel, int(valor)))
                if valor is not None
                else None
            ),
            key=chave,
        )
        if escolha is not None:
            usados.add(int(escolha))
            selecoes.append(
                {
                    "papel": papel,
                    "papel_id": int(vaga["papel_id"]),
                    "vaga": int(vaga["vaga"]),
                    "candidato_id": int(escolha),
                }
            )

    duplicados = [
        cid for cid in {s["candidato_id"] for s in selecoes}
        if sum(1 for s in selecoes if s["candidato_id"] == cid) > 1
    ]
    if duplicados:
        st.error(
            "Um candidato não pode ocupar duas vagas. Ajuste: "
            + ", ".join(str(cid) for cid in sorted(duplicados))
        )

    slots: list[SlotAlocado] = []
    for selecao in selecoes:
        linha = linhas_por_chave.get((selecao["papel"], selecao["candidato_id"]))
        if linha is None:
            continue
        slots.append(
            SlotAlocado(
                papel=selecao["papel"],
                papel_nome=str(linha["papel_nome"]),
                vaga=selecao["vaga"],
                candidato_id=selecao["candidato_id"],
                candidato_nome=str(linha["nome"]),
                score=float(linha["score"]),
                confianca=float(linha["confianca"]),
                dominio_energia=float(linha["dominio_energia"]),
                dias_ingestao=int(linha["dias_ingestao"]),
                status_gates=str(linha["status_gates"]),
            )
        )

    vagas_quantidade = {
        vaga["papel"]: int(vaga["quantidade"]) for vaga in vagas
    }
    score = calcular_score_time(
        slots, vagas_quantidade, dados.pool_elegivel(ranking)
    )

    st.divider()
    st.subheader("Score do time")
    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Score final", f"{score.score_final:.2f}")
    s2.metric("Média alocada", f"{score.media:.2f}")
    s3.metric("Bônus de cobertura", f"+{score.bonus_cobertura:.2f}")
    s4.metric("Confiança média", f"{score.confianca_media:.1f}")
    st.caption(
        f"Cobertura {score.preenchidas}/{score.total_vagas}. "
        "Fórmula do doc §10.5; pesos iguais por vaga nesta demo."
    )

    composicao = [
        {"Componente": "Média das pontuações", "Valor": score.media},
        {"Componente": "Bônus de cobertura", "Valor": score.bonus_cobertura},
    ]
    for nome, valor in score.penalidades.items():
        composicao.append(
            {"Componente": f"Penalidade — {nome.replace('_', ' ')}", "Valor": -valor}
        )
    composicao.append({"Componente": "Score final", "Valor": score.score_final})
    st.dataframe(
        pd.DataFrame(composicao),
        hide_index=True,
        width="stretch",
        column_config={
            "Valor": st.column_config.NumberColumn(format="%.2f")
        },
    )

    if score.riscos:
        st.subheader("Riscos sinalizados")
        for risco in score.riscos:
            st.warning(risco["mensagem"])
    else:
        st.success("Nenhum risco adicional sinalizado pelas regras atuais.")

    st.divider()
    pode_salvar = (
        len(selecoes) == len(vagas)
        and not duplicados
        and all(s["candidato_id"] for s in selecoes)
    )
    if not pode_salvar:
        st.info(
            f"Preencha as {len(vagas)} vagas para gravar a alocação da proposta."
        )
    if st.button(
        "Salvar alocação do time",
        type="primary",
        disabled=not pode_salvar,
    ):
        try:
            salvar_alocacao(
                con,
                int(disputa["id"]),
                [
                    {
                        "papel_id": selecao["papel_id"],
                        "candidato_id": selecao["candidato_id"],
                    }
                    for selecao in selecoes
                ],
                ator="ui",
            )
            cache.limpar()
            st.session_state["flash_alocacao"] = (
                "Alocação salva; o funil e o status dos candidatos foram "
                "atualizados."
            )
            st.rerun()
        except Exception as erro:
            st.error(f"Falha ao salvar alocação: {erro}")
