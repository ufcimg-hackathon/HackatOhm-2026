"""T4 — alocação do time e score do time."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from src.governance.ciclo import classificar_disputa, recalcular_pendentes
from src.scoring.composicao import avaliar_composicao, recomendar_composicao
from src.scoring.time import (
    SlotAlocado,
    calcular_score_time,
    carregar_alocacoes,
    listar_vagas,
    salvar_alocacao,
)
from src.ui import cache, dados
from src.ui.comum import cabecalho, caminho_db, status_gate

ROTULO_STATUS_DEMANDA = {
    "elegivel": "Elegível",
    "pendente": "Pendente",
    "eliminado": "Eliminado",
}


def _rotulo_opcao(linha: pd.Series | None) -> str:
    if linha is None:
        return "— vaga aberta —"
    return (
        f"{linha['nome']} · {linha['score']:.2f} · "
        f"{status_gate(linha['status_gates'])}"
    )


def _rotulo_demanda(linha: pd.Series | None) -> str:
    if linha is None:
        return "— vaga aberta —"
    return (
        f"{linha['nome']} · nota {linha['score']:.2f} · "
        f"apoio {linha['score_interno_apoio']:.1f} · "
        f"{ROTULO_STATUS_DEMANDA.get(str(linha['status_gates']), linha['status_gates'])}"
    )


def _aviso_pendencias(con, disputa: dict) -> None:
    classificacao = classificar_disputa(con, int(disputa["id"]))
    if classificacao["status"] != "pendente":
        return
    st.warning(
        f"Esta recomendação tem {classificacao['pendencias']} pendência(s) "
        "de atualização. A decisão anterior foi preservada; recalcule para "
        "usar os dados vigentes."
    )
    if st.button("Recalcular pendências", key=f"recalcular_{disputa['id']}"):
        with st.spinner("Recalculando disputa..."):
            resultado = recalcular_pendentes(
                con, disputa_id=int(disputa["id"]), ator="ui"
            )
        if resultado["falhas"]:
            st.error(
                "Recálculo falhou e continua pendente: "
                + "; ".join(item["erro"] for item in resultado["falhas"])
            )
        else:
            st.success("Disputa recalculada; pendências resolvidas.")
            st.rerun()


def _tabela_coletivos(composicao) -> None:
    if not composicao.requisitos_coletivos:
        return
    st.subheader("Requisitos coletivos")
    linhas = []
    for item in composicao.requisitos_coletivos:
        status = {
            "atendido": "Atendido",
            "nao_atendido": "Não atendido",
            "pendente": "Revisão manual",
        }.get(item["status"], item["status"])
        linhas.append(
            {
                "Requisito": item["texto"],
                "Escopo": item["escopo"],
                "Alvo": item["alvo"] or "—",
                "Mínimo": item["minimo"] or "—",
                "Atendidos": item["atendidos"],
                "Status": status,
                "Motivo": item["motivo"],
            }
        )
    st.dataframe(
        pd.DataFrame(linhas),
        hide_index=True,
        width="stretch",
    )


def _tabela_equipe(composicao) -> None:
    linhas = []
    for slot in composicao.slots:
        candidato = slot.get("candidato")
        if not candidato:
            linhas.append(
                {
                    "Papel": slot["papel_nome"],
                    "Vaga": slot["vaga"],
                    "Integrante": "— vaga descoberta —",
                    "Nota": None,
                    "Apoio": None,
                    "Confiança": None,
                    "Atualidade": None,
                    "Status": "Sem candidato elegível",
                    "Evidências": "",
                }
            )
            continue
        ancoras = candidato.get("ancoras") or []
        textos = [
            item.get("descricao") or item.get("motivo") or ""
            if isinstance(item, dict)
            else str(item)
            for item in ancoras
        ]
        linhas.append(
            {
                "Papel": slot["papel_nome"],
                "Vaga": slot["vaga"],
                "Integrante": candidato["nome"],
                "Nota": candidato["nota_tecnica"],
                "Apoio": candidato["score_interno_apoio"],
                "Confiança": candidato["confianca"],
                "Atualidade": candidato["atualidade"],
                "Status": ROTULO_STATUS_DEMANDA.get(
                    candidato["status"], candidato["status"]
                ),
                "Evidências": "; ".join(texto for texto in textos if texto) or "—",
            }
        )
    tabela = pd.DataFrame(linhas)
    st.dataframe(
        tabela,
        hide_index=True,
        width="stretch",
        column_config={
            "Nota": st.column_config.NumberColumn(format="%.2f"),
            "Apoio": st.column_config.NumberColumn(format="%.1f"),
            "Confiança": st.column_config.NumberColumn(format="%.1f"),
            "Atualidade": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    st.download_button(
        "Baixar composição (CSV)",
        tabela.to_csv(index=False).encode("utf-8-sig"),
        file_name="composicao_time.csv",
        mime="text/csv",
        key="download_composicao",
    )


def _render_demanda(con, disputa: dict) -> None:
    versao_id = int(disputa["edital_versao_id"])
    cabecalho(
        "Alocação do time",
        f"{disputa['nome_ficticio']} · edital v{disputa.get('edital_numero', '?')}"
        " · composição por requisitos",
    )
    flash = st.session_state.pop("flash_alocacao", None)
    if flash:
        st.success(flash)

    _aviso_pendencias(con, disputa)
    st.caption(
        "Objetivo da recomendação: soma da nota técnica + bônus por requisito "
        "coletivo atendido - penalidade por requisito coletivo não atendido. "
        "Candidatos eliminados ou pendentes não preenchem vagas automaticamente."
    )

    ranking = cache.ranking_demanda(
        str(caminho_db()), int(disputa["id"]), versao_id
    )
    if ranking.empty:
        st.info(
            "Sem pontuação dinâmica para esta demanda. Recalcule o ranking em "
            "**Ranking** antes de montar a equipe."
        )
        return

    try:
        recomendacao = recomendar_composicao(con, int(disputa["id"]))
    except Exception as erro:
        st.error(f"Não foi possível montar a composição: {erro}")
        return

    st.subheader("Recomendação automática")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Objetivo", f"{recomendacao.objetivo:.2f}")
    m2.metric("Nota técnica", f"{recomendacao.nota_total:.2f}")
    m3.metric(
        "Coletivos",
        f"+{recomendacao.bonus_coletivo:.0f} / -{recomendacao.penalidade_coletiva:.0f}",
    )
    m4.metric("Cobertura", f"{recomendacao.cobertura * 100:.0f}%")
    st.caption(
        f"Status: {recomendacao.status} · referência {recomendacao.referencia} · "
        f"gulosa {recomendacao.objetivo_guloso:.2f} · "
        f"ganho da otimização {recomendacao.diferenca_guloso:+.2f}."
    )
    if not recomendacao.busca_completa:
        st.warning(
            "A busca foi limitada pelo tamanho do pool; a solução é a melhor "
            "encontrada, não uma prova de ótimo global."
        )
    if recomendacao.pendencias:
        st.warning(
            "Pendências da recomendação:\n\n"
            + "\n".join(f"- {item}" for item in recomendacao.pendencias)
        )
    _tabela_coletivos(recomendacao)

    alocacoes = carregar_alocacoes(con, int(disputa["id"]))
    por_papel: dict[str, list[dict]] = {}
    for alocacao in alocacoes:
        por_papel.setdefault(alocacao["papel"], []).append(alocacao)

    linhas_por_chave = {
        (int(linha["papel_id"]), int(linha["candidato_id"])): linha
        for _, linha in ranking.iterrows()
    }
    permitidos = {
        (candidato.papel_id, candidato.candidato_id)
        for candidato in recomendacao.candidatos
        if candidato.status == "elegivel"
        and candidato.conflito_disputa_id is None
    }
    recomendados = {
        (slot["papel_id"], slot["vaga"]): slot["candidato"]
        for slot in recomendacao.slots
    }

    st.subheader("Revisão do gestor")
    st.caption(
        "A recomendação é o ponto de partida. Troque integrantes para testar "
        "alternativas; a cobertura e os requisitos coletivos são reavaliados."
    )
    selecoes: list[dict] = []
    usados: set[int] = set()
    for vaga in recomendacao.vagas:
        papel_id = int(vaga.papel_id)
        grupo = ranking[
            (ranking["papel_id"] == papel_id)
            & (ranking["status_gates"] == "elegivel")
        ]
        grupo = grupo[
            grupo.apply(
                lambda linha: (
                    int(linha["papel_id"]),
                    int(linha["candidato_id"]),
                )
                in permitidos,
                axis=1,
            )
        ]
        grupo = grupo[~grupo["candidato_id"].isin(usados)]
        ids = [int(valor) for valor in grupo["candidato_id"].tolist()]
        existentes = por_papel.get(vaga.papel, [])
        existente_id = (
            int(existentes[vaga.vaga - 1]["candidato_id"])
            if vaga.vaga - 1 < len(existentes)
            else None
        )
        if existente_id is not None and existente_id not in ids:
            linha_existente = linhas_por_chave.get((papel_id, existente_id))
            nome = (
                linha_existente["nome"]
                if linha_existente is not None
                else str(existente_id)
            )
            st.warning(
                f"A alocação anterior de {nome} em {vaga.papel_nome} não está "
                "mais elegível (ou está em conflito) e não será mantida "
                "automaticamente."
            )
            existente_id = None
        sugestao = existente_id
        if sugestao is None:
            candidato_recomendado = recomendados.get((papel_id, vaga.vaga))
            if candidato_recomendado:
                candidato_id = int(candidato_recomendado["candidato_id"])
                if candidato_id in ids:
                    sugestao = candidato_id
        if sugestao is None:
            sugestao = next((cid for cid in ids if cid not in usados), None)
        opcoes: list[int | None] = [None] + ids
        indice = opcoes.index(sugestao) if sugestao in opcoes else 0
        escolha = st.selectbox(
            f"{vaga.papel_nome} · vaga {vaga.vaga}",
            opcoes,
            index=indice,
            format_func=lambda valor, papel_id=papel_id: _rotulo_demanda(
                linhas_por_chave.get((papel_id, int(valor)))
                if valor is not None
                else None
            ),
            key=f"demanda_{disputa['id']}_{papel_id}_{vaga.vaga}",
        )
        if escolha is None:
            st.caption(
                f"Vaga descoberta: nenhum candidato elegível disponível para "
                f"{vaga.papel_nome}."
            )
            continue
        usados.add(int(escolha))
        selecoes.append(
            {
                "papel": vaga.papel,
                "papel_id": papel_id,
                "vaga": int(vaga.vaga),
                "candidato_id": int(escolha),
            }
        )

    avaliada = avaliar_composicao(con, int(disputa["id"]), selecoes)
    st.subheader("Avaliação da seleção")
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Objetivo", f"{avaliada.objetivo:.2f}")
    a2.metric("Nota técnica", f"{avaliada.nota_total:.2f}")
    a3.metric(
        "Coletivos",
        f"+{avaliada.bonus_coletivo:.0f} / -{avaliada.penalidade_coletiva:.0f}",
    )
    a4.metric("Cobertura", f"{avaliada.cobertura * 100:.0f}%")
    if avaliada.pendencias:
        st.warning(
            "Pendências da seleção:\n\n"
            + "\n".join(f"- {item}" for item in avaliada.pendencias)
        )
    if avaliada.riscos:
        st.subheader("Riscos sinalizados")
        for risco in avaliada.riscos:
            st.warning(risco["mensagem"])

    _tabela_coletivos(avaliada)
    _tabela_equipe(avaliada)

    if recomendacao.alternativas:
        with st.expander("Alternativas por vaga"):
            for chave, itens in recomendacao.alternativas.items():
                if not itens:
                    continue
                st.markdown(f"**{chave.replace(':', ' · vaga ')}**")
                st.dataframe(
                    pd.DataFrame(
                        [
                            {
                                "Candidato": item["nome"],
                                "Nota": item["nota_tecnica"],
                                "Apoio": item["score_interno_apoio"],
                                "Confiança": item["confianca"],
                                "Status": ROTULO_STATUS_DEMANDA.get(
                                    item["status"], item["status"]
                                ),
                            }
                            for item in itens
                        ]
                    ),
                    hide_index=True,
                    width="stretch",
                    column_config={
                        "Nota": st.column_config.NumberColumn(format="%.2f"),
                        "Apoio": st.column_config.NumberColumn(format="%.1f"),
                        "Confiança": st.column_config.NumberColumn(format="%.1f"),
                    },
                )

    st.divider()
    coletivo_inviavel = any(
        item["status"] == "nao_atendido"
        for item in avaliada.requisitos_coletivos
    )
    limite_excedido = any(
        item.startswith("Limite de ") for item in avaliada.pendencias
    )
    pode_salvar = (
        len(selecoes) == len(recomendacao.vagas)
        and not coletivo_inviavel
        and not limite_excedido
        and len({s["candidato_id"] for s in selecoes}) == len(selecoes)
    )
    if coletivo_inviavel:
        st.error(
            "A seleção não atende a todos os requisitos coletivos conhecidos. "
            "Ajuste a equipe ou trate a pendência antes de salvar."
        )
    elif limite_excedido:
        st.error(
            "A seleção excede o limite de integrantes por papel definido no "
            "edital. Ajuste a equipe antes de salvar."
        )
    elif len(selecoes) != len(recomendacao.vagas):
        st.info(
            f"Preencha as {len(recomendacao.vagas)} vagas para gravar a "
            "alocação da proposta."
        )
    if st.button(
        "Salvar alocação do time",
        type="primary",
        disabled=not pode_salvar,
        key=f"salvar_demanda_{disputa['id']}",
    ):
        try:
            salvar_alocacao(
                con,
                int(disputa["id"]),
                [
                    {
                        "papel_id": selecao["papel_id"],
                        "vaga": selecao["vaga"],
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


def render(con) -> None:
    disputa = dados.carregar_disputa(con)
    if not disputa:
        st.warning("Nenhuma disputa disponível.")
        return
    if disputa.get("edital_versao_id"):
        _render_demanda(con, disputa)
        return
    if not disputa.get("preset_versao_id"):
        st.warning("Nenhuma disputa com preset publicado.")
        return
    _render_preset(con, disputa)


def _render_preset(con, disputa: dict) -> None:
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

    _aviso_pendencias(con, disputa)

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
            st.warning(
                f"Nenhum candidato elegível para {vaga['papel_nome']}. "
                "Eliminados não são sugeridos nem alocados."
            )
        ids = [int(valor) for valor in grupo["candidato_id"].tolist()]
        existentes = por_papel.get(papel, [])
        existente_id = (
            int(existentes[indice_vaga]["candidato_id"])
            if indice_vaga < len(existentes)
            else None
        )
        if existente_id is not None and existente_id not in ids:
            linha_existente = linhas_por_chave.get((papel, existente_id))
            if (
                linha_existente is not None
                and linha_existente["status_gates"] == "eliminado"
            ):
                st.warning(
                    f"A alocação anterior de {linha_existente['nome']} em "
                    f"{vaga['papel_nome']} está eliminada pelos gates e "
                    "não será mantida automaticamente."
                )
            existente_id = None
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
        "Pesos iguais por vaga nesta demonstração."
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
        key=f"salvar_preset_{disputa['id']}",
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
