"""T3 — ficha do candidato, evidências e atualização versionada."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import streamlit as st

from src.governance.ciclo import (
    adicionar_certificacao,
    atualizar_disponibilidade,
    atualizar_item_fila,
    confirmar_sem_mudancas,
    listar_fila,
)
from src.governance.comprovacao import (
    ROTULOS as ROTULOS_COMPROVACAO,
    anexar_evidencia,
    listar_evidencias,
)
from src.governance.vault import CofrePII, redigir
from src.ingestion.pipeline import ingerir_registro
from src.scoring.engine import ResultadoScore
from src.scoring.resumo import gerar_resumo_proposta
from src.scoring.servico import recalcular_candidato
from src.ui import cache, dados
from src.ui.comum import (
    cabecalho,
    caminho_db,
    formatar_data,
    status_curacao,
    status_gate,
    status_gate_resultado,
)

CATEGORIAS = {
    "aderencia": "Aderência",
    "dominio_energia": "Domínio de energia",
    "certificacoes": "Certificações",
    "experiencia": "Experiência",
    "idioma": "Idioma",
    "educacao": "Educação",
    "ferramentas": "Ferramentas",
}


def _evidencia(ev: dict | None) -> None:
    if not ev:
        st.caption("Sem trecho de evidência registrado.")
        return
    st.caption(
        f"Evidência N{ev.get('nivel', 0)} · confiança "
        f"{ev.get('confianca', 0)}/100 · página {ev.get('pagina') or '—'}"
    )
    st.code(redigir(ev.get("trecho") or ""), language=None)


def _periodo(item: dict) -> str:
    inicio = item.get("data_inicio") or "—"
    fim = item.get("data_fim") or "atual"
    return f"{inicio} → {fim}"


def _rotulo_atualidade(perfil: dict, tipo: str, item: dict) -> str:
    info = perfil.get("atualidade_por_fato", {}).get((tipo, int(item["id"])))
    if not info:
        return "sem informação"
    return info.get("rotulo_frescura") or info.get("frescura") or "sem informação"


def _rotulo_comprovacao(perfil: dict, tipo: str, item: dict) -> str:
    estado = perfil.get("comprovacao_por_fato", {}).get(
        (tipo, int(item["id"])), "declarado"
    )
    return ROTULOS_COMPROVACAO.get(estado, estado)


def _render_comprovacao(
    con, perfil: dict, tipo: str, item: dict, chave: str
) -> None:
    st.markdown(
        f"**Comprovação:** {_rotulo_comprovacao(perfil, tipo, item)}"
    )
    evidencias = listar_evidencias(con, tipo, int(item["id"]))
    if evidencias:
        ultima = evidencias[0]
        st.caption(
            f"Último comprovante: {ultima['nome_arquivo']} · "
            f"status **{ultima['status']}** · "
            f"revisor: {ultima.get('revisor') or '—'} · "
            f"validade: {ultima.get('valido_ate') or '—'}"
        )
    arquivo = st.file_uploader(
        "Comprovante .docx",
        type=["docx"],
        key=f"proof_file_{chave}",
    )
    validade = st.date_input(
        "Validade do comprovante",
        value=None,
        key=f"proof_val_{chave}",
    )
    if arquivo is not None and st.button(
        "Registrar comprovante", key=f"proof_btn_{chave}"
    ):
        try:
            resultado = anexar_evidencia(
                con,
                tipo,
                int(item["id"]),
                nome_arquivo=arquivo.name,
                conteudo=arquivo.getvalue(),
                ator="ui",
                valido_ate=validade.isoformat() if validade else None,
            )
        except Exception as exc:  # noqa: BLE001 - erro visível na UI
            st.error(f"Falha ao registrar comprovante: {exc}")
        else:
            if resultado["duplicado"]:
                st.info(
                    "Este documento já estava registrado para o fato; "
                    "nenhuma duplicata foi criada."
                )
            else:
                st.success(
                    "Comprovante enviado; análise pendente na Governança."
                )
                st.rerun()


ROTULOS_REQUISITO = {
    "atendido": "Atendido",
    "pendente": "Pendente",
    "nao_atendido": "Não atendido",
    "eliminado": "Eliminado",
}


def _render_demanda_scores(con, candidato_id: int) -> bool:
    """Mostra a nota do edital e a explicação por requisito, se existirem."""
    disputa = dados.carregar_disputa(con)
    if not disputa or not disputa.get("edital_versao_id"):
        return False
    resultados = dados.carregar_resultados_demanda(
        con,
        int(disputa["id"]),
        candidato_id,
        int(disputa["edital_versao_id"]),
    )
    if not resultados:
        st.info(
            "A demanda ativa ainda não tem pontuação para este candidato. "
            "Abra o ranking e clique em calcular a demanda."
        )
        return False

    st.subheader("Aderência à demanda")
    rotulos = {
        int(item["papel_id"]): (
            f"{item['papel_nome']} · nota {item['nota_tecnica']:.2f} "
            f"({status_gate(item['status'])})"
        )
        for item in resultados
    }
    papel_id = st.selectbox(
        "Papel na demanda",
        [int(item["papel_id"]) for item in resultados],
        format_func=lambda valor: rotulos.get(valor, str(valor)),
        key=f"demanda_score_{candidato_id}",
    )
    item = next(
        item for item in resultados if int(item["papel_id"]) == int(papel_id)
    )
    detalhe = item["detalhe"]
    st.caption(
        f"Edital v{detalhe.get('edital_numero')} · referência "
        f"{detalhe.get('data_referencia')} · status **{status_gate(item['status'])}**"
    )
    s1, s2, s3, s4, s5 = st.columns(5)
    s1.metric(
        "Nota técnica",
        f"{float(item['nota_tecnica']):.2f}",
        help=f"Máximo previsto no edital: {float(detalhe.get('pontuacao_maxima') or 0):.2f}",
    )
    s2.metric("Apoio interno", f"{float(item['score_interno_apoio']):.2f}")
    s3.metric("Confiança", f"{float(item['confianca']):.1f}")
    s4.metric("Atualidade", f"{float(item['atualidade']):.1f}")
    s5.metric("Triagem", status_gate(item["status"]))
    st.caption(
        "Nota técnica e apoio interno são leituras separadas. Confiança é "
        "heurística; não é probabilidade estatística."
    )

    requisitos = detalhe.get("requisitos") or []
    st.markdown("#### Explicação por requisito")
    st.dataframe(
        [
            {
                "Tipo": req.get("tipo"),
                "Escopo": req.get("escopo"),
                "Obrigatório": "Sim" if req.get("obrigatorio") else "Não",
                "Requisito": req.get("descricao"),
                "Status": ROTULOS_REQUISITO.get(req.get("status"), req.get("status")),
                "Pontos": (
                    f"{float(req.get('pontos_obtidos') or 0):.2f}/"
                    f"{float(req.get('pontos_possiveis') or 0):.2f}"
                ),
                "Motivo": req.get("motivo"),
                "Confiança": round(float(req.get("confianca") or 0), 1),
                "Atualidade": round(float(req.get("atualidade") or 0), 1),
                "Evidência": req.get("evidencia"),
                "Fatos": ", ".join(
                    str(fato.get("descricao"))
                    for fato in (req.get("fatos") or [])
                    if fato.get("descricao")
                )
                or "—",
            }
            for req in requisitos
        ],
        hide_index=True,
        width="stretch",
    )

    coletivos = detalhe.get("coletivos") or []
    if coletivos:
        with st.expander(f"Requisitos coletivos ({len(coletivos)})"):
            st.dataframe(
                [
                    {
                        "Escopo": req.get("escopo"),
                        "Requisito": req.get("descricao"),
                        "Status": ROTULOS_REQUISITO.get(
                            req.get("status"), req.get("status")
                        ),
                        "Motivo": req.get("motivo"),
                    }
                    for req in coletivos
                ],
                hide_index=True,
                width="stretch",
            )
    with st.expander("Como a nota da demanda foi calculada"):
        st.markdown(
            f"""
