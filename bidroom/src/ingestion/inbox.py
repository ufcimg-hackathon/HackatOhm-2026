"""Fase 6 — sincronização da pasta inbox.

Responsabilidades:
- Escanear `data/inbox/` (ou pasta definida por BIDROOM_INBOX) em busca de .docx.
- Para cada arquivo: compute checksum, verificar se já foi processado, inserir/atualizar `inbox_arquivos`.
- Chamar o pipeline `ingerir_registro` para novos arquivos.
- Registrar auditoria (`ingerir_curriculo` / `atualizar_curriculo` / `erro_ingestao`).
- Após sucesso, invalidar cache de scoring (`cache.limpar()`) se houver disputa ativa.
- Retornar resumo: processados, erros, duplicados, candidatos criados/atualizados.
"""

from __future__ import annotations

import os
from pathlib import Path

from docx import Document

from src.extraction.parser_docx import checksum_arquivo, parse_docx
from src.governance.auditoria import registrar
from src.governance.vault import CofrePII, mascarar
from src.ingestion.db import conectar, criar_schema
from src.ingestion.pipeline import ingerir_registro
from src.scoring.servico import recalcular_candidato
from src.ui.comum import caminho_inbox


def _arquivos_inbox(pasta: Path) -> list[Path]:
    """Retorna .docx da pasta, ignorando arquivos temporários do Word."""
    if not pasta.exists():
        return []
    return sorted(
        f for f in pasta.iterdir()
        if f.is_file()
        and f.suffix.lower() == ".docx"
        and not f.name.startswith("~$")
    )


def _checksum_e_nome(arquivo: Path) -> tuple[str, str]:
    """Retorna (checksum, nome_arquivo)."""
    checksum = checksum_arquivo(arquivo)
    return checksum, arquivo.name


def _registros_ja_processados(con, checksums: set[str]) -> dict[str, dict]:
    """Dado um conjunto de checksums, retorna {checksum: row} para linhas já em inbox_arquivos."""
    if not checksums:
        return {}
    rows = con.execute(
        "SELECT checksum, status, candidato_id, curriculo_id, versao FROM inbox_arquivos WHERE checksum IN ({})".format(
            ",".join("?" * len(checksums))
        ),
        list(checksums),
    ).fetchall()
    return {row["checksum"]: dict(row) for row in rows}


def esca(con, pasta: Path) -> list[dict]:
    """Escaneia a pasta e retorna lista de dicts com info do arquivo e seu status atual.

    Cada dict tem: arquivo (caminho Path), checksum, nome, status_atual (pendente/processado/erro/duplicado),
    candidato_id, curriculo_id, versao.
    """
    arquivos = _arquivos_inbox(pasta)
    checksums = {_checksum_e_nome(f)[0] for f in arquivos}
    existentes = _registros_ja_processados(con, checksums)

    resumo = []
    for f in arquivos:
        checksum, nome = _checksum_e_nome(f)
        row = existentes.get(checksum)
        if row:
            status = row["status"]
        else:
            # também verifica curriculos por checksum (pipeline dedupe)
            existente = con.execute(
                "SELECT id, candidato_id, versao FROM curriculos WHERE checksum = ?",
                (checksum,),
            ).fetchone()
            if existente:
                status = "duplicado"
                candidato_id = existente["candidato_id"]
                curriculo_id = existente["id"]
                versao = existente["versao"]
            else:
                status = "pendente"
                candidato_id = None
                curriculo_id = None
                versao = 1
        resumo.append(
            {
                "arquivo": f,
                "checksum": checksum,
                "nome": nome,
                "status": status,
                "candidato_id": candidato_id,
                "curriculo_id": curriculo_id,
                "versao": versao,
            }
        )
    return resumo


