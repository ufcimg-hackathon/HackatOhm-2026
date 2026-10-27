"""Fase 6 — sincronização da pasta inbox.

Responsabilidades:
- Escanear `data/inbox/` (ou pasta definida por BIDROOM_INBOX) em busca de .docx.
- Para cada arquivo: calcular checksum, verificar se já foi processado,
  inserir/atualizar `inbox_arquivos`.
- Chamar o pipeline `ingerir_registro` para novos arquivos.
- Registrar auditoria (`ingerir_curriculo` / `atualizar_curriculo` / erro).
- Após sucesso, recalcular o candidato na disputa ativa e invalidar cache.
- Devolver resumo com processados, erros, duplicados e versões atualizadas.

O arquivo original nunca é apagado: erro fica visível e pode ser reprocessado.
"""

from __future__ import annotations

from pathlib import Path

from src.extraction.parser_docx import checksum_arquivo
from src.governance.auditoria import registrar
from src.governance.vault import CofrePII
from src.ingestion.db import conectar, criar_schema
from src.ingestion.pipeline import ingerir_registro
from src.scoring.servico import recalcular_candidato
from src.ui.comum import caminho_inbox


def _arquivos_inbox(pasta: Path) -> list[Path]:
    """Retorna .docx da pasta, ignorando arquivos temporários do Word."""
    if not pasta.exists():
        return []
    return sorted(
        f
        for f in pasta.iterdir()
        if f.is_file()
        and f.suffix.lower() == ".docx"
        and not f.name.startswith("~$")
    )


def _checksum_e_nome(arquivo: Path) -> tuple[str, str]:
    return checksum_arquivo(arquivo), arquivo.name


def _registros_ja_processados(con, checksums: set[str]) -> dict[str, dict]:
    if not checksums:
        return {}
    rows = con.execute(
        "SELECT checksum, status, candidato_id, curriculo_id, versao, erro"
        " FROM inbox_arquivos WHERE checksum IN ({})".format(
            ",".join("?" * len(checksums))
        ),
        list(checksums),
    ).fetchall()
    return {row["checksum"]: dict(row) for row in rows}


def _linha_inbox(con, checksum: str, nome: str, caminho: Path) -> int:
    """Garante uma linha em inbox_arquivos e devolve seu id."""
    row = con.execute(
        "SELECT id FROM inbox_arquivos WHERE checksum = ?", (checksum,)
    ).fetchone()
    if row:
        return int(row["id"])
    cur = con.execute(
        "INSERT INTO inbox_arquivos (nome_arquivo, caminho, checksum, status)"
        " VALUES (?, ?, ?, 'pendente')",
        (nome, str(caminho), checksum),
    )
    con.commit()
    return int(cur.lastrowid)


def escanear(con, pasta: Path) -> list[dict]:
    """Escaneia a pasta e retorna o estado atual de cada arquivo.

    Cada item contém: arquivo, checksum, nome, status, candidato_id,
    curriculo_id, versao e erro. O status vem de `inbox_arquivos`; quando a
    linha ainda não existe, procuramos o checksum em `curriculos` para marcar
    duplicado.
    """
    arquivos = _arquivos_inbox(pasta)
    checksums = {_checksum_e_nome(f)[0] for f in arquivos}
    existentes = _registros_ja_processados(con, checksums)

    resumo: list[dict] = []
    for arquivo in arquivos:
        checksum, nome = _checksum_e_nome(arquivo)
        row = existentes.get(checksum)
        if row:
            status = row["status"]
            candidato_id = row.get("candidato_id")
            curriculo_id = row.get("curriculo_id")
            versao = int(row.get("versao") or 1)
            erro = row.get("erro")
        else:
            existente = con.execute(
                "SELECT id, candidato_id, versao FROM curriculos WHERE checksum = ?",
                (checksum,),
            ).fetchone()
            if existente:
                status = "duplicado"
                candidato_id = existente["candidato_id"]
                curriculo_id = existente["id"]
                versao = int(existente["versao"])
                erro = None
            else:
                status = "pendente"
                candidato_id = None
                curriculo_id = None
                versao = 1
                erro = None
        resumo.append(
            {
                "arquivo": arquivo,
                "checksum": checksum,
                "nome": nome,
                "status": status,
                "candidato_id": candidato_id,
                "curriculo_id": curriculo_id,
                "versao": versao,
                "erro": erro,
            }
        )
    return resumo


# Compatibilidade com chamadas antigas da UI/testes.
esca = escanear


