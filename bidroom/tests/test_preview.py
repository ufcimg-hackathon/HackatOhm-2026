"""Fase 5 — regressões do preview: diff com nome real, contagem total e novo."""
from __future__ import annotations

import json

import pytest

from scripts.build_db import GATES_ENERGIA_V1, PESOS_ENERGIA_V1, construir
from src.ingestion.db import conectar
from src.scoring import preview
from src.scoring.engine import ResultadoScore

PAPEL = "java"


@pytest.fixture()
def banco(tmp_path):
    db = tmp_path / "preview.db"
    construir(db)
    con = conectar(db)
    try:
        con.execute(
            "INSERT INTO disputas (nome_ficticio, preset_versao_id)"
            " VALUES ('Teste preview', 1)"
        )
        disputa_id = int(con.execute("SELECT id FROM disputas").fetchone()["id"])
        papel_id = int(
            con.execute(
                "SELECT id FROM papeis WHERE sigla = ?", (PAPEL,)
            ).fetchone()["id"]
        )
        yield con, disputa_id, papel_id
    finally:
        con.close()


def _candidato(
    con,
    disputa_id: int,
    papel_id: int,
    nome: str,
    *,
    base: tuple[float, str] | None = None,
) -> tuple[int, int]:
    cid = int(
        con.execute(
            "INSERT INTO candidatos (nome_pseudonimo, status_curacao)"
            " VALUES (?, 'curado')",
            (nome,),
        ).lastrowid
    )
    curriculo_id = int(
        con.execute(
            "INSERT INTO curriculos (candidato_id, nome_arquivo,"
            " caminho_cache, checksum, versao)"
            " VALUES (?, ?, 'x', ?, 1)",
            (cid, f"{nome}.docx", f"chk-{cid}"),
        ).lastrowid
    )
    if base is not None:
        score, status = base
        con.execute(
            "INSERT INTO pontuacoes (disputa_id, candidato_id, papel_id,"
            " versao_preset_id, curriculo_versao_id, pontuacao_ponderada,"
            " confianca, pontuacao_final, detalhe_json)"
            " VALUES (?, ?, ?, 1, ?, 50, 25, ?, ?)",
            (
                disputa_id,
                cid,
                papel_id,
                curriculo_id,
                score,
                json.dumps({"status_gates": status}),
            ),
        )
    return cid, curriculo_id


def _fake_pontuar(monkeypatch, scores: dict[int, tuple[float, str]]) -> None:
    def fake(ctx, perfil, pesos, gates_config=None):
        score, status = scores[ctx.candidato_id]
        return ResultadoScore(
            papel=perfil.sigla,
            pontuacao_ponderada=score,
            confianca=50.0,
            pontuacao_final=score,
            status_gates=status,
            gates=[],
            detalhe={"status_gates": status},
        )

    monkeypatch.setattr(preview, "pontuar", fake)


def test_preview_sairam_usa_nome_real(banco, monkeypatch):
    con, disputa_id, papel_id = banco
    ids = []
    scores = {}
    for i in range(11):
        cid, _cur = _candidato(
            con,
            disputa_id,
            papel_id,
            f"Candidato {i:02d}",
            base=(100 - i, "revisar"),
        )
        ids.append(cid)
        scores[cid] = (100 - i, "revisar")
    # O 1º cai para 0 e sai do top; o 11º sobe para 100 e entra.
    scores[ids[0]] = (0, "eliminado")
    scores[ids[10]] = (100, "revisar")
    con.commit()
    _fake_pontuar(monkeypatch, scores)

    resultado = preview.preview_papel(
        con, disputa_id, 1, PAPEL, PESOS_ENERGIA_V1, GATES_ENERGIA_V1, 10
    )

    assert resultado["resumo"]["sairam"] == ["Candidato 00"]
    assert resultado["resumo"]["entraram"] == ["Candidato 10"]
    assert "candidato_" not in " ".join(resultado["resumo"]["sairam"])


def test_preview_depois_conta_todos_os_avaliados(banco, monkeypatch):
    con, disputa_id, papel_id = banco
    scores = {}
    for i in range(12):
        cid, _cur = _candidato(
            con,
            disputa_id,
            papel_id,
            f"Candidato {i:02d}",
            base=(100 - i, "revisar"),
        )
        scores[cid] = (100 - i, "revisar")
    con.commit()
    _fake_pontuar(monkeypatch, scores)

    resultado = preview.preview_papel(
        con, disputa_id, 1, PAPEL, PESOS_ENERGIA_V1, GATES_ENERGIA_V1, 10
    )

    assert len(resultado["linhas"]) == 10
    assert resultado["resumo"]["depois"] == {
        "elegivel": 0,
        "revisar": 12,
        "eliminado": 0,
    }
    assert resultado["resumo"]["antes"] == {
        "elegivel": 0,
        "revisar": 12,
        "eliminado": 0,
    }


def test_preview_sem_score_inclui_candidato_novo(banco, monkeypatch):
    con, disputa_id, papel_id = banco
    antigo, _cur = _candidato(
        con, disputa_id, papel_id, "Candidato Antigo", base=(80, "revisar")
    )
    novo, _cur = _candidato(
        con, disputa_id, papel_id, "Candidato Novo", base=None
    )
    con.commit()
    _fake_pontuar(
        monkeypatch, {antigo: (80, "revisar"), novo: (90, "revisar")}
    )

    resultado = preview.preview_papel(
        con, disputa_id, 1, PAPEL, PESOS_ENERGIA_V1, GATES_ENERGIA_V1, 10
    )

    assert resultado["resumo"]["sem_score"] == 1
    linha = next(
        l for l in resultado["linhas"] if l["candidato_id"] == novo
    )
    assert linha["novo"] is True
    assert linha["score_atual"] is None
    assert linha["delta"] is None
    assert resultado["resumo"]["entraram"] == ["Candidato Novo"]