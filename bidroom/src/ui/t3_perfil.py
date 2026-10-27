"""T3 — ficha do candidato, evidências e atualização versionada (handoff §7)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd
import streamlit as st

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

    scores = perfil["scores"]
    if not scores:
        st.warning(
            "O currículo atual ainda não tem pontuação. Rode o score da disputa "
            "ou atualize o currículo."
        )
        _render_fatos(perfil)
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

    _render_fatos(perfil)
    _render_upload(con, int(selecionado), curriculo)


def _render_fatos(perfil: dict) -> None:
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
                f"{redigir(item.get('empresa') or '—')} · {_periodo(item)}"
            )
            with st.expander(titulo):
                st.write(
                    f"Domínio: **{item.get('dominio') or 'não mapeado'}** · "
                    f"Tecnologias: {redigir(str(item.get('tecnologias') or '—'))}"
                )
                if item.get("resumo"):
                    st.write(redigir(item["resumo"]))
                _evidencia(item.get("evidencia"))

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
                f"{redigir(item.get('emissor') or 'emissor não informado')}"
            ):
                st.caption(f"Ano: {item.get('ano') or '—'} · N0 sem anexo")
                _evidencia(item.get("evidencia"))

    with abas[3]:
        if not perfil["formacoes"]:
            st.caption("Nenhuma formação extraída.")
        for item in perfil["formacoes"]:
            with st.expander(
                f"{redigir(item.get('grau') or 'Formação')} · "
                f"{redigir(item.get('curso') or '—')}"
            ):
                st.write(
                    f"{redigir(item.get('instituicao') or '—')} · "
                    f"{item.get('ano_inicio') or '—'}–{item.get('ano_fim') or '—'}"
                )
                _evidencia(item.get("evidencia"))

    with abas[4]:
        if not perfil["idiomas"]:
            st.caption("Nenhum idioma extraído.")
        for item in perfil["idiomas"]:
            with st.expander(f"{item['idioma']} · {item.get('nivel') or '—'}"):
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
