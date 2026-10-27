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
from datetime import datetime, timezone

import streamlit as st

from src.governance.auditoria import registrar, ultimos, por_entidade
from src.governance.vault import CofrePII, mascarar, salvar, revelar, estatisticas
from src.ingestion.db import conectar, criar_schema, TABELAS_ESPERADAS
from src.ingestion.inbox import esca, sincronizar
from src.ui.comum import caminho_db, caminho_inbox, formatar_data, _dias_desde
from src.ui.dados import carregar_disputa, listar_candidatos, ROTULO_STATUS_CURACAO, ROTULO_GATE
from src.ui import cache


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
                    f"**#{row['id'])}** — {row['data_hora']} — **{row['acao']}** — **{row['ator'] or '—'}** — {row['entidade']} (id={row['entidade_id'] or '—'})"
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
            st.dataframe(
                candidatos_df[["nome_pseudonimo", "status_curacao"]],
                column_config={},
                hide_index=True,
            )
            st.caption("Freshness: >365 d = desatualizado; >180 d = atenção (padrão).")
        else:
            st.caption("Nenhum candidato cadastrado.")

        # --- preset ---
        st.markdown("#### Preset")
        preset_info = st.expander("Informações do preset atual")
        with preset_info:
            disputa = carregar_disputa(con)
            if disputa:
                st.write(f"Disputa: {disputa.get('nome_ficticio')}")
                st.write(f"Preset versão: {disputa.get('preset_versao_id')}")
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
            st.capning("Nenhum arquivo .docx encontrado na pasta inbox.")
        else:
            # resumo
            status_counts = {}
            for s in scan:
                status_counts[s["status"]] = status_counts.get(s["status"], 0) + 1

            cols = st.columns(min(3, len(status_counts)))
            for i, (st_name, count) in enumerate(status_counts.items()):
                with cols[i % len(cols)]:
                    st.metric(status_name := st_name, count)

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

    # rodapé pequeno
    st.divider()
    st.caption("Todos os dados são fictícios. PII nunca é exibida — sempre `***`.")