def sincronizar(
    con,
    cofre: CofrePII,
    pasta: Path | None = None,
    ator: str = "inbox",
    retry_erros: bool = True,
) -> dict:
    """Sincroniza a pasta inbox com o banco.

    - `processado` não é reprocessado (idempotência por checksum).
    - `erro` é tentado novamente por padrão; `retry_erros=False` permite
      apenas listar a falha sem nova tentativa.
    - Arquivo novo é registrado como pendente antes do parse; se o parse
      falhar, a linha vira `erro` com a mensagem, sem currículo parcial.
    """
    if pasta is None:
        pasta = caminho_inbox()
    proprio = isinstance(con, (str, Path))
    if proprio:
        con = conectar(con)
    try:
        criar_schema(con)
        resumo = {
            "total": 0,
            "processados": 0,
            "duplicados": 0,
            "erros": 0,
            "candidatos_criados": 0,
            "versoes_atualizadas": 0,
            "recalculados": 0,
            "auditoria": [],
        }
        dados_arquivos = escanear(con, pasta)
        resumo["total"] = len(dados_arquivos)

        for info in dados_arquivos:
            checksum = info["checksum"]
            arquivo = info["arquivo"]
            nome = info["nome"]
            status_atual = info["status"]
            candidato_id_antigo = info.get("candidato_id")
            curriculo_id_antigo = info.get("curriculo_id")
            versao_antiga = int(info.get("versao") or 1)

            _linha_inbox(con, checksum, nome, arquivo)

            if status_atual == "processado":
                resumo["duplicados"] += 1
                continue

            if status_atual == "duplicado" and curriculo_id_antigo is not None:
                con.execute(
                    "UPDATE inbox_arquivos SET status = 'duplicado',"
                    " candidato_id = ?, curriculo_id = ?, versao = ?,"
                    " erro = NULL, atualizado_em = datetime('now')"
                    " WHERE checksum = ?",
                    (
                        candidato_id_antigo,
                        curriculo_id_antigo,
                        versao_antiga,
                        checksum,
                    ),
                )
                con.commit()
                resumo["duplicados"] += 1
                continue

            if status_atual == "erro" and not retry_erros:
                resumo["erros"] += 1
                continue

            con.execute(
                "UPDATE inbox_arquivos SET status = 'pendente', erro = NULL,"
                " atualizado_em = datetime('now') WHERE checksum = ?",
                (checksum,),
            )
            con.commit()

            registro = {
                "id": checksum[:8],
                "nome_arquivo": nome,
                "nome_candidato": Path(arquivo).stem,
                "link_individual": "",
                "status_download": "ok (inbox)",
                "caminho_cache": str(arquivo).replace("\\", "/"),
            }

            try:
                resultado = ingerir_registro(
                    con, cofre, registro, ator=ator
                )
            except Exception as exc:  # noqa: BLE001 - lote continua
                con.execute(
                    "UPDATE inbox_arquivos SET status = 'erro', erro = ?,"
                    " atualizado_em = datetime('now') WHERE checksum = ?",
                    (str(exc), checksum),
                )
                con.commit()
                registrar(
                    con,
                    acao="erro_ingestao",
                    ator=ator,
                    entidade="inbox_arquivos",
                    entidade_id=None,
                    detalhes={
                        "arquivo": nome,
                        "checksum": checksum,
                        "erro": str(exc),
                    },
                )
                resumo["erros"] += 1
                resumo["auditoria"].append(
                    {"acao": "erro_ingestao", "arquivo": nome, "erro": str(exc)}
                )
                continue

            if resultado["status"] != "processado":
                con.execute(
                    "UPDATE inbox_arquivos SET status = 'duplicado',"
                    " candidato_id = ?, curriculo_id = ?, versao = ?,"
                    " erro = NULL, atualizado_em = datetime('now')"
                    " WHERE checksum = ?",
                    (
                        resultado["candidato_id"],
                        resultado["curriculo_id"],
                        resultado["versao"],
                        checksum,
                    ),
                )
                con.commit()
                resumo["duplicados"] += 1
                continue

            novo_candidato_id = int(resultado["candidato_id"])
            novo_curriculo_id = int(resultado["curriculo_id"])
            nova_versao = int(resultado["versao"])
            con.execute(
                "UPDATE inbox_arquivos SET status = 'processado',"
                " candidato_id = ?, curriculo_id = ?, versao = ?, erro = NULL,"
                " atualizado_em = datetime('now') WHERE checksum = ?",
                (novo_candidato_id, novo_curriculo_id, nova_versao, checksum),
            )
            con.commit()

            acao = (
                "atualizar_curriculo"
                if nova_versao > 1
                else "ingerir_curriculo"
            )
            registrar(
                con,
                acao=acao,
                ator=ator,
                entidade="inbox_arquivos",
                entidade_id=None,
                detalhes={
                    "arquivo": nome,
                    "checksum": checksum,
                    "versao_anterior": versao_antiga,
                    "versao_nova": nova_versao,
                    "candidato_id": novo_candidato_id,
                },
            )
            resumo["processados"] += 1
            if resultado.get("candidato_criado"):
                resumo["candidatos_criados"] += 1
            if nova_versao > 1:
                resumo["versoes_atualizadas"] += 1

            try:
                resultados_score = recalcular_candidato(
                    con, novo_candidato_id, ator=ator
                )
                if resultados_score:
                    resumo["recalculados"] += 1
            except Exception as exc:  # noqa: BLE001 - não invalida a ingestão
                registrar(
                    con,
                    acao="erro_recalculo",
                    ator=ator,
                    entidade="candidatos",
                    entidade_id=novo_candidato_id,
                    detalhes={"erro": str(exc), "origem": "inbox"},
                )
                resumo["auditoria"].append(
                    {
                        "acao": "erro_recalculo",
                        "candidato_id": novo_candidato_id,
                        "erro": str(exc),
                    }
                )

        from src.ui import cache

        cache.limpar()
        return resumo
    finally:
        if proprio:
            con.close()
