"""Fase 2 — pipeline de ingestão dos currículos (handoff §5).

Fluxo por arquivo:
1. checksum SHA-256 (idempotência: mesmo arquivo não entra duas vezes);
2. parse do .docx (texto + páginas estimadas);
3. extração por regras (fatos + evidências N0/N1);
4. PII (e-mail/telefone/endereço) vai para o cofre cifrado e é redigida do
   `raw_texto`, dos trechos de evidência e do residual;
5. candidato: mesmo e-mail => mesmo `candidato_id`; currículo novo do mesmo
   candidato ganha `versao + 1`;
6. grava fatos, evidências, residual e um evento de auditoria por currículo.

Nada de LLM nesta fase (`ENABLE_LLM_EXTRACTION` continua fora do caminho).
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.fernet import InvalidToken

from src.extraction.parser_docx import checksum_arquivo, parse_docx
from src.extraction.regras import PERIODO_RE, FatoExtraido, extrair
from src.governance.auditoria import registrar
from src.governance.vault import CofrePII, buscar_pii, redigir, salvar


@dataclass
class ResumoIngestao:
    total: int = 0
    processados: int = 0
    duplicados: int = 0
    erros: int = 0
    candidatos_criados: int = 0
    pii_gravada: int = 0
    fatos: int = 0
    detalhes_erros: list[str] = field(default_factory=list)


def carregar_inventario(caminho_csv: str | Path) -> list[dict]:
    """Lê o inventário e mantém apenas downloads concluídos."""
    with Path(caminho_csv).open(encoding="utf-8-sig", newline="") as f:
        registros = list(csv.DictReader(f))
    return [
        r
        for r in registros
        if (r.get("status_download") or "").lower().startswith("ok")
    ]


def carregar_mapa_emails(con, cofre: CofrePII) -> dict[str, int]:
    """e-mail (minúsculo) -> candidato_id, para deduplicar por PII cifrada.

    Leitura interna de deduplicação: não expõe valor e não substitui a
    leitura auditada (`revelar`) usada na UI.
    """
    mapa: dict[str, int] = {}
    for row in con.execute(
        "SELECT candidato_id, valor_criptografado FROM cofre_dados_pessoais"
        " WHERE tipo = 'email'"
    ):
        try:
            mapa[cofre.decifrar(row["valor_criptografado"]).lower()] = row[
                "candidato_id"
            ]
        except InvalidToken:
            continue
    return mapa


def _partes_periodo(texto: str) -> tuple[str | None, str | None]:
    achado = PERIODO_RE.search(texto or "")
    if not achado:
        return None, None
    return achado.group(1), achado.group(2)


def _ano(valor: str | None) -> int | None:
    if not valor:
        return None
    achado = PERIODO_RE.search(valor)
    alvo = achado.group(1) if achado else valor
    digitos = "".join(c for c in alvo if c.isdigit())
    if len(digitos) >= 4:
        return int(digitos[:4])
    return None


def _persistir_fato(con, curriculo_id: int, fato: FatoExtraido) -> int:
    dados = fato.dados
    if fato.tipo == "competencia":
        cur = con.execute(
            "INSERT INTO competencias (curriculo_id, nome, anos, ultimo_uso, familia)"
            " VALUES (?, ?, ?, NULL, ?)",
            (curriculo_id, dados["nome"], dados.get("anos"), dados.get("familia")),
        )
    elif fato.tipo == "experiencia":
        inicio, fim = _partes_periodo(str(dados.get("periodo") or ""))
        cur = con.execute(
            "INSERT INTO experiencias (curriculo_id, empresa, papel, dominio,"
            " data_inicio, data_fim, tecnologias, resumo)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                curriculo_id,
                dados.get("empresa"),
                dados.get("papel"),
                dados.get("dominio"),
                inicio,
                fim,
                json.dumps(dados.get("tecnologias") or [], ensure_ascii=False),
                dados.get("resumo"),
            ),
        )
    elif fato.tipo == "certificacao":
        cur = con.execute(
            "INSERT INTO certificacoes (curriculo_id, nome, emissor, ano)"
            " VALUES (?, ?, ?, ?)",
            (curriculo_id, dados["nome"], dados.get("emissor"), dados.get("ano")),
        )
    elif fato.tipo == "formacao":
        inicio, fim = _partes_periodo(str(dados.get("curso") or ""))
        cur = con.execute(
            "INSERT INTO formacoes (curriculo_id, grau, curso, instituicao,"
            " ano_inicio, ano_fim) VALUES (?, ?, ?, ?, ?, ?)",
            (
                curriculo_id,
                dados.get("grau"),
                dados.get("curso"),
                dados.get("instituicao"),
                _ano(inicio),
                _ano(fim),
            ),
        )
    elif fato.tipo == "idioma":
        cur = con.execute(
            "INSERT INTO idiomas (curriculo_id, idioma, nivel) VALUES (?, ?, ?)",
            (curriculo_id, dados["idioma"], dados.get("nivel")),
        )
    else:
        raise ValueError(f"tipo de fato desconhecido: {fato.tipo}")
    return int(cur.lastrowid)


def ingerir_registro(
    con,
    cofre: CofrePII,
    registro: dict,
    mapa_emails: dict[str, int] | None = None,
    ator: str = "build",
    candidato_id_forcado: int | None = None,
) -> dict:
    """Ingere um currículo; devolve status/ids/contadores.

    `candidato_id_forcado` é usado pela atualização de um currículo existente
    (T3): o arquivo novo vira uma nova versão do mesmo candidato, sem criar
    outro candidato. O checksum continua idempotente.
    """
    if mapa_emails is None:
        mapa_emails = carregar_mapa_emails(con, cofre)

    caminho = Path(registro["caminho_cache"])
    if not caminho.exists():
        raise FileNotFoundError(f"arquivo nao encontrado: {caminho}")

    checksum = checksum_arquivo(caminho)
    existente = con.execute(
        "SELECT id, candidato_id, versao FROM curriculos WHERE checksum = ?",
        (checksum,),
    ).fetchone()
    if existente:
        if (
            candidato_id_forcado is not None
            and int(existente["candidato_id"]) != candidato_id_forcado
        ):
            raise ValueError("arquivo ja processado para outro candidato")
        return {
            "status": "duplicado",
            "curriculo_id": int(existente["id"]),
            "candidato_id": int(existente["candidato_id"]),
            "versao": int(existente["versao"]),
            "candidato_criado": False,
            "pii_gravada": 0,
            "fatos": 0,
        }

    documento = parse_docx(caminho)
    if len(documento.texto.strip()) < 50:
        raise ValueError("texto insuficiente (talvez PDF digitalizado sem OCR)")

    extracao = extrair(documento.texto, documento.n_paginas)
    achados_pii = buscar_pii(documento.texto)
    email = next(
        (str(a["valor"]).lower() for a in achados_pii if a["tipo"] == "email"),
        None,
    )

    candidato_criado = False
    if candidato_id_forcado is not None:
        existe = con.execute(
            "SELECT id FROM candidatos WHERE id = ?", (candidato_id_forcado,)
        ).fetchone()
        if existe is None:
            raise KeyError(f"candidato {candidato_id_forcado} nao encontrado")
        if email:
            dono = mapa_emails.get(email)
            if dono is not None and dono != candidato_id_forcado:
                raise ValueError("e-mail do arquivo pertence a outro candidato")
            mapa_emails[email] = candidato_id_forcado
        candidato_id = candidato_id_forcado
    else:
        candidato_id = mapa_emails.get(email) if email else None

    if candidato_id is None:
        proximo = con.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 AS n FROM candidatos"
        ).fetchone()["n"]
        idiomas = (
            json.dumps(
                [f.dados["idioma"] for f in extracao.idiomas], ensure_ascii=False
            )
            if extracao.idiomas
            else None
        )
        cur = con.execute(
            "INSERT INTO candidatos (nome_pseudonimo, origem, idiomas)"
            " VALUES (?, 'base_hackathon', ?)",
            (f"Candidato {proximo:03d}", idiomas),
        )
        candidato_id = int(cur.lastrowid)
        candidato_criado = True
        if email:
            mapa_emails[email] = candidato_id

    versao_anterior = con.execute(
        "SELECT COALESCE(MAX(versao), 0) AS v FROM curriculos"
        " WHERE candidato_id = ?",
        (candidato_id,),
    ).fetchone()["v"]
    versao = versao_anterior + 1
    cur = con.execute(
        "INSERT INTO curriculos (candidato_id, nome_arquivo, caminho_cache,"
        " checksum, n_paginas, versao, status_processamento, raw_texto)"
        " VALUES (?, ?, ?, ?, ?, ?, 'processado', ?)",
        (
            candidato_id,
            registro["nome_arquivo"],
            registro["caminho_cache"],
            checksum,
            documento.n_paginas,
            versao,
            redigir(documento.texto),
        ),
    )
    curriculo_id = int(cur.lastrowid)
    # Atualiza sempre a data de atualização do candidato,
    # não apenas quando candidato_id_forcado é usado.
    con.execute(
        "UPDATE candidatos SET atualizado_em = datetime('now') WHERE id = ?",
        (candidato_id,),
    )

    pii_gravada = 0
    vistos: set[tuple[str, str]] = set()
    for achado in achados_pii:
        chave = (str(achado["tipo"]), str(achado["valor"]))
        if chave in vistos:
            continue
        vistos.add(chave)
        salvar(
            con,
            cofre,
            candidato_id,
            str(achado["tipo"]),
            str(achado["valor"]),
            ator=ator,
        )
        pii_gravada += 1

    fatos = 0
    for fato in extracao.evidencias():
        fato_id = _persistir_fato(con, curriculo_id, fato)
        con.execute(
            "INSERT INTO evidencias (tipo_fato, fato_id, trecho, pagina, nivel,"
            " confianca) VALUES (?, ?, ?, ?, ?, ?)",
            (
                fato.tipo,
                fato_id,
                redigir(fato.trecho),
                fato.pagina,
                fato.nivel,
                fato.confianca,
            ),
        )
        fatos += 1

    for trecho in extracao.residual:
        con.execute(
            "INSERT INTO residual (curriculo_id, termo, trecho) VALUES (?, NULL, ?)",
            (curriculo_id, redigir(trecho)),
        )

    detalhes_auditoria = {
        "checksum": checksum,
        "versao": versao,
        "fatos": fatos,
        "pii": pii_gravada,
    }
    # Como agora sempre atualizamos atualizado_em, registramos as versões
    # para fins de auditoria, mesmo quando não há candidato_id_forçado.
    detalhes_auditoria["versao_anterior"] = int(versao_anterior)
    detalhes_auditoria["versao_nova"] = int(versao)
    registrar(
        con,
        acao=(
            "atualizar_curriculo"
            if candidato_id_forcado is not None
            else "ingerir_curriculo"
        ),
        ator=ator,
        entidade="curriculos",
        entidade_id=curriculo_id,
        detalhes=detalhes_auditoria,
    )
    con.commit()
    return {
        "status": "processado",
        "curriculo_id": curriculo_id,
        "candidato_id": int(candidato_id),
        "versao": int(versao),
        "candidato_criado": candidato_criado,
        "pii_gravada": pii_gravada,
        "fatos": fatos,
    }


def ingerir_inventario(
    con,
    cofre: CofrePII,
    caminho_csv: str | Path,
    limite: int | None = None,
    ator: str = "build",
) -> ResumoIngestao:
    registros = carregar_inventario(caminho_csv)
    if limite is not None:
        registros = registros[:limite]
    resumo = ResumoIngestao(total=len(registros))
    mapa_emails = carregar_mapa_emails(con, cofre)
    for registro in registros:
        try:
            resultado = ingerir_registro(
                con, cofre, registro, mapa_emails=mapa_emails, ator=ator
            )
        except Exception as exc:  # noqa: BLE001 - lote continua e reporta no fim
            con.rollback()
            resumo.erros += 1
            resumo.detalhes_erros.append(
                f"{registro.get('id')} ({registro.get('nome_arquivo')}): {exc}"
            )
            continue
        if resultado["status"] == "duplicado":
            resumo.duplicados += 1
        else:
            resumo.processados += 1
            resumo.pii_gravada += resultado["pii_gravada"]
            resumo.fatos += resultado["fatos"]
            if resultado["candidato_criado"]:
                resumo.candidatos_criados += 1
    return resumo
