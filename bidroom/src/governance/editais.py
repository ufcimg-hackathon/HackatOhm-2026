"""Etapa 4 — serviço de editais, versões e revisão humana.

Regras:
- versão é imutável depois de ativa; retificação cria outra versão;
- requisito guarda trecho, localização, regra interpretada e ambiguidades;
- ativação exige revisão de todos os requisitos e bloqueia obrigatório
  rejeitado ou ambiguidade ainda pendente;
- critério de empresa/equipe não é convertido em ponto individual;
- a demanda anterior é preservada; a nova versão só aponta para a frente
  quando o responsável ativa.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from src.extraction.edital import EditalExtraido, RequisitoExtraido, extrair_edital
from src.governance.auditoria import registrar


def _agora() -> str:
    return datetime.now().replace(microsecond=0).isoformat(sep=" ")


def _json(valor: Any) -> str | None:
    return json.dumps(valor, ensure_ascii=False, sort_keys=True) if valor is not None else None


def _dict_json(texto: str | None) -> dict:
    if not texto:
        return {}
    try:
        valor = json.loads(texto)
        return valor if isinstance(valor, dict) else {}
    except (TypeError, ValueError):
        return {}


def _norm(texto: str) -> str:
    return (
        unicodedata.normalize("NFKD", texto)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
        .strip()
    )


def caminho_editais() -> Path:
    pasta = Path(os.environ.get("BIDROOM_EDITAIS", "data/editais"))
    pasta.mkdir(parents=True, exist_ok=True)
    return pasta


def _guardar(nome_arquivo: str, conteudo: bytes, checksum: str, subpasta: str) -> Path:
    nome = Path(nome_arquivo).name or "edital.docx"
    destino = caminho_editais() / subpasta / f"{checksum}_{nome}"
    destino.parent.mkdir(parents=True, exist_ok=True)
    if not destino.exists():
        destino.write_bytes(conteudo)
    return destino


def criar_edital(
    con,
    nome: str,
    *,
    cliente_ficticio: str | None = None,
    descricao: str | None = None,
    dono: str | None = None,
    ator: str = "ui",
) -> int:
    if not nome.strip():
        raise ValueError("nome do edital é obrigatório")
    cur = con.execute(
        "INSERT INTO editais (nome, cliente_ficticio, descricao, dono)"
        " VALUES (?, ?, ?, ?)",
        (nome.strip(), cliente_ficticio, descricao, dono),
    )
    edital_id = int(cur.lastrowid)
    registrar(
        con,
        acao="criar_edital",
        ator=ator,
        entidade="editais",
        entidade_id=edital_id,
        detalhes={"nome": nome},
        commit=False,
    )
    con.commit()
    return edital_id


def listar_editais(con) -> list[dict]:
    rows = con.execute(
        "SELECT e.*,"
        " (SELECT v.id FROM edital_versoes v WHERE v.edital_id = e.id"
        "  AND v.status = 'ativa' ORDER BY v.numero DESC LIMIT 1) AS versao_ativa_id,"
        " (SELECT MAX(v.numero) FROM edital_versoes v WHERE v.edital_id = e.id)"
        "  AS ultima_versao,"
        " (SELECT COUNT(*) FROM edital_versoes v WHERE v.edital_id = e.id)"
        "  AS total_versoes"
        " FROM editais e ORDER BY e.id DESC"
    ).fetchall()
    return [dict(row) for row in rows]


def obter_edital(con, edital_id: int) -> dict | None:
    row = con.execute("SELECT * FROM editais WHERE id = ?", (edital_id,)).fetchone()
    return dict(row) if row else None


def listar_versoes(con, edital_id: int) -> list[dict]:
    rows = con.execute(
        "SELECT v.*, e.nome AS edital_nome,"
        " (SELECT COUNT(*) FROM edital_requisitos r"
        "  WHERE r.edital_versao_id = v.id) AS total_requisitos"
        " FROM edital_versoes v JOIN editais e ON e.id = v.edital_id"
        " WHERE v.edital_id = ? ORDER BY v.numero DESC, v.id DESC",
        (edital_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def obter_versao(con, versao_id: int) -> dict | None:
    row = con.execute(
        "SELECT v.*, e.nome AS edital_nome, e.cliente_ficticio"
        " FROM edital_versoes v JOIN editais e ON e.id = v.edital_id"
        " WHERE v.id = ?",
        (versao_id,),
    ).fetchone()
    return dict(row) if row else None


def listar_requisitos(
    con,
    versao_id: int,
    *,
    status: str | None = None,
    tipo: str | None = None,
    escopo: str | None = None,
) -> list[dict]:
    where = ["edital_versao_id = ?"]
    params: list[Any] = [versao_id]
    if status:
        where.append("status = ?")
        params.append(status)
    if tipo:
        where.append("tipo = ?")
        params.append(tipo)
    if escopo:
        where.append("escopo = ?")
        params.append(escopo)
    rows = con.execute(
        "SELECT * FROM edital_requisitos WHERE "
        + " AND ".join(where)
        + " ORDER BY id",
        params,
    ).fetchall()
    resultado: list[dict] = []
    for row in rows:
        item = dict(row)
        item["regra"] = _dict_json(row["regra_json"])
        item["regra_original"] = _dict_json(row["regra_original_json"])
        resultado.append(item)
    return resultado


def _inserir_requisito(con, versao_id: int, req: RequisitoExtraido) -> int:
    cur = con.execute(
        "INSERT INTO edital_requisitos (edital_versao_id, tipo, escopo, chave,"
        " descricao, regra_json, trecho, localizacao, pagina, obrigatorio,"
        " ambiguidade, ambiguidade_motivo, status)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendente')",
        (
            versao_id,
            req.tipo,
            req.escopo,
            req.chave,
            req.descricao,
            _json(req.regra),
            req.trecho,
            req.localizacao,
            req.pagina,
            1 if req.obrigatorio else 0,
            1 if req.ambiguidade else 0,
            req.ambiguidade_motivo,
        ),
    )
    return int(cur.lastrowid)


def _requisito_tratamento(texto: str = "") -> RequisitoExtraido:
    return RequisitoExtraido(
        tipo="tratamento_documento",
        escopo="individual",
        chave="documento_sem_requisitos",
        descricao=(
            "Nenhum requisito foi reconhecido automaticamente; revisar o "
            "documento manualmente antes de ativar."
        ),
        regra={"tratamento": "revisao_manual", "texto": texto[:500]},
        trecho=texto[:500] or "(documento sem requisitos reconhecidos)",
        localizacao="documento",
        pagina=1,
        obrigatorio=True,
        ambiguidade=True,
        ambiguidade_motivo="extração não reconheceu requisitos",
    )


def _chave_requisito(row) -> tuple:
    """Agrupa requisitos do mesmo tipo/escopo para comparar versões.

    A chave detalhada (por exemplo, a lista de competências) muda quando o
    conteúdo muda; para o diff interessa parear os requisitos e mostrar
    ``antes/depois`` em vez de tratar tudo como remoção + inclusão.
    """
    chave = row["chave"] or ""
    prefixo = chave.split(":", 1)[0] if ":" in chave else chave
    return (row["tipo"], row["escopo"], prefixo)


def comparar_versoes(con, versao_a: int, versao_b: int) -> dict:
    """Diferença explícita entre duas versões, sem alterar a antiga."""
    if versao_a == versao_b:
        return {"adicionados": [], "removidos": [], "alterados": []}
    a = listar_requisitos(con, versao_a)
    b = listar_requisitos(con, versao_b)
    grupos_a: dict[tuple, list[dict]] = {}
    grupos_b: dict[tuple, list[dict]] = {}
    for item in a:
        grupos_a.setdefault(_chave_requisito(item), []).append(item)
    for item in b:
        grupos_b.setdefault(_chave_requisito(item), []).append(item)
    adicionados: list[dict] = []
    removidos: list[dict] = []
    alterados: list[dict] = []
    for chave in set(grupos_a) | set(grupos_b):
        antes_grupo = grupos_a.get(chave, [])
        depois_grupo = grupos_b.get(chave, [])
        pares = min(len(antes_grupo), len(depois_grupo))
        for indice in range(pares):
            antes = antes_grupo[indice]
            depois = depois_grupo[indice]
            if (
                _json(antes["regra"]) != _json(depois["regra"])
                or antes["descricao"] != depois["descricao"]
            ):
                alterados.append(
                    {
                        "tipo": depois["tipo"],
                        "escopo": depois["escopo"],
                        "antes": antes["descricao"],
                        "depois": depois["descricao"],
                    }
                )
        for item in depois_grupo[pares:]:
            adicionados.append(
                {
                    "tipo": item["tipo"],
                    "escopo": item["escopo"],
                    "descricao": item["descricao"],
                }
            )
        for item in antes_grupo[pares:]:
            removidos.append(
                {
                    "tipo": item["tipo"],
                    "escopo": item["escopo"],
                    "descricao": item["descricao"],
                }
            )
    return {
        "adicionados": adicionados,
        "removidos": removidos,
        "alterados": alterados,
    }


def receber_versao(
    con,
    edital_id: int,
    *,
    nome_arquivo: str,
    conteudo: bytes,
    ator: str = "ui",
    tipo: str = "original",
    motivo: str | None = None,
) -> dict:
    """Recebe `.docx`, cria versão e extrai requisitos com rastreabilidade."""
    edital = obter_edital(con, edital_id)
    if edital is None:
        raise KeyError("edital não encontrado")
    if Path(nome_arquivo).suffix.lower() != ".docx":
        raise ValueError("edital deve ser .docx no MVP")
    if not conteudo:
        raise ValueError("arquivo de edital vazio")
    if tipo not in ("original", "retificacao"):
        raise ValueError("tipo de versão inválido")
    checksum = hashlib.sha256(conteudo).hexdigest()
    existente = con.execute(
        "SELECT * FROM edital_versoes WHERE edital_id = ? AND checksum = ?",
        (edital_id, checksum),
    ).fetchone()
    if existente is not None:
        return {
            "versao_id": int(existente["id"]),
            "duplicado": True,
            "status": existente["status"],
            "requisitos": con.execute(
                "SELECT COUNT(*) c FROM edital_requisitos"
                " WHERE edital_versao_id = ?",
                (existente["id"],),
            ).fetchone()["c"],
            "efeito": _dict_json(existente["efeito_json"]),
        }

    ultima = con.execute(
        "SELECT * FROM edital_versoes WHERE edital_id = ?"
        " ORDER BY numero DESC, id DESC LIMIT 1",
        (edital_id,),
    ).fetchone()
    if tipo == "original" and ultima is not None:
        raise ValueError("já existe versão; envie como retificação")
    if tipo == "retificacao" and ultima is None:
        raise ValueError("retificação exige uma versão anterior")
    numero = 1 if ultima is None else int(ultima["numero"]) + 1
    anterior_id = int(ultima["id"]) if ultima is not None else None

    destino = _guardar(nome_arquivo, conteudo, checksum, str(edital_id))
    cur = con.execute(
        "INSERT INTO edital_versoes (edital_id, numero, tipo, versao_anterior_id,"
        " nome_arquivo, caminho, checksum, status, observacao, autor)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 'recebida', ?, ?)",
        (
            edital_id,
            numero,
            tipo,
            anterior_id,
            Path(nome_arquivo).name,
            str(destino),
            checksum,
            motivo,
            ator,
        ),
    )
    versao_id = int(cur.lastrowid)
    try:
        extraido: EditalExtraido = extrair_edital(destino)
    except Exception as exc:  # noqa: BLE001 - erro fica visível na versão
        con.execute(
            "UPDATE edital_versoes SET status = 'erro', observacao = ? WHERE id = ?",
            (f"falha na extração: {exc}", versao_id),
        )
        registrar(
            con,
            acao="erro_extracao_edital",
            ator=ator,
            entidade="edital_versoes",
            entidade_id=versao_id,
            detalhes={"erro": str(exc)},
            commit=False,
        )
        con.commit()
        raise ValueError(f"falha ao ler edital: {exc}") from exc

    requisitos = list(extraido.requisitos)
    if not requisitos:
        requisitos = [_requisito_tratamento(extraido.texto)]
    for req in requisitos:
        _inserir_requisito(con, versao_id, req)

    efeito = (
        comparar_versoes(con, anterior_id, versao_id)
        if anterior_id is not None
        else {"adicionados": [], "removidos": [], "alterados": []}
    )
    con.execute(
        "UPDATE edital_versoes SET texto_extraido = ?, n_paginas = ?,"
        " status = 'extraida', efeito_json = ?, atualizado_em = datetime('now')"
        " WHERE id = ?",
        (
            extraido.texto,
            extraido.n_paginas,
            _json(efeito),
            versao_id,
        ),
    )
    registrar(
        con,
        acao="receber_edital_versao",
        ator=ator,
        entidade="edital_versoes",
        entidade_id=versao_id,
        detalhes={
            "edital_id": edital_id,
            "numero": numero,
            "tipo": tipo,
            "requisitos": len(requisitos),
            "alertas": extraido.alertas,
            "efeito": efeito,
        },
        commit=False,
    )
    con.commit()
    return {
        "versao_id": versao_id,
        "duplicado": False,
        "status": "extraida",
        "requisitos": len(requisitos),
        "efeito": efeito,
        "alertas": extraido.alertas,
    }


def iniciar_revisao(con, versao_id: int, ator: str = "ui") -> str:
    versao = obter_versao(con, versao_id)
    if versao is None:
        raise KeyError("versão não encontrada")
    if versao["status"] in ("recebida", "ativa", "substituida", "erro"):
        raise ValueError(f"versão {versao['status']} não pode entrar em revisão")
    con.execute(
        "UPDATE edital_versoes SET status = 'em_revisao',"
        " atualizado_em = datetime('now') WHERE id = ?",
        (versao_id,),
    )
    registrar(
        con,
        acao="iniciar_revisao_edital",
        ator=ator,
        entidade="edital_versoes",
        entidade_id=versao_id,
        commit=False,
    )
    con.commit()
    return "em_revisao"


def status_revisao(con, versao_id: int) -> dict:
    requisitos = listar_requisitos(con, versao_id)
    contagem = {
        "total": len(requisitos),
        "pendentes": 0,
        "aprovados": 0,
        "alterados": 0,
        "rejeitados": 0,
        "ambiguos_pendentes": 0,
        "obrigatorios_pendentes": 0,
        "obrigatorios_rejeitados": 0,
    }
    for req in requisitos:
        status = req["status"]
        if status == "pendente":
            contagem["pendentes"] += 1
        elif status == "aprovado":
            contagem["aprovados"] += 1
        elif status == "alterado":
            contagem["alterados"] += 1
        elif status == "rejeitado":
            contagem["rejeitados"] += 1
        if req["ambiguidade"] and status == "pendente":
            contagem["ambiguos_pendentes"] += 1
        if req["obrigatorio"] and status == "pendente":
            contagem["obrigatorios_pendentes"] += 1
        if req["obrigatorio"] and status == "rejeitado":
            contagem["obrigatorios_rejeitados"] += 1
    return contagem


def revisar_requisito(
    con,
    requisito_id: int,
    *,
    revisor: str,
    decisao: str,
    descricao: str | None = None,
    regra: dict | None = None,
    observacao: str | None = None,
    ator: str = "ui",
) -> dict:
    if not revisor.strip():
        raise ValueError("revisor é obrigatório")
    if decisao not in ("aprovar", "alterar", "rejeitar"):
        raise ValueError("decisão deve ser aprovar, alterar ou rejeitar")
    if decisao == "rejeitar" and not (observacao or "").strip():
        raise ValueError("rejeição exige observação")
    if decisao == "alterar" and descricao is None and regra is None:
        raise ValueError("alteração exige nova descrição ou regra")
    if regra is not None and not isinstance(regra, dict):
        raise ValueError("regra interpretada deve ser um objeto JSON")
    row = con.execute(
        "SELECT r.*, v.status AS versao_status FROM edital_requisitos r"
        " JOIN edital_versoes v ON v.id = r.edital_versao_id WHERE r.id = ?",
        (requisito_id,),
    ).fetchone()
    if row is None:
        raise KeyError("requisito não encontrado")
    if row["versao_status"] in ("recebida", "erro", "ativa", "substituida"):
        raise ValueError(
            f"versão {row['versao_status']} não pode ser revisada"
        )
    novo_status = {
        "aprovar": "aprovado",
        "alterar": "alterado",
        "rejeitar": "rejeitado",
    }[decisao]
    nova_descricao = row["descricao"]
    nova_regra_json = row["regra_json"]
    descricao_original = row["descricao_original"]
    regra_original_json = row["regra_original_json"]
    if decisao == "alterar":
        if descricao_original is None:
            descricao_original = row["descricao"]
        if regra_original_json is None:
            regra_original_json = row["regra_json"]
        if descricao is not None:
            nova_descricao = descricao.strip() or row["descricao"]
        if regra is not None:
            nova_regra_json = _json(regra)
    con.execute(
        "UPDATE edital_requisitos SET descricao = ?, descricao_original = ?,"
        " regra_json = ?, regra_original_json = ?, status = ?, revisor = ?,"
        " revisado_em = datetime('now'), observacao_revisao = ?,"
        " ambiguidade = 0, ambiguidade_motivo = NULL,"
        " atualizado_em = datetime('now') WHERE id = ?",
        (
            nova_descricao,
            descricao_original,
            nova_regra_json,
            regra_original_json,
            novo_status,
            revisor,
            observacao,
            requisito_id,
        ),
    )
    con.execute(
        "UPDATE edital_versoes SET status = 'em_revisao',"
        " atualizado_em = datetime('now') WHERE id = ?"
        " AND status IN ('extraida','aprovada')",
        (row["edital_versao_id"],),
    )
    registrar(
        con,
        acao="revisar_requisito_edital",
        ator=ator,
        entidade="edital_requisitos",
        entidade_id=requisito_id,
        detalhes={
            "decisao": decisao,
            "revisor": revisor,
            "observacao": observacao,
            "regra": regra,
        },
        commit=False,
    )
    con.commit()
    return {
        "requisito_id": requisito_id,
        "status": novo_status,
        "versao_id": int(row["edital_versao_id"]),
    }


def aprovar_versao(con, versao_id: int, *, revisor: str, ator: str = "ui") -> dict:
    if not revisor.strip():
        raise ValueError("revisor é obrigatório")
    versao = obter_versao(con, versao_id)
    if versao is None:
        raise KeyError("versão não encontrada")
    if versao["status"] in ("ativa", "substituida"):
        return {"versao_id": versao_id, "status": versao["status"], "ja_aprovada": True}
    if versao["status"] in ("recebida", "erro"):
        raise ValueError(
            f"versão {versao['status']} não foi extraída corretamente; "
            "reenvie o documento ou trate o erro"
        )
    requisitos = listar_requisitos(con, versao_id)
    if not requisitos:
        raise ValueError("versão sem requisitos; revise o documento")
    pendentes = [r for r in requisitos if r["status"] == "pendente"]
    if pendentes:
        raise ValueError(
            f"{len(pendentes)} requisito(s) ainda pendente(s) de revisão"
        )
    ambiguos = [r for r in requisitos if r["ambiguidade"]]
    if ambiguos:
        raise ValueError(
            f"{len(ambiguos)} requisito(s) com ambiguidade não resolvida"
        )
    rejeitados = [
        r for r in requisitos if r["obrigatorio"] and r["status"] == "rejeitado"
    ]
    if rejeitados:
        raise ValueError(
            f"{len(rejeitados)} requisito(s) obrigatório(s) rejeitado(s);"
            " ajuste a interpretação ou o documento"
        )
    con.execute(
        "UPDATE edital_versoes SET status = 'aprovada',"
        " atualizado_em = datetime('now') WHERE id = ?",
        (versao_id,),
    )
    registrar(
        con,
        acao="aprovar_versao_edital",
        ator=ator,
        entidade="edital_versoes",
        entidade_id=versao_id,
        detalhes={"revisor": revisor, "requisitos": len(requisitos)},
        commit=False,
    )
    con.commit()
    return {"versao_id": versao_id, "status": "aprovada"}


def avaliar_cobertura(con, versao_id: int) -> dict:
    requisitos = listar_requisitos(con, versao_id)
    papeis = [r for r in requisitos if r["tipo"] == "papel"]
    vagas = 0
    for req in papeis:
        valor = req["regra"].get("vagas")
        if isinstance(valor, int):
            vagas += valor
    for req in requisitos:
        if req["tipo"] == "vaga":
            valor = req["regra"].get("quantidade")
            if isinstance(valor, int):
                vagas += valor
    competencias = [r for r in requisitos if r["tipo"] == "competencia"]
    experiencia = [r for r in requisitos if r["tipo"] == "experiencia_minima"]
    certificacoes = [r for r in requisitos if r["tipo"] == "certificacao"]
    comprovantes = [r for r in requisitos if r["tipo"] == "comprovante"]
    criterios = [r for r in requisitos if r["tipo"] == "criterio_eliminatorio"]
    pontuacao = [r for r in requisitos if r["tipo"] == "pontuacao"]
    return {
        "papeis": len(papeis),
        "vagas": vagas,
        "competencias": len(competencias),
        "experiencia_minima": len(experiencia),
        "certificacoes": len(certificacoes),
        "comprovantes": len(comprovantes),
        "criterios_eliminatorios": len(criterios),
        "pontuacao": len(pontuacao),
        "empresa": sum(1 for r in requisitos if r["escopo"] == "empresa"),
        "equipe": sum(1 for r in requisitos if r["escopo"] == "equipe"),
        "suficiente": bool(
            papeis
            and vagas > 0
            and (competencias or experiencia or certificacoes)
        ),
    }


def _marcar_disputas_para_nova_versao(
    con,
    *,
    anterior_id: int,
    nova_id: int,
    efeito: dict,
    ator: str,
) -> list[int]:
    disputas = [
        int(row["id"])
        for row in con.execute(
            "SELECT id FROM disputas WHERE edital_versao_id = ? AND status = 'aberta'",
            (anterior_id,),
        )
    ]
    for disputa_id in disputas:
        existente = con.execute(
            "SELECT id FROM atualizacoes_pendentes WHERE disputa_id = ?"
            " AND tipo = 'edital_retificado' AND referencia_id = ?"
            " AND status = 'pendente' LIMIT 1",
            (disputa_id, nova_id),
        ).fetchone()
        if existente is not None:
            continue
        con.execute(
            "INSERT INTO atualizacoes_pendentes (disputa_id, candidato_id, tipo,"
            " referencia_tipo, referencia_id, motivo, detalhes_json)"
            " VALUES (?, NULL, 'edital_retificado', 'edital_versao', ?, ?, ?)",
            (
                disputa_id,
                nova_id,
                "Nova versão do edital ativada; revisar a demanda anterior.",
                _json(efeito),
            ),
        )
    return disputas


def ativar_versao(
    con,
    versao_id: int,
    *,
    ator: str = "ui",
    criar_disputa: bool = True,
) -> dict:
    versao = obter_versao(con, versao_id)
    if versao is None:
        raise KeyError("versão não encontrada")
    if versao["status"] == "ativa":
        return {
            "versao_id": versao_id,
            "status": "ativa",
            "disputas": [],
            "ja_ativa": True,
        }
    if versao["status"] != "aprovada":
        raise ValueError("versão precisa ser aprovada antes da ativação")
    anterior = con.execute(
        "SELECT id, numero, efeito_json FROM edital_versoes"
        " WHERE edital_id = ? AND status = 'ativa' AND id != ?"
        " ORDER BY numero DESC LIMIT 1",
        (versao["edital_id"], versao_id),
    ).fetchone()
    efeito = _dict_json(versao["efeito_json"])
    disputas: list[int] = []
    if anterior is not None:
        con.execute(
            "UPDATE edital_versoes SET status = 'substituida',"
            " atualizado_em = datetime('now') WHERE id = ?",
            (anterior["id"],),
        )
        disputas = _marcar_disputas_para_nova_versao(
            con,
            anterior_id=int(anterior["id"]),
            nova_id=versao_id,
            efeito=efeito,
            ator=ator,
        )
    con.execute(
        "UPDATE edital_versoes SET status = 'ativa',"
        " atualizado_em = datetime('now') WHERE id = ?",
        (versao_id,),
    )
    con.execute(
        "UPDATE editais SET status = 'ativo', atualizado_em = datetime('now')"
        " WHERE id = ?",
        (versao["edital_id"],),
    )
    for disputa_id in disputas:
        con.execute(
            "UPDATE disputas SET edital_versao_id = ? WHERE id = ?",
            (versao_id, disputa_id),
        )
    disputa_criada = None
    if criar_disputa:
        existente = con.execute(
            "SELECT id FROM disputas WHERE edital_versao_id = ?"
            " ORDER BY id DESC LIMIT 1",
            (versao_id,),
        ).fetchone()
        if existente is None:
            preset = con.execute(
                "SELECT id FROM versoes_preset WHERE status = 'publicada'"
                " ORDER BY numero DESC, id DESC LIMIT 1"
            ).fetchone()
            cur = con.execute(
                "INSERT INTO disputas (nome_ficticio, cliente_ficticio,"
                " preset_versao_id, edital_versao_id, status)"
                " VALUES (?, ?, ?, ?, 'aberta')",
                (
                    versao["edital_nome"],
                    versao.get("cliente_ficticio"),
                    int(preset["id"]) if preset is not None else None,
                    versao_id,
                ),
            )
            disputa_criada = int(cur.lastrowid)
            disputas.append(disputa_criada)
    registrar(
        con,
        acao="ativar_edital_versao",
        ator=ator,
        entidade="edital_versoes",
        entidade_id=versao_id,
        detalhes={
            "edital_id": versao["edital_id"],
            "anterior_id": int(anterior["id"]) if anterior is not None else None,
            "disputas": disputas,
            "disputa_criada": disputa_criada,
            "efeito": efeito,
        },
        commit=False,
    )
    con.commit()
    return {
        "versao_id": versao_id,
        "status": "ativa",
        "disputas": disputas,
        "disputa_criada": disputa_criada,
        "anterior_id": int(anterior["id"]) if anterior is not None else None,
        "efeito": efeito,
    }


def anexar_anexo(
    con,
    versao_id: int,
    *,
    nome_arquivo: str,
    conteudo: bytes,
    tipo: str | None = None,
    ator: str = "ui",
) -> dict:
    if obter_versao(con, versao_id) is None:
        raise KeyError("versão não encontrada")
    if Path(nome_arquivo).suffix.lower() != ".docx":
        raise ValueError("anexo deve ser .docx no MVP")
    if not conteudo:
        raise ValueError("anexo vazio")
    checksum = hashlib.sha256(conteudo).hexdigest()
    existente = con.execute(
        "SELECT * FROM edital_anexos WHERE edital_versao_id = ? AND checksum = ?",
        (versao_id, checksum),
    ).fetchone()
    if existente is not None:
        return {"anexo_id": int(existente["id"]), "duplicado": True}
    destino = _guardar(nome_arquivo, conteudo, checksum, f"anexos/{versao_id}")
    cur = con.execute(
        "INSERT INTO edital_anexos (edital_versao_id, nome_arquivo, caminho,"
        " checksum, tipo) VALUES (?, ?, ?, ?, ?)",
        (versao_id, Path(nome_arquivo).name, str(destino), checksum, tipo),
    )
    anexo_id = int(cur.lastrowid)
    registrar(
        con,
        acao="anexar_edital",
        ator=ator,
        entidade="edital_anexos",
        entidade_id=anexo_id,
        detalhes={"versao_id": versao_id, "nome": nome_arquivo},
        commit=False,
    )
    con.commit()
    return {"anexo_id": anexo_id, "duplicado": False}


def listar_anexos(con, versao_id: int) -> list[dict]:
    rows = con.execute(
        "SELECT * FROM edital_anexos WHERE edital_versao_id = ? ORDER BY id",
        (versao_id,),
    ).fetchall()
    return [dict(row) for row in rows]
