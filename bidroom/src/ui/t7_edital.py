"""T7 — receber edital, revisar interpretação e ativar a demanda.

A tela é deliberadamente operacional: o parser propõe, o responsável revisa e
só então a versão pode ser ativada. Nenhum requisito ambíguo ou obrigatório
rejeitado passa despercebido.
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from src.governance import editais
from src.ingestion.db import criar_schema
from src.ui.comum import cabecalho, formatar_data

ROTULO_STATUS = {
    "recebida": "Recebida",
    "extraida": "Extraída",
    "em_revisao": "Em revisão",
    "aprovada": "Aprovada",
    "ativa": "Ativa",
    "substituida": "Substituída",
    "erro": "Erro",
}
ROTULO_TIPO = {
    "papel": "Papel",
    "vaga": "Vaga",
    "competencia": "Competência",
    "experiencia_minima": "Experiência mínima",
    "certificacao": "Certificação",
    "comprovante": "Comprovante",
    "periodo_trabalho": "Período",
    "criterio_eliminatorio": "Eliminatório",
    "pontuacao": "Pontuação",
    "limite": "Limite",
    "regra_combinacao": "Combinação",
    "tratamento_documento": "Tratamento do documento",
    "outro": "Outro",
}
ROTULO_ESCOPO = {
    "individual": "Individual",
    "equipe": "Equipe",
    "empresa": "Empresa",
}
ROTULO_REQ_STATUS = {
    "pendente": "Pendente",
    "aprovado": "Aprovado",
    "alterado": "Alterado",
    "rejeitado": "Rejeitado",
}


def _flash() -> None:
    mensagem = st.session_state.pop("t7_flash", None)
    if mensagem:
        st.success(mensagem)


def _definir_flash(mensagem: str) -> None:
    st.session_state["t7_flash"] = mensagem


def _data(valor: str | None) -> str:
    return formatar_data(valor)


def _efeito(versao: dict) -> dict:
    try:
        valor = json.loads(versao.get("efeito_json") or "{}")
        return valor if isinstance(valor, dict) else {}
    except (TypeError, ValueError):
        return {}


def _render_efeito(versao: dict) -> None:
    efeito = _efeito(versao)
    if not any(efeito.get(chave) for chave in ("adicionados", "removidos", "alterados")):
        return
    st.subheader("Efeito sobre a demanda anterior")
    st.caption(
        "A versão anterior permanece consultável; a nova só substitui a antiga "
        "quando for ativada."
    )
    colunas = st.columns(3)
    with colunas[0]:
        st.markdown(f"**Adicionados ({len(efeito.get('adicionados', []))})**")
        for item in efeito.get("adicionados", [])[:20]:
            st.caption(
                f"{ROTULO_TIPO.get(item.get('tipo'), item.get('tipo'))} · "
                f"{item.get('descricao', '')}"
            )
    with colunas[1]:
        st.markdown(f"**Removidos ({len(efeito.get('removidos', []))})**")
        for item in efeito.get("removidos", [])[:20]:
            st.caption(
                f"{ROTULO_TIPO.get(item.get('tipo'), item.get('tipo'))} · "
                f"{item.get('descricao', '')}"
            )
    with colunas[2]:
        st.markdown(f"**Alterados ({len(efeito.get('alterados', []))})**")
        for item in efeito.get("alterados", [])[:20]:
            st.caption(
                f"{ROTULO_TIPO.get(item.get('tipo'), item.get('tipo'))} · "
                f"{item.get('antes', '')} → {item.get('depois', '')}"
            )


def _render_cobertura(con, versao_id: int) -> dict:
    cobertura = editais.avaliar_cobertura(con, versao_id)
    st.subheader("Cobertura da demanda")
    cols = st.columns(6)
    cols[0].metric("Papéis", cobertura["papeis"])
    cols[1].metric("Vagas", cobertura["vagas"])
    cols[2].metric("Competências", cobertura["competencias"])
    cols[3].metric("Certificações", cobertura["certificacoes"])
    cols[4].metric("Eliminatórios", cobertura["criterios_eliminatorios"])
    cols[5].metric("Pontuação", cobertura["pontuacao"])
    if cobertura["suficiente"]:
        st.success(
            "Cobertura suficiente para substituir o preset fixo: há papéis, "
            "vagas e critérios individuais."
        )
    else:
        st.warning(
            "Cobertura ainda insuficiente: revise papéis/vagas e critérios "
            "individuais antes de tratar esta versão como demanda completa."
        )
    st.caption(
        f"Requisitos de empresa: {cobertura['empresa']} · "
        f"de equipe: {cobertura['equipe']}. Esses critérios não são "
        "convertidos em pontos individuais."
    )
    return cobertura


def _render_anexos(con, versao: dict) -> None:
    st.subheader("Anexos da versão")
    anexos = editais.listar_anexos(con, int(versao["id"]))
    if anexos:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "arquivo": item["nome_arquivo"],
                        "tipo": item.get("tipo") or "—",
                        "checksum": item["checksum"][:12],
                        "criado_em": _data(item.get("criado_em")),
                    }
                    for item in anexos
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("Nenhum anexo nesta versão.")
    with st.form(f"t7_anexo_{versao['id']}"):
        arquivo = st.file_uploader(
            "Anexo .docx",
            type=["docx"],
            key=f"t7_anexo_file_{versao['id']}",
        )
        tipo = st.text_input(
            "Tipo do anexo",
            placeholder="ex: planilha de preços, termo de referência",
            key=f"t7_anexo_tipo_{versao['id']}",
        )
        if st.form_submit_button("Anexar"):
            if arquivo is None:
                st.error("Selecione um arquivo .docx.")
            else:
                try:
                    resultado = editais.anexar_anexo(
                        con,
                        int(versao["id"]),
                        nome_arquivo=arquivo.name,
                        conteudo=arquivo.getvalue(),
                        tipo=tipo or None,
                        ator="ui",
                    )
                except Exception as exc:  # noqa: BLE001 - erro visível na tela
                    st.error(f"Falha ao anexar: {exc}")
                else:
                    if resultado.get("duplicado"):
                        st.info("Este anexo já estava registrado.")
                    else:
                        _definir_flash("Anexo registrado.")
                        st.rerun()


def _render_revisao(con, versao: dict) -> None:
    st.subheader("Revisão humana")
    resumo = editais.status_revisao(con, int(versao["id"]))
    cols = st.columns(6)
    cols[0].metric("Total", resumo["total"])
    cols[1].metric("Pendentes", resumo["pendentes"])
    cols[2].metric("Aprovados", resumo["aprovados"])
    cols[3].metric("Alterados", resumo["alterados"])
    cols[4].metric("Ambíguos pendentes", resumo["ambiguos_pendentes"])
    cols[5].metric("Obrigatórios rejeitados", resumo["obrigatorios_rejeitados"])

    if resumo["ambiguos_pendentes"]:
        st.warning(
            "Há requisito ambíguo ainda pendente. A versão não pode ser "
            "aprovada até que o responsável o interprete ou rejeite."
        )
    if resumo["obrigatorios_rejeitados"]:
        st.error(
            "Há requisito obrigatório rejeitado. A ativação continuará "
            "bloqueada até que a interpretação seja ajustada."
        )

    requisitos = editais.listar_requisitos(con, int(versao["id"]))
    if not requisitos:
        st.info("Nenhum requisito extraído.")
        return

    filtro_status = st.multiselect(
        "Filtrar por status",
        list(ROTULO_REQ_STATUS),
        default=list(ROTULO_REQ_STATUS),
        format_func=lambda valor: ROTULO_REQ_STATUS[valor],
        key=f"t7_filtro_status_{versao['id']}",
    )
    filtrados = [r for r in requisitos if r["status"] in filtro_status]
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "id": req["id"],
                    "tipo": ROTULO_TIPO.get(req["tipo"], req["tipo"]),
                    "escopo": ROTULO_ESCOPO.get(req["escopo"], req["escopo"]),
                    "obrigatório": "sim" if req["obrigatorio"] else "não",
                    "status": ROTULO_REQ_STATUS.get(req["status"], req["status"]),
                    "ambíguo": "sim" if req["ambiguidade"] else "não",
                    "descrição": req["descricao"],
                    "localização": req["localizacao"],
                }
                for req in filtrados
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    if not filtrados:
        st.info("Nenhum requisito com os filtros selecionados.")
        return

    opcoes = [int(req["id"]) for req in filtrados]
    chave_req = f"t7_req_{versao['id']}"
    if st.session_state.get(chave_req) not in opcoes:
        st.session_state.pop(chave_req, None)
    requisito_id = st.selectbox(
        "Requisito",
        opcoes,
        format_func=lambda rid: next(
            (
                f"#{r['id']} · {ROTULO_TIPO.get(r['tipo'], r['tipo'])} · "
                f"{ROTULO_REQ_STATUS.get(r['status'], r['status'])} · "
                f"{r['descricao'][:90]}"
                for r in filtrados
                if int(r["id"]) == rid
            ),
            str(rid),
        ),
        key=chave_req,
    )
    requisito = next(r for r in filtrados if int(r["id"]) == requisito_id)

    st.markdown(f"**{requisito['descricao']}**")
    meta = st.columns(4)
    meta[0].caption(
        f"Tipo: {ROTULO_TIPO.get(requisito['tipo'], requisito['tipo'])}"
    )
    meta[1].caption(
        f"Escopo: {ROTULO_ESCOPO.get(requisito['escopo'], requisito['escopo'])}"
    )
    meta[2].caption(
        f"Obrigatório: {'sim' if requisito['obrigatorio'] else 'não'}"
    )
    meta[3].caption(
        f"Página: {requisito.get('pagina') or '—'} · "
        f"Local: {requisito['localizacao']}"
    )
    if requisito["ambiguidade"]:
        st.warning(f"Ambiguidade: {requisito.get('ambiguidade_motivo') or '—'}")
    st.caption("Trecho de origem")
    st.code(requisito["trecho"], language=None)
    st.caption(
        "Regra interpretada. Em critérios sem fato estruturado, use "
        "**Alterar interpretação** e inclua `\"resultado\": \"atendido\"`, "
        "`\"nao_atendido\"` ou `\"pendente\"` para registrar a decisão humana."
    )
    st.code(json.dumps(requisito["regra"], ensure_ascii=False, indent=2), language="json")
    if requisito.get("regra_original"):
        with st.expander("Regra original preservada"):
            st.code(
                json.dumps(requisito["regra_original"], ensure_ascii=False, indent=2),
                language="json",
            )
    if requisito.get("revisor"):
        st.caption(
            f"Revisado por {requisito['revisor']} em "
            f"{_data(requisito.get('revisado_em'))} · "
            f"{requisito.get('observacao_revisao') or 'sem observação'}"
        )

    if versao["status"] in ("ativa", "substituida", "erro"):
        st.info("Versão imutável ou com erro: não é possível revisar requisitos.")
        return

    decisao = st.radio(
        "Decisão",
        ["aprovar", "alterar", "rejeitar"],
        horizontal=True,
        format_func=lambda valor: {
            "aprovar": "Aprovar",
            "alterar": "Alterar interpretação",
            "rejeitar": "Rejeitar",
        }[valor],
        key=f"t7_decisao_{requisito_id}",
    )
    descricao_nova = st.text_area(
        "Descrição interpretada",
        value=requisito["descricao"],
        key=f"t7_desc_{requisito_id}",
    )
    regra_texto = st.text_area(
        "Regra interpretada (JSON)",
        value=json.dumps(requisito["regra"], ensure_ascii=False, indent=2),
        key=f"t7_regra_{requisito_id}",
    )
    observacao = st.text_area(
        "Observação / justificativa",
        key=f"t7_obs_{requisito_id}",
    )
    if st.button("Salvar revisão", type="primary", key=f"t7_salvar_{requisito_id}"):
        regra = None
        if decisao == "alterar":
            try:
                regra = json.loads(regra_texto)
            except ValueError as exc:
                st.error(f"JSON inválido: {exc}")
                return
            if not isinstance(regra, dict):
                st.error("A regra interpretada deve ser um objeto JSON.")
                return
        try:
            editais.revisar_requisito(
                con,
                requisito_id,
                revisor=st.session_state.get("t7_revisor") or "responsável",
                decisao=decisao,
                descricao=descricao_nova if decisao == "alterar" else None,
                regra=regra,
                observacao=observacao or None,
                ator="ui",
            )
        except Exception as exc:  # noqa: BLE001 - erro visível na tela
            st.error(f"Falha ao revisar: {exc}")
        else:
            _definir_flash("Revisão registrada.")
            st.rerun()


def _render_fluxo(con, versao: dict) -> None:
    st.subheader("Fluxo da versão")
    status = versao["status"]
    st.caption(f"Status atual: **{ROTULO_STATUS.get(status, status)}**")
    resumo = editais.status_revisao(con, int(versao["id"]))
    bloqueios: list[str] = []
    if resumo["pendentes"]:
        bloqueios.append(f"{resumo['pendentes']} requisito(s) pendente(s)")
    if resumo["ambiguos_pendentes"]:
        bloqueios.append(f"{resumo['ambiguos_pendentes']} ambiguidade(s) pendente(s)")
    if resumo["obrigatorios_rejeitados"]:
        bloqueios.append(
            f"{resumo['obrigatorios_rejeitados']} obrigatório(s) rejeitado(s)"
        )
    if bloqueios:
        st.warning("Bloqueios para aprovação: " + "; ".join(bloqueios))

    colunas = st.columns(3)
    if status == "extraida":
        with colunas[0]:
            if st.button("Iniciar revisão", key=f"t7_iniciar_{versao['id']}"):
                try:
                    editais.iniciar_revisao(con, int(versao["id"]), ator="ui")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Falha: {exc}")
                else:
                    _definir_flash("Versão em revisão.")
                    st.rerun()
    if status in ("extraida", "em_revisao"):
        with colunas[1]:
            if st.button(
                "Aprovar versão",
                key=f"t7_aprovar_{versao['id']}",
                disabled=bool(bloqueios),
            ):
                try:
                    editais.aprovar_versao(
                        con,
                        int(versao["id"]),
                        revisor=st.session_state.get("t7_revisor") or "responsável",
                        ator="ui",
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Falha ao aprovar: {exc}")
                else:
                    _definir_flash("Versão aprovada. A ativação está liberada.")
                    st.rerun()
    if status == "aprovada":
        with colunas[2]:
            if st.button(
                "Ativar demanda",
                type="primary",
                key=f"t7_ativar_{versao['id']}",
            ):
                try:
                    resultado = editais.ativar_versao(
                        con, int(versao["id"]), ator="ui", criar_disputa=True
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Falha ao ativar: {exc}")
                else:
                    _definir_flash(
                        "Demanda ativada. "
                        f"Disputas vinculadas: {resultado.get('disputas', [])}."
                    )
                    st.rerun()
    if status == "ativa":
        st.success("Esta é a versão ativa da demanda.")
    if status == "substituida":
        st.info("Versão histórica substituída por uma retificação ativa.")
    if status == "erro":
        st.error(f"Erro de extração: {versao.get('observacao') or '—'}")


def _render_upload(con, edital: dict, versoes: list[dict]) -> None:
    if not versoes:
        st.subheader("Receber edital")
        st.caption("Apenas `.docx` no MVP. O arquivo fica no armazenamento local.")
        with st.form(f"t7_upload_original_{edital['id']}"):
            arquivo = st.file_uploader(
                "Edital original .docx",
                type=["docx"],
                key=f"t7_original_file_{edital['id']}",
            )
            if st.form_submit_button("Registrar edital", type="primary"):
                if arquivo is None:
                    st.error("Selecione o edital em .docx.")
                else:
                    try:
                        resultado = editais.receber_versao(
                            con,
                            int(edital["id"]),
                            nome_arquivo=arquivo.name,
                            conteudo=arquivo.getvalue(),
                            ator="ui",
                            tipo="original",
                        )
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Falha ao receber edital: {exc}")
                    else:
                        st.session_state[f"t7_versao_{edital['id']}"] = resultado[
                            "versao_id"
                        ]
                        _definir_flash(
                            f"Versão recebida com {resultado['requisitos']} "
                            "requisito(s) para revisão."
                        )
                        st.rerun()
        return

    st.subheader("Retificação")
    st.caption(
        "A retificação cria uma nova versão imutável e explicita o efeito "
        "sobre a versão anterior."
    )
    with st.form(f"t7_upload_retificacao_{edital['id']}"):
        arquivo = st.file_uploader(
            "Retificação .docx",
            type=["docx"],
            key=f"t7_retificacao_file_{edital['id']}",
        )
        motivo = st.text_input(
            "Motivo da retificação",
            placeholder="ex: alteração de vagas e prazo",
            key=f"t7_retificacao_motivo_{edital['id']}",
        )
        if st.form_submit_button("Registrar retificação", type="primary"):
            if arquivo is None:
                st.error("Selecione a retificação em .docx.")
            elif not motivo.strip():
                st.error("Informe o motivo da retificação.")
            else:
                try:
                    resultado = editais.receber_versao(
                        con,
                        int(edital["id"]),
                        nome_arquivo=arquivo.name,
                        conteudo=arquivo.getvalue(),
                        ator="ui",
                        tipo="retificacao",
                        motivo=motivo,
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Falha ao receber retificação: {exc}")
                else:
                    st.session_state[f"t7_versao_{edital['id']}"] = resultado[
                        "versao_id"
                    ]
                    _definir_flash(
                        f"Retificação recebida com {resultado['requisitos']} "
                        "requisito(s); revise antes de ativar."
                    )
                    st.rerun()


def render(con: object) -> None:
    criar_schema(con)
    cabecalho(
        "Edital e demanda",
        "Extrair regras com rastreabilidade, revisar e versionar antes de classificar.",
    )
    _flash()

    st.text_input(
        "Responsável pela revisão",
        value=st.session_state.get("t7_revisor", "responsável"),
        key="t7_revisor",
        help="Nome fictício usado na trilha de auditoria do MVP.",
    )

    with st.expander("Novo edital", expanded=False):
        with st.form("t7_novo_edital"):
            nome = st.text_input("Nome do edital")
            cliente = st.text_input("Cliente fictício")
            descricao = st.text_area("Descrição")
            if st.form_submit_button("Criar edital", type="primary"):
                if not nome.strip():
                    st.error("Informe o nome do edital.")
                else:
                    try:
                        edital_id = editais.criar_edital(
                            con,
                            nome,
                            cliente_ficticio=cliente or None,
                            descricao=descricao or None,
                            dono=st.session_state.get("t7_revisor") or "responsável",
                            ator="ui",
                        )
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Falha ao criar edital: {exc}")
                    else:
                        st.session_state["t7_edital_sel"] = edital_id
                        _definir_flash("Edital criado; envie a versão original.")
                        st.rerun()

    lista = editais.listar_editais(con)
    if not lista:
        st.info(
            "Nenhum edital recebido. Crie um edital e envie o `.docx` para "
            "extrair a demanda."
        )
        return

    ids = [int(item["id"]) for item in lista]
    if st.session_state.get("t7_edital_sel") not in ids:
        st.session_state.pop("t7_edital_sel", None)
    edital_id = st.selectbox(
        "Edital",
        ids,
        format_func=lambda eid: next(
            (
                f"#{e['id']} · {e['nome']} · {e['status']}"
                for e in lista
                if int(e["id"]) == eid
            ),
            str(eid),
        ),
        key="t7_edital_sel",
    )
    edital = next(item for item in lista if int(item["id"]) == edital_id)
    st.caption(
        f"Cliente: {edital.get('cliente_ficticio') or '—'} · "
        f"Dono: {edital.get('dono') or '—'} · "
        f"Criado em: {_data(edital.get('criado_em'))}"
    )

    versoes = editais.listar_versoes(con, edital_id)
    _render_upload(con, edital, versoes)
    versoes = editais.listar_versoes(con, edital_id)
    if not versoes:
        return

    ids_versoes = [int(v["id"]) for v in versoes]
    chave_versao = f"t7_versao_{edital_id}"
    if st.session_state.get(chave_versao) not in ids_versoes:
        st.session_state.pop(chave_versao, None)
    versao_id = st.selectbox(
        "Versão",
        ids_versoes,
        format_func=lambda vid: next(
            (
                f"v{v['numero']} · {v['tipo']} · "
                f"{ROTULO_STATUS.get(v['status'], v['status'])} · "
                f"{v['nome_arquivo']}"
                for v in versoes
                if int(v["id"]) == vid
            ),
            str(vid),
        ),
        key=chave_versao,
    )
    versao = next(v for v in versoes if int(v["id"]) == versao_id)
    st.caption(
        f"Arquivo: {versao['nome_arquivo']} · checksum "
        f"{versao['checksum'][:12]} · {versao.get('n_paginas') or '—'} página(s) · "
        f"recebido em {_data(versao.get('criado_em'))}"
    )
    if versao.get("observacao"):
        st.caption(f"Observação: {versao['observacao']}")

    _render_efeito(versao)
    _render_cobertura(con, versao_id)
    _render_fluxo(con, versao)
    _render_revisao(con, versao)
    _render_anexos(con, versao)

    vinculadas = [
        dict(row)
        for row in con.execute(
            "SELECT id, nome_ficticio, status FROM disputas"
            " WHERE edital_versao_id = ? ORDER BY id",
            (versao_id,),
        )
    ]
    if vinculadas:
        st.subheader("Disputas vinculadas")
        st.dataframe(
            pd.DataFrame(vinculadas),
            hide_index=True,
            width="stretch",
        )
