# Editor de preset para a Fase 5 do Bid Room.
# Permite criar uma nova versão do preset, ajustar pesos por categoria,
# gates e piso de confiança. O save publica uma nova versão e recalcula
# as pontuações da disputa ativa.

from __future__ import annotations

import copy
import json

import pandas as pd
import streamlit as st

from src.scoring.gates import NIVEIS_PORTUGUES_PROFISSIONAL
from src.scoring.preset import carregar_versao, listar_versoes
from src.scoring.servico import publicar_nova_versao, rollback_preset
from src.ui import cache, dados
from src.ui.comum import cabecalho, caminho_db

PAPEIS = ["pm_scrum", "ba_bsa", "java", "bi_data"]
NOMES = {
    "pm_scrum": "PM/Scrum Sênior",
    "ba_bsa": "BA/BSA Sênior",
    "java": "Java Developer Sênior",
    "bi_data": "BI/Data Analyst Sênior",
}
CATEGORIAS = [
    "aderencia", "dominio_energia", "certificacoes", "experiencia",
    "idioma", "educacao", "ferramentas",
]
GATES = {
    "G1": {
        "nome": "Base técnica mínima",
        "regra": "Pelo menos 3 das 5 competências core, com 2 em N2+.",
    },
    "G2": {
        "nome": "Experiência mínima",
        "regra": "Pelo menos 5 anos de experiência total (proxy; não é tempo no papel).",
    },
    "G3": {
        "nome": "Português profissional",
        "regra": "Aceita níveis profissionais; se não informado, vai para revisão.",
    },
    "G4": {
        "nome": "Elegibilidade Brasil",
        "regra": "Só elimina se o currículo disser explicitamente 'não'.",
    },
    "G5": {
        "nome": "Disponibilidade",
        "regra": "A base não tem disponibilidade; sinaliza revisão.",
    },
    "G6": {
        "nome": "Core sem N0",
        "regra": "Nenhuma core em N0; pelo menos 2 em N2+.",
    },
}


def _reset_draft(current):
    st.session_state["t5_draft"] = copy.deepcopy(current.pesos)
    st.session_state["t5_gates"] = copy.deepcopy(current.gates)
    st.session_state["t5_base_version"] = current.versao_preset_id

    prefixos = (
        "t5_peso_", "t5_gate_",
        "t5_g1_", "t5_g2_", "t5_g3_", "t5_g6_",
    )
    for chave in list(st.session_state):
        if chave.startswith(prefixos) or chave == "t5_piso":
            del st.session_state[chave]


def _slider(label, key, default, help=None):
    if key not in st.session_state:
        st.session_state[key] = int(default)
    return st.slider(label, 0, 100, key=key, help=help)


def _number(label, key, default, minimo, maximo):
    if key not in st.session_state:
        st.session_state[key] = int(default)
    return st.number_input(label, min_value=minimo, max_value=maximo, key=key)


def _check(label, key, default):
    if key not in st.session_state:
        st.session_state[key] = bool(default)
    return st.checkbox(label, key=key)


def _multi(label, key, default, options):
    if key not in st.session_state:
        st.session_state[key] = list(default)
    return st.multiselect(label, options, key=key)


def _soma(pesos_papel):
    return sum(int(pesos_papel[c]) for c in CATEGORIAS)