- **Nota técnica:** soma dos pontos do edital; máximo previsto
  **{float(detalhe.get('pontuacao_maxima') or 0):.2f}**.
- **Apoio interno:** {detalhe.get('formula')}.
- **Cobertura de obrigatórios:** {detalhe.get('cobertura_obrigatorios')}%.
- **Data de referência:** {detalhe.get('data_referencia')}.
- **Confiança:** {detalhe.get('confianca_heuristica')}.
"""
        )
    return True


def render(con) -> None:
    candidatos = cache.candidatos(str(caminho_db()))
    if candidatos.empty:
        st.warning("Nenhum candidato ingerido.")
        return

    ids = candidatos["id"].astype(int).tolist()
    atual = st.session_state.get("candidato_id")
    indice = ids.index(int(atual)) if atual in ids else 0
    rotulos = {
        int(row["id"]): (
            f"{row['nome_pseudonimo']} · {status_curacao(row['status_curacao'])}"
            + (
                f" · score {row['score']:.2f}"
                if pd.notna(row["score"])
                else " · sem score"
            )
        )
        for _, row in candidatos.iterrows()
    }
    selecionado = st.selectbox(
        "Candidato",
        ids,
        index=indice,
        format_func=lambda valor: rotulos.get(valor, str(valor)),
    )
    st.session_state["candidato_id"] = int(selecionado)

    perfil = cache.perfil(str(caminho_db()), int(selecionado))
    if perfil is None:
        st.warning("Candidato sem currículo processado.")
        return

    candidato = perfil["candidato"]
    curriculo = perfil["curriculo"]
    flash = st.session_state.pop("flash_perfil", None)
    if flash:
        st.success(flash)
    cabecalho(
        candidato["nome_pseudonimo"],
        "Ficha profissional com evidência rastreável. Dados pessoais nunca são "
        "exibidos: o cofre mostra apenas `***`.",
    )
    st.markdown(
        f"**Curadoria:** {status_curacao(candidato['status_curacao'])} · "
        f"**Currículo:** v{curriculo['versao']} · "
        f"**Páginas:** {curriculo.get('n_paginas') or '—'} · "
        f"**Ingestão:** {formatar_data(curriculo.get('data_ingestao'))}",
        unsafe_allow_html=True,
    )

    m1, m2, m3 = st.columns(3)
    m1.metric("Currículos do candidato", len(perfil["curriculos"]))
    m2.metric("PII no cofre", sum(perfil["pii"].values()))
    m3.metric(
        "Elegível Brasil",
        {"sim": "Sim", "nao": "Não", "nao_informado": "Não informado"}.get(
            candidato.get("elegivel_brasil"), "—"
        ),
    )

    atualidade = perfil.get("atualidade", {})
    status = atualidade.get("por_status", {})
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Fatos rastreados", atualidade.get("total", 0))
    a2.metric("Sem confirmação", status.get("sem_confirmacao", 0))
    a3.metric(
        "Atenção/desatualizado",
        status.get("atencao", 0) + status.get("desatualizado", 0),
    )
    a4.metric("Vencidos", status.get("vencido", 0))
    st.caption(
        "Atualidade: atenção após "
        f"{atualidade.get('limiares', {}).get('atencao_dias', 180)} dias; "
        "desatualizado após "
        f"{atualidade.get('limiares', {}).get('desatualizado_dias', 365)} dias. "
        "Ingestão não é confirmação."
    )
    total_pendencias = len(atualidade.get("pendencias", []))
    with st.expander(f"Pendências de atualidade ({total_pendencias})"):
        if atualidade.get("pendencias"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Tipo": item.get("tipo_fato"),
                            "Descrição": item.get("descricao"),
                            "Situação": item.get("rotulo_frescura"),
                            "Última confirmação": item.get("ultima_confirmacao")
                            or "—",
                            "Validade": item.get("valido_ate") or "—",
                            "Responsável": item.get("responsavel") or "—",
                            "Fonte": item.get("fonte") or "—",
                        }
                        for item in atualidade["pendencias"]
                    ]
                ),
                hide_index=True,
                width="stretch",
            )
        else:
            st.success("Nenhuma pendência de atualidade.")
        disp = atualidade.get("disponibilidade") or {}
        st.write(
            "Disponibilidade: "
            f"**{disp.get('estado', 'desconhecido')}**"
            + (
                f" · {disp.get('inicio')} → {disp.get('fim')}"
                if disp.get("inicio") or disp.get("fim")
                else ""
            )
        )

    with st.expander("Dados pessoais (mascarados)"):
        colunas = st.columns(3)
        for coluna, (tipo, rotulo) in zip(
            colunas, [("email", "E-mail"), ("telefone", "Telefone"), ("endereco", "Endereço")]
        ):
            with coluna:
                st.text_input(
                    rotulo,
                    value="***" if perfil["pii"].get(tipo) else "—",
                    disabled=True,
                    key=f"pii_{selecionado}_{tipo}",
                )
                st.caption(f"{perfil['pii'].get(tipo, 0)} registro(s) no cofre")
        st.caption(
            "Leitura descriptografada é auditada e pertence à tela de governança "
            "(Fase 6); esta ficha não decifra PII."
        )

    with st.expander("Ciclo de atualização"):
        st.caption(
            "Confirmar registra nova verificação sem criar qualificação. "
            "Mudanças de disponibilidade ou novos fatos viram pendência em "
            "todas as disputas abertas até o recálculo."
        )
        if st.button(
            "Confirmar que os dados continuam corretos",
            key=f"confirmar_dados_{selecionado}",
        ):
            resultado = confirmar_sem_mudancas(
                con, int(selecionado), ator="ui"
            )
            st.success(
                f"{resultado['fatos_confirmados']} fato(s) confirmado(s); "
                f"{resultado['pendencias_revisao_resolvidas']} pendência(s) "
                "de revisão periódica resolvida(s)."
            )

        st.markdown("##### Disponibilidade no período")
        with st.form(key=f"form_disponibilidade_{selecionado}"):
            d1, d2 = st.columns(2)
            inicio = d1.date_input(
                "Início", value=None, key=f"disp_inicio_{selecionado}"
            )
            fim = d2.date_input(
                "Fim", value=None, key=f"disp_fim_{selecionado}"
            )
            percentual = st.number_input(
                "Percentual disponível",
                min_value=0,
                max_value=100,
                value=100,
                key=f"disp_percentual_{selecionado}",
            )
            fonte_disp = st.text_input(
                "Fonte da disponibilidade",
                value="informado pelo profissional",
                key=f"disp_fonte_{selecionado}",
            )
            if st.form_submit_button("Salvar disponibilidade"):
                try:
                    resultado = atualizar_disponibilidade(
                        con,
                        int(selecionado),
                        inicio=inicio.isoformat() if inicio else None,
                        fim=fim.isoformat() if fim else None,
                        percentual=int(percentual),
                        fonte=fonte_disp,
                        ator="ui",
                    )
                except Exception as exc:  # noqa: BLE001 - erro visível na UI
                    st.error(f"Falha ao salvar disponibilidade: {exc}")
                else:
                    st.success(
                        "Disponibilidade registrada; "
                        f"{len(resultado['disputas'])} disputa(s) aberta(s) "
                        "ficaram pendentes de recálculo."
                    )
                    st.rerun()

        st.markdown("##### Nova certificação declarada")
        with st.form(key=f"form_certificacao_{selecionado}"):
            nome_cert = st.text_input(
                "Nome da certificação", key=f"cert_nome_{selecionado}"
            )
            emissor_cert = st.text_input(
                "Emissor", key=f"cert_emissor_{selecionado}"
            )
            ano_cert = st.number_input(
                "Ano",
                min_value=1980,
                max_value=2100,
                value=2026,
                key=f"cert_ano_{selecionado}",
            )
            if st.form_submit_button("Adicionar certificação"):
                try:
                    resultado = adicionar_certificacao(
                        con,
                        int(selecionado),
                        nome=nome_cert,
                        emissor=emissor_cert or None,
                        ano=int(ano_cert),
                        ator="ui",
                    )
                except Exception as exc:  # noqa: BLE001 - erro visível na UI
                    st.error(f"Falha ao adicionar certificação: {exc}")
                else:
                    st.success(
                        "Certificação declarada; comprovante pendente e "
                        f"{len(resultado['disputas'])} disputa(s) aberta(s) "
                        "marcada(s) para recálculo."
                    )
                    st.rerun()

        st.markdown("##### Fila de revisão do candidato")
        fila = listar_fila(con, candidato_id=int(selecionado))
        if fila:
            st.dataframe(
                [
                    {
                        "ID": item["id"],
                        "Tipo": item["tipo"],
                        "Motivo": item["motivo"],
                        "Status": item["status"],
                        "Responsável": item.get("responsavel") or "—",
                        "Erro": item.get("erro") or "—",
                    }
                    for item in fila
                ],
                hide_index=True,
                width="stretch",
            )
            abertos = [
                item
                for item in fila
                if item["status"] in ("pendente", "em_andamento")
            ]
            if abertos:
                item_id = st.selectbox(
                    "Item para resolver",
                    [int(item["id"]) for item in abertos],
                    format_func=lambda valor: next(
                        (
                            f"#{item['id']} · {item['tipo']} · {item['motivo']}"
                            for item in abertos
                            if int(item["id"]) == int(valor)
                        ),
                        str(valor),
                    ),
                    key=f"fila_item_{selecionado}",
                )
                if st.button(
                    "Marcar item como resolvido",
                    key=f"resolver_fila_{selecionado}",
                ):
                    atualizar_item_fila(
                        con, int(item_id), "resolvida", ator="ui"
                    )
                    st.success("Item da fila resolvido.")
                    st.rerun()
        else:
            st.info("Nenhuma pendência de revisão para este candidato.")

    with st.expander("Histórico de versões do currículo"):
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Versão": item["versao"],
                        "Ingestão": formatar_data(item.get("data_ingestao")),
                        "Páginas": item.get("n_paginas") or "—",
                        "Status": item.get("status_processamento"),
                    }
                    for item in perfil["curriculos"]
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        st.caption("O score de cada versão fica preservado por currículo + preset.")

    if _render_demanda_scores(con, int(selecionado)):
        _render_fatos(con, perfil)
        _render_upload(con, int(selecionado), curriculo)
        return

    scores = perfil["scores"]
    if not scores:
        st.warning(
            "O currículo atual ainda não tem pontuação. Rode o score da disputa "
            "ou atualize o currículo."
        )
        _render_fatos(con, perfil)
        _render_upload(con, int(selecionado), curriculo)
        return

    st.subheader("Aderência por papel")
    score_rotulos = {
        int(item["id"]): (
            f"{item['papel_nome']} · {item['pontuacao_final']:.2f} "
            f"({status_gate(item['detalhe'].get('status_gates', 'revisar'))})"
        )
        for item in scores
    }
    score_id = st.selectbox(
        "Papel para o resumo",
        [int(item["id"]) for item in scores],
        format_func=lambda valor: score_rotulos.get(valor, str(valor)),
        key=f"score_{selecionado}",
    )
    item = next(item for item in scores if int(item["id"]) == int(score_id))
    detalhe = item["detalhe"]
    resultado = ResultadoScore(
        papel=item["papel"],
        pontuacao_ponderada=float(item["pontuacao_ponderada"] or 0),
        confianca=float(item["confianca"] or 0),
        pontuacao_final=float(item["pontuacao_final"] or 0),
        status_gates=detalhe.get("status_gates", "revisar"),
        gates=[],
        detalhe=detalhe,
    )

    st.markdown("#### Resumo pronto para proposta")
    frases = gerar_resumo_proposta(
        candidato["nome_pseudonimo"],
        item["papel_nome"],
        resultado,
        perfil["contexto"],
    )
    st.info(" ".join(frases))
    st.caption("Gerado por regras a partir dos fatos; revise antes de enviar.")

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Score final", f"{resultado.pontuacao_final:.2f}")
    s2.metric("Ponderada", f"{resultado.pontuacao_ponderada:.2f}")
    s3.metric("Confiança", f"{resultado.confianca:.1f}")
    s4.metric("Triagem", status_gate(resultado.status_gates))

    st.markdown("#### Explicabilidade do score")
    categorias = pd.DataFrame(
        [
            {
                "Categoria": CATEGORIAS.get(c["categoria"], c["categoria"]),
                "Score": c["score"],
                "Peso": c["peso"],
                "Confiança": c["confianca"],
                "Contribuição": c["contribuicao"],
            }
            for c in detalhe.get("categorias", [])
        ]
    )
    st.dataframe(categorias, hide_index=True, width="stretch")

    gates = pd.DataFrame(
        [
            {
                "Gate": g.get("gate"),
                "Status": status_gate_resultado(g.get("status", "")),
                "Motivo": g.get("motivo"),
            }
            for g in detalhe.get("gates", [])
        ]
    )
    with st.expander("Gates G1–G6"):
        st.dataframe(gates, hide_index=True, width="stretch")

    ancoras = detalhe.get("ancoras_pm", [])
    if ancoras:
        with st.expander("Âncoras do case (subconjunto, não somadas)"):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Âncora": a.get("nome"),
                            "Peso": a.get("peso"),
                            "Atingida": "sim"
                            if a.get("atingida")
                            else ("declarada (N0)" if a.get("declarada") else "não"),
                            "Contribuição": a.get("contribuicao"),
                        }
                        for a in ancoras
                    ]
                ),
                hide_index=True,
                width="stretch",
            )

    _render_fatos(con, perfil)
    _render_upload(con, int(selecionado), curriculo)


def _render_fatos(con, perfil: dict) -> None:
    st.subheader("Fatos e evidências")
    abas = st.tabs(
        ["Experiências", "Competências", "Certificações", "Formação", "Idiomas"]
    )

    with abas[0]:
        if not perfil["experiencias"]:
            st.caption("Nenhuma experiência extraída.")
        for item in perfil["experiencias"]:
            titulo = (
                f"{redigir(item.get('papel') or 'Experiência')} · "
                f"{redigir(item.get('empresa') or '—')} · {_periodo(item)} · "
                f"{_rotulo_atualidade(perfil, 'experiencia', item)}"
            )
            with st.expander(titulo):
                st.write(
                    f"Domínio: **{item.get('dominio') or 'não mapeado'}** · "
                    f"Tecnologias: {redigir(str(item.get('tecnologias') or '—'))}"
                )
                if item.get("resumo"):
                    st.write(redigir(item["resumo"]))
                _evidencia(item.get("evidencia"))
                _render_comprovacao(
                    con, perfil, "experiencia", item, f"exp_{item['id']}"
                )

    with abas[1]:
        if not perfil["competencias"]:
            st.caption("Nenhuma competência extraída.")
        for item in perfil["competencias"]:
            nivel = (
                f"N{item['evidencia']['nivel']}"
                if item.get("evidencia")
                else "sem evidência"
            )
            with st.expander(
                f"{item['nome']} · {nivel}"
                + (f" · {item['anos']:.0f} anos" if item.get("anos") else "")
                + f" · {_rotulo_atualidade(perfil, 'competencia', item)}"
            ):
                st.caption(
                    f"Família: {item.get('familia') or '—'} · "
                    f"último uso: {item.get('ultimo_uso') or '—'}"
                )
                _evidencia(item.get("evidencia"))

    with abas[2]:
        if not perfil["certificacoes"]:
            st.caption("Nenhuma certificação extraída.")
        for item in perfil["certificacoes"]:
            with st.expander(
                f"{redigir(item['nome'])} · "
                f"{redigir(item.get('emissor') or 'emissor não informado')} · "
                f"{_rotulo_atualidade(perfil, 'certificacao', item)}"
            ):
                st.caption(f"Ano: {item.get('ano') or '—'}")
                _evidencia(item.get("evidencia"))
                _render_comprovacao(
                    con, perfil, "certificacao", item, f"cert_{item['id']}"
                )

    with abas[3]:
        if not perfil["formacoes"]:
            st.caption("Nenhuma formação extraída.")
        for item in perfil["formacoes"]:
            with st.expander(
                f"{redigir(item.get('grau') or 'Formação')} · "
                f"{redigir(item.get('curso') or '—')} · "
                f"{_rotulo_atualidade(perfil, 'formacao', item)}"
            ):
                st.write(
                    f"{redigir(item.get('instituicao') or '—')} · "
                    f"{item.get('ano_inicio') or '—'}–{item.get('ano_fim') or '—'}"
                )
                _evidencia(item.get("evidencia"))
                _render_comprovacao(
                    con, perfil, "formacao", item, f"form_{item['id']}"
                )

    with abas[4]:
        if not perfil["idiomas"]:
            st.caption("Nenhum idioma extraído.")
        for item in perfil["idiomas"]:
            with st.expander(
                f"{item['idioma']} · {item.get('nivel') or '—'} · "
                f"{_rotulo_atualidade(perfil, 'idioma', item)}"
            ):
                _evidencia(item.get("evidencia"))


def _render_upload(con, candidato_id: int, curriculo: dict) -> None:
    st.divider()
    st.subheader("Atualizar currículo")
    st.caption(
        "O upload cria uma nova versão do mesmo candidato, preserva o score "
        "antigo para auditoria e recalcula a disputa ativa."
    )
    arquivo = st.file_uploader("Novo arquivo .docx", type=["docx"])
    if arquivo is None:
        return
    if st.button("Processar atualização", type="primary"):
        conteudo = arquivo.getvalue()
        checksum = hashlib.sha256(conteudo).hexdigest()
        destino = Path("data/uploads")
        destino.mkdir(parents=True, exist_ok=True)
        caminho = destino / f"{checksum}.docx"
        if not caminho.exists():
            caminho.write_bytes(conteudo)
        cofre = CofrePII.carregar_ou_criar()
        registro = {
            "id": checksum[:12],
            "nome_arquivo": f"upload_{candidato_id}.docx",
            "nome_candidato": f"Candidato {candidato_id}",
            "link_individual": "",
            "status_download": "ok (upload)",
            "caminho_cache": str(caminho).replace("\\", "/"),
        }
        try:
            resultado = ingerir_registro(
                con,
                cofre,
                registro,
                ator="ui",
                candidato_id_forcado=candidato_id,
            )
        except Exception as erro:  # a UI deve mostrar a causa, não quebrar
            st.error(f"Falha ao atualizar: {erro}")
            return
        if resultado["status"] == "duplicado":
            st.info(
                "Este arquivo já foi processado para o candidato; nenhuma "
                "versão nova foi criada."
            )
            return
        try:
            recalcular = recalcular_candidato(con, candidato_id, ator="ui")
            mensagem = (
                f"Currículo atualizado para a versão {resultado['versao']} "
                f"({resultado['fatos']} fatos extraídos; "
                f"{len(recalcular)} papéis repontuados)."
            )
        except Exception as erro:
            mensagem = (
                f"Currículo v{resultado['versao']} salvo, mas o score não foi "
                f"recalculado: {erro}"
            )
        cache.limpar()
        st.session_state["flash_perfil"] = mensagem
        st.rerun()
