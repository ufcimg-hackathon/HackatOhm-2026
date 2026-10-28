"""T6 — página Governança.

Tabs:
- Auditoria: filtros por ação, ator, entidade, período; tabela de registros.
- Dono e freshness: candidatos e preset com dono, última atualização, dias e status.
- Cofre PII: estatísticas, total por tipo, últimas leituras auditadas, chave configurada.
- Acesso por papel: matriz descritiva, nota de que MVP não tem login.
- Inbox: botão “Sincronizar pasta”, lista de arquivos com status, versão, candidato, erro.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import streamlit as st

from src.governance.atualidade import listar_pendencias, resumo_candidato
from src.governance.ciclo import (
    atualizar_item_fila,
    classificar_disputa,
    listar_atualizacoes_pendentes,
    listar_fila,
    recalcular_pendentes,
    verificar_vencimentos,
)
from src.governance.comprovacao import (
    ROTULOS as ROTULOS_COMPROVACAO,
    listar_pendencias_comprovacao,
    rejeitar_evidencia,
    validar_evidencia,
    verificar_vencimento_evidencias,
)
from src.governance.reconhecimento import ROTULOS_CONQUISTA, atualizar_meta, criar_meta, criar_programa, listar_conquistas, listar_programas, ranking_cultural, rejeitar_conquista, revogar_conquista, sincronizar_conquistas, validar_conquista, resumo_participacao
from src.governance.vault import CofrePII, estatisticas
from src.ingestion.db import criar_schema
from src.ingestion.inbox import esca, sincronizar
from src.ui.comum import caminho_inbox
from src.ui.dados import carregar_disputa, listar_candidatos, ROTULO_STATUS_CURACAO


def render(con: object) -> None:
    st.title("Governança e LGPD")
    st.caption("MVP A · Fase 6")

    # ---- garantir schema ----
    criar_schema(con)  # garante tabela inbox_arquivos

    # ---------- abas ----------
    abas = st.tabs(
        [
            "Auditoria",
            "Dono & Freshness",
            "Cofre PII",
            "Acesso por Papel",
            "Inbox",
            "Fila de revisão",
            "Reconhecimento",
        ]
    )

    # -------------------------------------------------------
    # ABA 1 — AUDITORIA
    # -------------------------------------------------------
    with abas[0]:
        st.subheader("Log de auditoria")

        # filtros
        col_f1, col_f2, col_f3 = st.columns(3)
        with col_f1:
            acao_filtro = st.text_input("Ação", placeholder="ex: ingerir_curriculo")
        with col_f2:
            ator_filtro = st.text_input("Ator", placeholder="ex: inbox")
        with col_f3:
            entidade_filtro = st.text_input("Entidade", placeholder="ex: curriculos")

        # construir WHERE dinâmico
        where_parts = ["1=1"]
        params = []
        if acao_filtro:
            where_parts.append("LOWER(acao) LIKE ?")
            params.append(f"%{acao_filtro.lower()}%")
        if ator_filtro:
            where_parts.append("LOWER(ator) LIKE ?")
            params.append(f"%{ator_filtro.lower()}%")
        if entidade_filtro:
            where_parts.append("LOWER(entidade) LIKE ?")
            params.append(f"%{entidade_filtro.lower()}%")

        # período
        col_p1, col_p2 = st.columns(2)
        with col_p1:
            data_inicio = st.date_input("De", value=None)
        with col_p2:
            data_fim = st.date_input("Até", value=None)
        if data_inicio:
            # SQLite armazena datetime; comparamos data iso
            where_parts.append("data_hora >= ?")
            params.append(f"{data_inicio.isoformat()} 00:00:00")
        if data_fim:
            where_parts.append("data_hora <= ?")
            params.append(f"{data_fim.isoformat()} 23:59:59")

        query = f"SELECT * FROM registros_auditoria WHERE {' AND '.join(where_parts)} ORDER BY id DESC LIMIT 200"
        rows = con.execute(query, params).fetchall() if params else con.execute(
            "SELECT * FROM registros_auditoria ORDER BY id DESC LIMIT 200"
        ).fetchall()

        if rows:
            # pretty JSON for detalhes
            for row in rows:
                st.markdown(
                    f"**#{row['id']}** — {row['data_hora']} — "
                    f"**{row['acao']}** — **{row['ator'] or '—'}** — "
                    f"{row['entidade']} (id={row['entidade_id'] or '—'})"
                )
                detalhes = json.loads(row["detalhes_json"]) if row["detalhes_json"] else {}
                st.caption(json.dumps(detalhes, ensure_ascii=False, indent=2))
        else:
            st.caption("Nenhuma auditoria encontrada com os filtros atuais.")

        # pequena estatística: total de ações distintas
        acoes_distintas = con.execute(
            "SELECT DISTINCT acao FROM registros_auditoria ORDER BY acao"
        ).fetchall()
        with st.expander("Ações registradas"):
            for r in acoes_distintas:
                st.write(r["acao"])

    # -------------------------------------------------------
    # ABA 2 — DONO & FRESHNESS
    # -------------------------------------------------------
    with abas[1]:
        st.subheader("Dono e freshness")

        # --- candidatos ---
        st.markdown("#### Candidatos")
        candidatos_df = listar_candidatos(con)
        if not candidatos_df.empty:
            linhas_freshness = []
            for _, candidato in candidatos_df.iterrows():
                resumo = resumo_candidato(con, int(candidato["id"]))
                status = resumo.get("por_status", {})
                linhas_freshness.append(
                    {
                        "Candidato": candidato["nome_pseudonimo"],
                        "Curadoria": ROTULO_STATUS_CURACAO.get(
                            candidato["status_curacao"], candidato["status_curacao"]
                        ),
                        "Fatos": resumo.get("total", 0),
                        "Sem confirmação": status.get("sem_confirmacao", 0),
                        "Atenção": status.get("atencao", 0),
                        "Desatualizado": status.get("desatualizado", 0),
                        "Vencido": status.get("vencido", 0),
                        "Pendências": len(resumo.get("pendencias", [])),
                    }
                )
            st.dataframe(
                linhas_freshness,
                column_config={},
                hide_index=True,
                use_container_width=True,
            )
            limites = resumo_candidato(
                con, int(candidatos_df.iloc[0]["id"])
            ).get("limiares", {})
            st.caption(
                "Freshness: atenção após "
                f"{limites.get('atencao_dias', 180)} d; desatualizado após "
                f"{limites.get('desatualizado_dias', 365)} d. "
                "Ingestão não é confirmação."
            )
        else:
            st.caption("Nenhum candidato cadastrado.")

        st.markdown("#### Pendências de atualidade")
        pendencias = listar_pendencias(con)
        if pendencias:
            st.dataframe(
                [
                    {
                        "Candidato ID": item["candidato_id"],
                        "Tipo": item.get("tipo_fato"),
                        "Descrição": item.get("descricao"),
                        "Situação": item.get("rotulo_frescura"),
                        "Última confirmação": item.get("ultima_confirmacao")
                        or "—",
                        "Validade": item.get("valido_ate") or "—",
                        "Responsável": item.get("responsavel") or "—",
                        "Fonte": item.get("fonte") or "—",
                    }
                    for item in pendencias
                ],
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.success("Nenhuma pendência de atualidade.")

        # --- preset ---
        st.markdown("#### Preset")
        preset_info = st.expander("Informações do preset atual")
        with preset_info:
            disputa = carregar_disputa(con)
            if disputa:
                st.write(f"Disputa: {disputa.get('nome_ficticio')}")
                st.write(f"Preset versão: {disputa.get('preset_versao_id')}")
                st.write(f"Preset: {disputa.get('preset_nome') or '—'}")
                dono_preset = con.execute(
                    "SELECT p.dono FROM presets p"
                    " JOIN versoes_preset v ON v.preset_id = p.id"
                    " WHERE v.id = ?",
                    (disputa.get("preset_versao_id"),),
                ).fetchone()
                st.write(f"Dono do preset: {dono_preset['dono'] if dono_preset else '—'}")
            else:
                st.write("Nenhuma disputa ativa.")

    # -------------------------------------------------------
    # ABA 3 — COFRE PII
    # -------------------------------------------------------
    with abas[2]:
        st.subheader("Status do Cofre PII")
        try:
            stats = estatisticas(con)
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total de registros PII", stats.get("total", 0))
            with col2:
                st.metric("E-mails", stats.get("email", 0))
            with col3:
                st.metric("Telefones", stats.get("telefone", 0))
            st.write(f"Última leitura auditada: {stats.get('ultima_ler_pii', '—')}")
            # chave configurada
            chave_env = os.environ.get("BIDROOM_PII_KEY", "")
            dotenv_path = Path(".env")
            chave_arquivo = dotenv_path.exists()
            st.write(f"Chave configurada: {'sim' if (chave_env or chave_arquivo) else 'não'}")
            # never reveal the key value
            st.caption("Never: the key value is never displayed or decrypted in the UI.")
        except Exception as e:
            st.error(f"Erro ao carregar estatísticas do cofre: {e}")

    # -------------------------------------------------------
    # ABA 4 — ACESSO POR PAPEL
    # -------------------------------------------------------
    with abas[3]:
        st.subheader("Matriz de acesso (descritiva — MVP sem login)")
        st.markdown(
            """
            **Papéis definidos (apenas visual, sem enforcement):**

            | Papel | Pode ver ranking | Pode ver perfil | Pode upload currículo | Pode alocar | Pode ver auditoria | Pode sincronizar inbox |
            |---|---|---|---|---|---|---|
            | Admin / Governança | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
            | Recrutador | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
            | Gestor da disputa | ✅ | ✅ | ❌ | ✅ | ❌ | ❌ |
            | Auditor / LGPD | ✅ | ✅ | ❌ | ❌ | ✅ | ❌ |

            *Nota: o MVP não possui autenticação. Esta matriz é **descritiva** e serve de referência apenas.*
            """
        )

    # -------------------------------------------------------
    # ABA 5 — INBOX
    # -------------------------------------------------------
    with abas[4]:
        st.subheader("Sincronizar pasta (data/inbox/)")
        st.caption(
            "Arquivos .docx encontrados. Mesmo checksum = 'sem alterações'. "
            "Falha de parse aparece como 'erro'. Arquivo preservado para inspeção."
        )

        # garantir pasta existe
        pasta_path = caminho_inbox()
        if not pasta_path.exists():
            pasta_path.mkdir(parents=True, exist_ok=True)
            st.info("Pasta `data/inbox/` criada. Coloque seus .docx nela e clique em Sincronizar.")

        # escanear
        with st.spinner("Escaneando pasta..."):
            scan = esca(con, pasta_path)

        if not scan:
            st.info("Nenhum arquivo .docx encontrado na pasta inbox.")
        else:
            # resumo
            status_counts = {}
            for s in scan:
                status_counts[s["status"]] = status_counts.get(s["status"], 0) + 1

            cols = st.columns(min(3, len(status_counts)))
            for i, (status_name, count) in enumerate(status_counts.items()):
                with cols[i % len(cols)]:
                    st.metric(status_name, count)

            # table
            st.markdown("#### Detalhes")
            table_data = []
            for s in scan:
                error_msg = s.get("erro", "")[:30] if s.get("erro") else ""
                table_data.append(
                    {
                        "Arquivo": s["nome"],
                        "Status": s["status"],
                        "Versão": s.get("versao", "—"),
                        "Candidato ID": s.get("candidato_id", "—"),
                        "Erro (truncado)": error_msg,
                    }
                )
            st.dataframe(table_data, hide_index=True, use_container_width=True)

        # botão sincronizar
        if st.button("Sincronizar pasta", type="primary"):
            with st.spinner("Sincronizando..."):
                cofre = CofrePII.carregar_ou_criar()
                resumo = sincronizar(con, cofre, pasta_path, ator="inbox")
            st.success(
                f"Sincronização concluída: "
                f"{resumo['processados']} processados, "
                f"{resumo['erros']} erros, "
                f"{resumo['duplicados']} duplicados, "
                f"{resumo['candidatos_criados']} candidatos criados, "
                f"{resumo['recalculados']} recalculados."
            )
            st.rerun()

    # -------------------------------------------------------
    # ABA 6 — FILA DE REVISÃO E CICLO CONTÍNUO
    # -------------------------------------------------------
    with abas[5]:
        st.subheader("Fila de revisão e atualizações pendentes")
        st.caption(
            "Mudanças e vencimentos marcam todas as disputas abertas. "
            "A decisão anterior é preservada até o recálculo."
        )

        if st.button("Verificar vencimentos por tempo", key="verificar_vencimentos"):
            with st.spinner("Verificando validade e revisão periódica..."):
                resultado = verificar_vencimentos(con, ator="ui")
            total = (
                len(resultado["vencidos_fatos"])
                + len(resultado["vencidos_disponibilidade"])
                + len(resultado["revisoes_periodicas"])
            )
            st.success(f"{total} evento(s) de atualização identificado(s).")
            st.rerun()

        st.markdown("#### Disputas")
        disputas = con.execute(
            "SELECT id, nome_ficticio, status FROM disputas ORDER BY id DESC"
        ).fetchall()
        if disputas:
            st.dataframe(
                [
                    {
                        "Disputa": row["nome_ficticio"],
                        "Status": row["status"],
                        "Recomendação": classificar_disputa(
                            con, int(row["id"])
                        )["status"],
                        "Pendências": classificar_disputa(
                            con, int(row["id"])
                        )["pendencias"],
                    }
                    for row in disputas
                ],
                hide_index=True,
                width="stretch",
            )
        else:
            st.info("Nenhuma disputa cadastrada.")

        pendentes = listar_atualizacoes_pendentes(con)
        st.markdown(f"#### Atualizações pendentes ({len(pendentes)})")
        if pendentes:
            st.dataframe(
                [
                    {
                        "Disputa": item["nome_ficticio"],
                        "Candidato": item["nome_pseudonimo"],
                        "Tipo": item["tipo"],
                        "Motivo": item["motivo"],
                        "Criado em": item["criado_em"],
                        "Erro": item.get("erro") or "—",
                    }
                    for item in pendentes
                ],
                hide_index=True,
                width="stretch",
            )
            if st.button("Recalcular disputas pendentes", key="recalcular_pendentes"):
                with st.spinner("Recalculando..."):
                    resultado = recalcular_pendentes(con, ator="ui")
                if resultado["falhas"]:
                    st.error(
                        "Algumas disputas continuam pendentes: "
                        + "; ".join(
                            f"#{item['disputa_id']}: {item['erro']}"
                            for item in resultado["falhas"]
                        )
                    )
                else:
                    st.success(
                        f"{len(resultado['recalculadas'])} disputa(s) "
                        "recalculada(s); pendências resolvidas."
                    )
                    st.rerun()
        else:
            st.success("Nenhuma atualização pendente.")

        st.markdown("#### Fila de revisão")
        fila = listar_fila(con)
        if fila:
            st.dataframe(
                [
                    {
                        "ID": item["id"],
                        "Candidato": item["nome_pseudonimo"],
                        "Tipo": item["tipo"],
                        "Motivo": item["motivo"],
                        "Status": item["status"],
                        "Responsável": item.get("responsavel") or "—",
                        "Prazo": item.get("prazo") or "—",
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
                            f"#{item['id']} · {item['nome_pseudonimo']} · "
                            f"{item['tipo']} · {item['motivo']}"
                            for item in abertos
                            if int(item["id"]) == int(valor)
                        ),
                        str(valor),
                    ),
                    key="fila_item_governanca",
                )
                if st.button("Marcar item como resolvido", key="resolver_fila_governanca"):
                    atualizar_item_fila(con, int(item_id), "resolvida", ator="ui")
                    st.success("Item da fila resolvido.")
                    st.rerun()
        else:
            st.info("Nenhum item na fila de revisão.")

        st.markdown("#### Comprovações")
        if st.button(
            "Verificar vencimento de comprovantes",
            key="verificar_vencimento_evidencias",
        ):
            with st.spinner("Verificando validade dos comprovantes..."):
                vencidas = verificar_vencimento_evidencias(con, ator="ui")
            st.success(f"{len(vencidas)} comprovante(s) vencido(s).")
            st.rerun()

        pendencias_prova = listar_pendencias_comprovacao(con)
        if pendencias_prova:
            st.dataframe(
                [
                    {
                        "Candidato ID": item["candidato_id"],
                        "Tipo": item["tipo_fato"],
                        "Fato": item["descricao"],
                        "Estado": item["rotulo"],
                    }
                    for item in pendencias_prova
                ],
                hide_index=True,
                width="stretch",
            )
        else:
            st.success("Todas as certificações, formações e experiências têm prova válida.")

        evidencias = con.execute(
            "SELECT e.*, c.nome_pseudonimo FROM evidencias_documentais e"
            " JOIN curriculos cur ON cur.id = ("
            "   CASE e.tipo_fato"
            "     WHEN 'experiencia' THEN (SELECT curriculo_id FROM experiencias WHERE id = e.fato_id)"
            "     WHEN 'certificacao' THEN (SELECT curriculo_id FROM certificacoes WHERE id = e.fato_id)"
            "     WHEN 'formacao' THEN (SELECT curriculo_id FROM formacoes WHERE id = e.fato_id)"
            "   END)"
            " JOIN candidatos c ON c.id = cur.candidato_id"
            " ORDER BY e.id DESC"
        ).fetchall()
        if evidencias:
            st.dataframe(
                [
                    {
                        "ID": row["id"],
                        "Candidato": row["nome_pseudonimo"],
                        "Tipo": row["tipo_fato"],
                        "Arquivo": row["nome_arquivo"],
                        "Status": ROTULOS_COMPROVACAO.get(
                            row["status"], row["status"]
                        ),
                        "Revisor": row["revisor"] or "—",
                        "Validade": row["valido_ate"] or "—",
                    }
                    for row in evidencias
                ],
                hide_index=True,
                width="stretch",
            )
            abertas = [
                row
                for row in evidencias
                if row["status"] in ("enviado", "validado")
            ]
            if abertas:
                evidencia_id = st.selectbox(
                    "Comprovante para analisar",
                    [int(row["id"]) for row in abertas],
                    format_func=lambda valor: next(
                        (
                            f"#{row['id']} · {row['nome_pseudonimo']} · "
                            f"{row['nome_arquivo']} · {row['status']}"
                            for row in abertas
                            if int(row["id"]) == int(valor)
                        ),
                        str(valor),
                    ),
                    key="evidencia_analise",
                )
                revisor = st.text_input(
                    "Revisor", value="governanca", key="evidencia_revisor"
                )
                validade_prova = st.date_input(
                    "Validade da comprovação",
                    value=None,
                    key="evidencia_validade",
                )
                motivo_rejeicao = st.text_input(
                    "Motivo da rejeição (obrigatório para rejeitar)",
                    key="evidencia_motivo",
                )
                c_validar, c_rejeitar = st.columns(2)
                with c_validar:
                    if st.button("Validar comprovante", key="validar_comprovante"):
                        try:
                            validar_evidencia(
                                con,
                                int(evidencia_id),
                                revisor=revisor,
                                ator="ui",
                                valido_ate=validade_prova.isoformat()
                                if validade_prova
                                else None,
                            )
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"Falha ao validar: {exc}")
                        else:
                            st.success("Comprovante validado; disputas marcadas.")
                            st.rerun()
                with c_rejeitar:
                    if st.button("Rejeitar comprovante", key="rejeitar_comprovante"):
                        try:
                            rejeitar_evidencia(
                                con,
                                int(evidencia_id),
                                revisor=revisor,
                                motivo=motivo_rejeicao,
                                ator="ui",
                            )
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"Falha ao rejeitar: {exc}")
                        else:
                            st.success("Comprovante rejeitado com motivo.")
                            st.rerun()
        else:
            st.info("Nenhum comprovante enviado ainda.")

    # -------------------------------------------------------
    # ABA 7 — RECONHECIMENTO E CULTURA
    # -------------------------------------------------------
    with abas[6]:
        st.subheader("Cultura, metas e reconhecimento")
        st.caption(
            "Conquista é a certificação concluída e validada no ano de referência. "
            "A data de conclusão manda no ano; enviar o comprovante depois não "
            "move a conquista para o ano atual. Reenvio não duplica a contagem."
        )
        st.caption(
            "O ranking cultural é somente leitura: não altera elegibilidade, "
            "pontuação da demanda nem alocação. A visibilidade é uma política "
            "do programa; o MVP não tem login e exibe a visão administrativa."
        )

        programas = listar_programas(con)
        with st.expander("Criar programa de reconhecimento"):
            with st.form("form_programa_reconhecimento"):
                nome_programa = st.text_input(
                    "Nome do programa", value="Programa de certificação"
                )
                ano_programa = st.number_input(
                    "Ano de referência",
                    min_value=2000,
                    max_value=2100,
                    value=datetime.now().year,
                    step=1,
                )
                visibilidade = st.selectbox(
                    "Visibilidade",
                    ["agregado", "publico", "privado"],
                    index=0,
                )
                desempate = st.selectbox(
                    "Critério de desempate",
                    ["data_conclusao_mais_recente", "nome", "certificacoes"],
                    index=0,
                )
                descricao_programa = st.text_area(
                    "Descrição / política de participação", value=""
                )
                if st.form_submit_button("Criar programa"):
                    try:
                        criar_programa(
                            con,
                            nome_programa,
                            int(ano_programa),
                            descricao=descricao_programa,
                            regra={"desempate": desempate},
                            visibilidade=visibilidade,
                            ator="ui",
                        )
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"Falha ao criar programa: {exc}")
                    else:
                        st.success("Programa criado.")
                        st.rerun()

        if not programas:
            st.info(
                "Nenhum programa de reconhecimento cadastrado. Crie um programa "
                "para acompanhar conquistas e metas."
            )
        else:
            programa_id = st.selectbox(
                "Programa",
                [int(item["id"]) for item in programas],
                format_func=lambda valor: next(
                    (
                        f"{item['nome']} · {item['ano']} · {item['visibilidade']}"
                        for item in programas
                        if int(item["id"]) == int(valor)
                    ),
                    str(valor),
                ),
                key="programa_reconhecimento",
            )
            programa = next(
                item for item in programas if int(item["id"]) == int(programa_id)
            )
            st.caption(
                f"Regra publicada: contar {programa['regra'].get('contar')} "
                f"({programa['regra'].get('pontos_por_certificacao', 1)} ponto(s) "
                f"por certificação distinta); desempate: "
                f"{programa['regra'].get('desempate')}."
            )
            if st.button(
                "Sincronizar conquistas validadas",
                key="sincronizar_conquistas",
            ):
                with st.spinner("Sincronizando certificações e comprovações..."):
                    resultado = sincronizar_conquistas(
                        con, int(programa_id), ator="ui"
                    )
                st.success(
                    f"{resultado['criadas']} criada(s), "
                    f"{resultado['atualizadas']} atualizada(s), "
                    f"{resultado['ignoradas']} preservada(s) por decisão anterior."
                )
                st.rerun()

            st.markdown("#### Ranking cultural")
            ranking = ranking_cultural(con, int(programa_id))
            if ranking:
                st.dataframe(
                    [
                        {
                            "Posição": item["posicao"],
                            "Candidato": item["nome"],
                            "Conquistas": item["total"],
                            "Certificações": ", ".join(item["certificacoes"]),
                            "Última conclusão": item["ultima_conclusao"] or "—",
                            "Desempate": item["regra_desempate"] or "—",
                        }
                        for item in ranking
                    ],
                    hide_index=True,
                    width="stretch",
                )
            else:
                st.info(
                    "Ainda não há conquistas validadas neste programa. "
                    "Sincronize após validar os comprovantes."
                )

            st.divider()
            st.markdown("#### Visão do colaborador")
            candidatos = listar_candidatos(con)
            if candidatos.empty:
                st.info("Nenhum candidato cadastrado.")
            else:
                candidato_id = st.selectbox(
                    "Candidato",
                    [int(item) for item in candidatos["id"].tolist()],
                    format_func=lambda valor: next(
                        (
                            f"{row['nome_pseudonimo']} · {row['status_curacao']}"
                            for _, row in candidatos.iterrows()
                            if int(row["id"]) == int(valor)
                        ),
                        str(valor),
                    ),
                    key="candidato_reconhecimento",
                )
                resumo = resumo_participacao(
                    con, int(candidato_id), programa_id=int(programa_id)
                )
                m1, m2, m3, m4 = st.columns(4)
                m1.metric(
                    "Conquistas validadas",
                    resumo["total_conquistas_validadas"],
                )
                m2.metric(
                    "Comprovações pendentes",
                    len(resumo["comprovacao"]["pendencias"]),
                )
                m3.metric(
                    "Metas pendentes",
                    sum(
                        1
                        for meta in resumo["metas"]
                        if meta["status"] == "pendente"
                    ),
                )
                m4.metric(
                    "Fatos sem confirmação",
                    resumo["atualidade"]
                    .get("por_status", {})
                    .get("sem_confirmacao", 0),
                )

                conquistas = resumo["conquistas"]
                if conquistas:
                    st.dataframe(
                        [
                            {
                                "Certificação": item["certificacao"],
                                "Ano": item["ano_referencia"],
                                "Conclusão": item["data_conclusao"] or "—",
                                "Status": ROTULOS_CONQUISTA.get(
                                    item["status"], item["status"]
                                ),
                                "Comprovação": ROTULOS_COMPROVACAO.get(
                                    item["estado_evidencia"],
                                    item["estado_evidencia"],
                                ),
                            }
                            for item in conquistas
                        ],
                        hide_index=True,
                        width="stretch",
                    )
                else:
                    st.info("Nenhuma conquista registrada para este programa.")

                pendencias_colaborador = resumo["comprovacao"]["pendencias"]
                if pendencias_colaborador:
                    st.markdown("##### Comprovações faltantes")
                    st.dataframe(
                        [
                            {
                                "Tipo": item["tipo_fato"],
                                "Fato": item["descricao"],
                                "Estado": item["rotulo"],
                            }
                            for item in pendencias_colaborador
                        ],
                        hide_index=True,
                        width="stretch",
                    )
                if resumo["proximos_passos"]:
                    st.markdown("##### Próximos passos")
                    for passo in resumo["proximos_passos"]:
                        st.write(f"- {passo}")

                metas = resumo["metas"]
                if metas:
                    st.dataframe(
                        [
                            {
                                "Meta": item["descricao"],
                                "Alvo": item["certificacao_alvo"] or "—",
                                "Prazo": item["prazo"] or "—",
                                "Status": item["status"],
                                "Concluída em": item["concluido_em"] or "—",
                            }
                            for item in metas
                        ],
                        hide_index=True,
                        width="stretch",
                    )
                    pendentes_meta = [
                        item for item in metas if item["status"] == "pendente"
                    ]
                    if pendentes_meta:
                        meta_id = st.selectbox(
                            "Meta para concluir",
                            [int(item["id"]) for item in pendentes_meta],
                            format_func=lambda valor: next(
                                (
                                    item["descricao"]
                                    for item in pendentes_meta
                                    if int(item["id"]) == int(valor)
                                ),
                                str(valor),
                            ),
                            key="meta_concluir",
                        )
                        if st.button("Marcar meta como concluída", key="concluir_meta"):
                            atualizar_meta(
                                con, int(meta_id), "concluida", ator="ui"
                            )
                            st.success("Meta concluída.")
                            st.rerun()

                with st.expander("Criar meta de desenvolvimento"):
                    with st.form("form_meta_desenvolvimento"):
                        descricao_meta = st.text_input(
                            "Descrição da meta",
                            value="Concluir certificação",
                        )
                        alvo_meta = st.text_input(
                            "Certificação alvo", value=""
                        )
                        prazo_meta = st.date_input(
                            "Prazo", value=None, key="prazo_meta"
                        )
                        if st.form_submit_button("Criar meta"):
                            try:
                                criar_meta(
                                    con,
                                    int(candidato_id),
                                    descricao=descricao_meta,
                                    programa_id=int(programa_id),
                                    certificacao_alvo=alvo_meta or None,
                                    prazo=prazo_meta.isoformat()
                                    if prazo_meta
                                    else None,
                                    ator="ui",
                                )
                            except Exception as exc:  # noqa: BLE001
                                st.error(f"Falha ao criar meta: {exc}")
                            else:
                                st.success("Meta criada.")
                                st.rerun()

            st.divider()
            st.markdown("#### Revisão de conquistas")
            conquistas_programa = listar_conquistas(
                con, programa_id=int(programa_id)
            )
            if not conquistas_programa:
                st.info("Nenhuma conquista para revisar neste programa.")
            else:
                conquista_id = st.selectbox(
                    "Conquista",
                    [int(item["id"]) for item in conquistas_programa],
                    format_func=lambda valor: next(
                        (
                            f"#{item['id']} · {item['nome_pseudonimo']} · "
                            f"{item['certificacao']} · {item['status']}"
                            for item in conquistas_programa
                            if int(item["id"]) == int(valor)
                        ),
                        str(valor),
                    ),
                    key="conquista_revisao",
                )
                motivo_conquista = st.text_input(
                    "Motivo (rejeição/revogação)",
                    key="motivo_conquista",
                )
                c_validar, c_rejeitar, c_revogar = st.columns(3)
                with c_validar:
                    if st.button("Validar conquista", key="validar_conquista"):
                        try:
                            validar_conquista(
                                con,
                                int(conquista_id),
                                revisor="governanca",
                                ator="ui",
                            )
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"Falha ao validar: {exc}")
                        else:
                            st.success("Conquista validada.")
                            st.rerun()
                with c_rejeitar:
                    if st.button("Rejeitar conquista", key="rejeitar_conquista"):
                        try:
                            rejeitar_conquista(
                                con,
                                int(conquista_id),
                                motivo=motivo_conquista,
                                ator="ui",
                            )
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"Falha ao rejeitar: {exc}")
                        else:
                            st.success("Conquista rejeitada.")
                            st.rerun()
                with c_revogar:
                    if st.button("Revogar conquista", key="revogar_conquista"):
                        try:
                            revogar_conquista(
                                con,
                                int(conquista_id),
                                motivo=motivo_conquista,
                                ator="ui",
                            )
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"Falha ao revogar: {exc}")
                        else:
                            st.success("Conquista revogada.")
                            st.rerun()

    # rodapé pequeno
    st.divider()
    st.caption("Todos os dados são fictícios. PII nunca é exibida — sempre `***`.")