def render(con) -> None:
    disputa = dados.carregar_disputa(con)
    if not disputa or not disputa.get("preset_versao_id"):
        st.warning("Nenhuma disputa com preset publicado.")
        return

    current = carregar_versao(con, int(disputa["preset_versao_id"]))
    if st.session_state.get("t5_base_version") != current.versao_preset_id:
        _reset_draft(current)

    draft = st.session_state["t5_draft"]
    gates = st.session_state["t5_gates"]

    cabecalho(
        "Editor de preset",
        f"Versão ativa: v{current.numero} · {current.status} · "
        f"preset {current.preset_id}",
    )

    flash = st.session_state.pop("t5_flash", None)
    if flash:
        st.success(flash)

    papel = st.radio(
        "Papel",
        PAPEIS,
        format_func=lambda p: NOMES[p],
        horizontal=True,
        key="t5_papel",
    )

    st.subheader(f"Pesos — {NOMES[papel]}")
    cols = st.columns(4)
    for i, categoria in enumerate(CATEGORIAS):
        chave = f"t5_peso_{papel}_{categoria}"
        with cols[i % 4]:
            draft["categorias"][papel][categoria] = _slider(
                categoria,
                chave,
                draft["categorias"][papel][categoria],
            )

    soma = _soma(draft["categorias"][papel])
    if soma == 100:
        st.success(f"Soma do papel: {soma}/100")
    else:
        st.error(f"Soma do papel: {soma}/100 — ajuste antes de salvar ou visualizar.")

    st.subheader("Piso de confiança")
    if "t5_piso" not in st.session_state:
        st.session_state["t5_piso"] = int(
            round(float(draft.get("piso_confianca", 0.6)) * 100)
        )
    draft["piso_confianca"] = st.slider(
        "Piso (%)",
        0,
        100,
        key="t5_piso",
        help="Nota mínima de confiança; gravada como fração 0.0–1.0.",
    ) / 100.0

    st.subheader("Gates")
    for gate, meta in GATES.items():
        cfg = gates.setdefault(gate, {})
        cfg["ativo"] = _check(
            f"{gate} — {meta['nome']}",
            f"t5_gate_{gate}",
            cfg.get("ativo", True),
        )
        st.caption(meta["regra"])

        with st.expander("Parâmetros"):
            if gate == "G1":
                cfg["min_core"] = _number(
                    "Mínimo de core", "t5_g1_min_core",
                    cfg.get("min_core", 3), 1, 5,
                )
                cfg["min_core_n2"] = _number(
                    "Mínimo em N2+", "t5_g1_min_n2",
                    cfg.get("min_core_n2", 2), 1, 5,
                )
            elif gate == "G2":
                anos = cfg.setdefault("anos_minimos", {})
                anos[papel] = _number(
                    f"Anos mínimos — {NOMES[papel]}",
                    f"t5_g2_{papel}",
                    anos.get(papel, 5),
                    0,
                    30,
                )
                st.caption(
                    "Hoje o motor usa o tempo total de experiência, "
                    "não o tempo no papel. Os outros papéis mantêm o valor atual."
                )
            elif gate == "G3":
                opcoes = sorted(
                    set(NIVEIS_PORTUGUES_PROFISSIONAL)
                    | set(cfg.get("niveis_aceitos", []))
                )
                cfg["niveis_aceitos"] = _multi(
                    "Níveis aceitos",
                    "t5_g3_niveis",
                    cfg.get("niveis_aceitos", opcoes),
                    opcoes,
                )
            elif gate == "G6":
                cfg["min_core_n2"] = _number(
                    "Mínimo em N2+", "t5_g6_min_n2",
                    cfg.get("min_core_n2", 2), 1, 5,
                )
            elif gate in ("G4", "G5"):
                st.caption("Este gate não tem parâmetros; é só ligar/desligar.")

    somas = {p: _soma(draft["categorias"][p]) for p in PAPEIS}
    valido = all(s == 100 for s in somas.values())
    st.caption(
        "Somas: " + " · ".join(f"{NOMES[p]} {s}" for p, s in somas.items())
    )

    st.subheader("Preview — top 10")
    if not valido:
        st.info("Corrija as somas dos 4 papéis para ver o preview.")
    else:
        resultado = cache.preview_papel(
            str(caminho_db()),
            int(disputa["id"]),
            current.versao_preset_id,
            papel,
            json.dumps(draft, sort_keys=True),
            json.dumps(gates, sort_keys=True),
            10,
        )
        antes = resultado["resumo"]["antes"]
        depois = resultado["resumo"]["depois"]
        st.caption(
            f"Antes: {antes['elegivel']} elegíveis / {antes['revisar']} revisar / "
            f"{antes['eliminado']} eliminados · "
            f"Depois: {depois['elegivel']} / {depois['revisar']} / {depois['eliminado']}"
        )
        if resultado["resumo"]["entraram"]:
            st.caption("Entraram no top: " + ", ".join(resultado["resumo"]["entraram"]))
        if resultado["resumo"]["sairam"]:
            st.caption("Saíram do top: " + ", ".join(resultado["resumo"]["sairam"]))

        st.caption(f"Sem score na versão base: {resultado['resumo']['sem_score']}")
        st.dataframe(
            pd.DataFrame(resultado["linhas"]),
            hide_index=True,
            width="stretch",
            column_config={
                "score_atual": st.column_config.NumberColumn("Score atual", format="%.2f"),
                "score_novo": st.column_config.NumberColumn("Score novo", format="%.2f"),
                "delta": st.column_config.NumberColumn("Δ", format="%+.2f"),
            },
        )

    if st.button("Salvar e publicar", type="primary", disabled=not valido):
        try:
            res = publicar_nova_versao(
                con,
                int(disputa["id"]),
                current.preset_id,
                draft,
                gates,
                ator="ui",
            )
        except Exception as exc:
            st.error(f"Falha ao publicar: {exc}")
        else:
            cache.limpar()
            st.session_state["t5_flash"] = (
                f"Versão v{res['numero']} publicada; "
                f"{res['pontuacoes']} pontuações recalculadas."
            )
            st.session_state.pop("t5_base_version", None)
            st.rerun()

    with st.expander("Rollback"):
        versoes = [
            v for v in listar_versoes(con, current.preset_id)
            if v["id"] != current.versao_preset_id and v["status"] != "rascunho"
        ]
        if not versoes:
            st.caption("Nenhuma versão anterior disponível.")
        else:
            alvo = st.selectbox(
                "Versão",
                versoes,
                format_func=lambda v: (
                    f"v{v['numero']} · {v['status']} · {v['autor'] or '—'}"
                ),
            )
            confirmar = st.checkbox("Confirmo o rollback")
            if st.button("Fazer rollback", disabled=not confirmar):
                try:
                    res = rollback_preset(
                        con, int(disputa["id"]), int(alvo["id"]), ator="ui"
                    )
                except Exception as exc:
                    st.error(f"Falha no rollback: {exc}")
                else:
                    cache.limpar()
                    st.session_state["t5_flash"] = (
                        f"Rollback para v{res['numero']} concluído."
                    )
                    st.session_state.pop("t5_base_version", None)
                    st.rerun()