def sincronizar(
    con,
    cofre: CofrePII,
    pasta: Path | None = None,
    ator: str = "inbox",
) -> dict:
    """Sincroniza a pasta inbox com o banco de dados.

    Etapas:
    1. Escaneia a pasta.
    2. Para cada arquivo:
       - Se já processado (checksum em curriculos ou inbox_arquivos processado/erro):
         - Se status processado -> "sem alterações", nada a fazer exceto auditagem opcional.
         - Se status erro -> retry de parse (nova tentativa).
       - Se novo -> chama ingerir_registro.
    3. Invalida cache de scoring se houver disputa ativa.
    4. Retorna resumo.

    Retorna dict com: processados, erros, duplicados, candidatos_criados, versoes_atualizadas,
    recalculados, auditoria.
    """
    if pasta is None:
        pasta = caminho_inbox()
    con = conectar(con) if isinstance(con, str) else con  # con já é Connection
    # garantir tabela
    criar_schema(con)  # incluirá inbox_arquivos se ainda nao existir

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

    dados_arquivos = esca(con, pasta)
    resumo["total"] = len(dados_arquivos)

    for info in dados_arquivos:
        resumo["total"]  # já counted
        checksum = info["checksum"]
        arquivo = info["arquivo"]
        nome = info["nome"]
        status_atual = info["status"]
        candidato_id_antigo = info.get("candidato_id")
        curriculo_id_antigo = info.get("curriculo_id")
        versao_antiga = info.get("versao", 1)

        # Se já está processado e não há mudança de arquivo, só reportamos
        if status_atual in ("processado", "erro") and not info.get("retry"):
            # Se o usuário quer retry de erro, we allow via info["retry"]=True
            if status_atual == "erro" and not info.get("retry"):
                # tenta novamente: resetamos status para pendente e prosseguimos
                con.execute(
                    "UPDATE inbox_arquivos SET status = 'pendente', erro = NULL, atualizado_em = datetime('now') WHERE checksum = ?",
                    (checksum,),
                )
                con.commit()
                status_atual = "pendente"
            else:
                resumo["duplicados"] += 1
                resumo["auditoria"].append(
                    {
                        "acao": "sincronizar_inbox",
                        "ator": ator,
                        "entidade": "inbox_arquivos",
                        "entidade_id": None,
                        "detalhes": {
                            "arquivo": nome,
                            "checksum": checksum,
                            "status": status_atual,
                            "motivo": "já processado",
                        },
                    }
                )
                continue

        # Prepara o registro para o pipeline
        # O pipeline espera: id, nome_arquivo, nome_candidato, link_individual, status_download, caminho_cache
        nome_candidato = Path(arquivo).stem  # usa o nome do arquivo sem extensão como pseudônimo
        registro = {
            "id": checksum[:8],  # prefix curto como id
            "nome_arquivo": nome,
            "nome_candidato": nome_candidato,
            "link_individual": "",
            "status_download": "ok (inbox)",
            "caminho_cache": str(arquivo).replace("\\", "/"),
        }

        try:
            resultado = ingerir_registro(con, cofre, registro, ator=ator)
        except Exception as exc:  # noqa: BLE001 - continuamos com outros arquivos
            # registrar erro no inbox e auditoria
            con.execute(
                "UPDATE inbox_arquivos SET status = 'erro', erro = ?, atualizado_em = datetime('now') WHERE checksum = ?",
                (str(exc), checksum),
            )
            con.commit()
            registrar(
                con,
                acao="erro_ingestao",
                ator=ator,
                entidade="inbox_arquivos",
                entidade_id=int(checksum, 16) if checksum else None,
                detalhes={"arquivo": nome, "erro": str(exc), "checksum": checksum},
            )
            resumo["erros"] += 1
            resumo["auditoria"].append(
                {
                    "acao": "erro_ingestao",
                    "ator": ator,
                    "entidade": "inbox_arquivos",
                    "entidade_id": None,
                    "detalhes": {"arquivo": nome, "erro": str(exc)},
                }
            )
            continue

        # Pipeline retornou status
        if resultado["status"] == "processado":
            # Atualiza linha do inbox
            novo_candidato_id = resultado["candidato_id"]
            novo_curriculo_id = resultado["curriculo_id"]
            nova_versao = resultado["versao"]

            con.execute(
                """UPDATE inbox_arquivos
                   SET status = 'processado',
                       candidato_id = ?,
                       curriculo_id = ?,
                       versao = ?,
                       atualizado_em = datetime('now')
                 WHERE checksum = ?""",
                (novo_candidato_id, novo_curriculo_id, nova_versao, checksum),
            )
            con.commit()

            registrar(
                con,
                acao=(
                    "atualizar_curriculo"
                    if candidato_id_antigo is not None and candidato_id_antigo != novo_candidato_id
                    else "ingerir_curriculo"
                ),
                ator=ator,
                entidade="inbox",
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
            resumo["auditoria"].append(
                {
                    "acao": (
                        "atualizar_curriculo"
                        if candidato_id_antigo is not None and candidato_id_antigo != novo_candidato_id
                        else "ingerir_curriculo"
                    ),
                    "ator": ator,
                    "entidade": "inbox",
                    "entidade_id": None,
                    "detalhes": {
                        "arquivo": nome,
                        "checksum": checksum,
                        "versao_anterior": versao_antiga,
                        "versao_nova": nova_versao,
                        "candidato_id": novo_candidato_id,
                    },
                }
            )

            # Contar candidato criado se foi novo
            if resultado.get("candidato_criado"):
                resumo["candidatos_criados"] += 1

            # Versão atualizada (não é o primeiro)
            if candidato_id_antigo is not None and candidato_id_antigo != novo_candidato_id:
                resumo["versoes_atualizadas"] += 1

            # Recalcular disputa ativa se existir
            disputa = None  # we'll check below
        else:
            # status duplicado etc.
            resumo["duplicados"] += 1
            registrar(
                con,
                acao="sincronizar_inbox",
                ator=ator,
                entidade="inbox_arquivos",
                entidade_id=None,
                detalhes={"arquivo": nome, "checksum": checksum, "status": resultado["status"]},
            )
            continue

        # Recalcular disputa ativa após processamento bem-sucedido
        # Verificar se há disputa ativa
        from src.ui.dados import carregar_disputa
        disputa = carregar_disputa(con)
        if disputa and disputa.get("preset_versao_id"):
            try:
                recalcular_candidato(con, novo_candidato_id, ator=ator)
                resumo["recalculados"] += 1
            except Exception:
                # se falhar, não bloqueia o resto
                pass

    # Limpar cache após o lote
    from src.ui import cache
    cache.limpar()

    return resumo