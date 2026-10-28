"""Fase 1 (3/4) — PII vault: criptografia, redaction e busca de dados pessoais.

Contrato:
- dados pessoais vivem APENAS em `cofre_dados_pessoais`, cifrados com Fernet;
- a UI nunca mostra o valor: `mascarar()` devolve `***`;
- leitura do cofre é auditada (`ler_pii`);
- `buscar_pii`/`redigir` são usados pela extração (Fase 2) para mover PII do
  texto do currículo para o cofre e redigir o restante.
- Chave: variável BIDROOM_PII_KEY ou `.env` local (gitignored); se não existir,
  gera e grava — nunca versionar.
"""
from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

from cryptography.fernet import Fernet

from src.governance.auditoria import registrar

CHAVE_ENV = "BIDROOM_PII_KEY"

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Telefone brasileiro: evita confundir intervalos de anos ("2015 - 2023")
# com números. Aceita (11) 98765-4321, 11 98765-4321, 11987654321 e +55.
TELEFONE_RE = re.compile(
    r"(?<!\d)(?!\s*(?:19|20)\d{2}\s*[-–—]\s*(?:19|20)\d{2})"
    r"(?:\+?55[\s.-]?)?"
    r"(?:\(\d{2}\)[\s.-]?|\d{2}[\s.-])?"
    r"9?\d{4}[\s.-]?\d{4}(?!\d)"
)
CEPE_RE = re.compile(r"\b\d{5}-?\d{3}\b")
ENDERECO_RE = re.compile(
    r"(?:Rua|Avenida|Av\.?|Alameda|Travessa|Rodovia|Street|Road|Rd\.?|Avenue|"
    r"Ave\.?|Lane|Drive|Colony|Nagar|Sector)\s+[^\n,;]{3,60}",
    re.IGNORECASE,
)


def _ler_env(caminho: str | Path = ".env") -> dict[str, str]:
    valores: dict[str, str] = {}
    arquivo = Path(caminho)
    if not arquivo.exists():
        return valores
    for linha in arquivo.read_text(encoding="utf-8").splitlines():
        if "=" in linha and not linha.strip().startswith("#"):
            chave, _, valor = linha.partition("=")
            valores[chave.strip()] = valor.strip()
    return valores


def obter_chave() -> str:
    """Chave Fernet: ambiente > .env local > gera e grava no .env."""
    chave = os.environ.get(CHAVE_ENV)
    if chave:
        return chave
    chave = _ler_env().get(CHAVE_ENV)
    if chave:
        return chave
    chave = Fernet.generate_key().decode()
    with Path(".env").open("a", encoding="utf-8") as f:
        f.write(f"{CHAVE_ENV}={chave}\n")
    return chave


class CofrePII:
    """Wrapper de cifra/decifra do cofre (Fernet)."""

    def __init__(self, chave: str | bytes):
        self._fern = Fernet(chave.encode() if isinstance(chave, str) else chave)

    @classmethod
    def carregar_ou_criar(cls) -> "CofrePII":
        return cls(obter_chave())

    @staticmethod
    def gerar_chave() -> str:
        return Fernet.generate_key().decode()

    def cifrar(self, valor: str) -> str:
        return self._fern.encrypt(valor.encode("utf-8")).decode("ascii")

    def decifrar(self, token: str) -> str:
        return self._fern.decrypt(token.encode("ascii")).decode("utf-8")


def salvar(
    con: sqlite3.Connection,
    cofre: CofrePII,
    candidato_id: int,
    tipo: str,
    valor: str,
    ator: str = "sistema",
    commit: bool = True,
) -> int:
    """Cifra e grava um dado pessoal; auditado como `criar_pii`.

    `commit=False` permite que a ingestão inteira seja uma única transação:
    se a extração falhar depois, nem a PII nem a auditoria ficam órfãs.
    """
    token = cofre.cifrar(valor)
    cur = con.execute(
        "INSERT INTO cofre_dados_pessoais (candidato_id, tipo, valor_criptografado)"
        " VALUES (?, ?, ?)",
        (candidato_id, tipo, token),
    )
    registrar(
        con,
        acao="criar_pii",
        ator=ator,
        entidade="cofre_dados_pessoais",
        entidade_id=cur.lastrowid,
        detalhes={"tipo": tipo, "candidato_id": candidato_id},
        commit=False,
    )
    if commit:
        con.commit()
    return cur.lastrowid


def revelar(
    con: sqlite3.Connection, cofre: CofrePII, registro_id: int, ator: str
) -> str:
    """Decifra um registro (uso restrito); audita a leitura como `ler_pii`."""
    row = con.execute(
        "SELECT valor_criptografado, tipo FROM cofre_dados_pessoais WHERE id = ?",
        (registro_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"registro de PII {registro_id} não encontrado")
    registrar(
        con,
        acao="ler_pii",
        ator=ator,
        entidade="cofre_dados_pessoais",
        entidade_id=registro_id,
        detalhes={"tipo": row["tipo"]},
    )
    con.commit()
    return cofre.decifrar(row["valor_criptografado"])


def mascarar(valor: str, tipo: str | None = None) -> str:
    """Contrato da UI: PII nunca é exibida — sempre `***`."""
    return "***"


def estatisticas(con: sqlite3.Connection) -> dict:
    """Retorna estatísticas gerais do cofre PII.

    Conta total de registros, por tipo (email/telefone/endereco),
    e a data da última leitura auditada (via registros_auditoria).
    Nunca retorna valores descriptografados.
    """
    total = con.execute(
        "SELECT COUNT(*) AS c FROM cofre_dados_pessoais"
    ).fetchone()["c"]

    email = con.execute(
        "SELECT COUNT(*) AS c FROM cofre_dados_pessoais WHERE tipo = 'email'"
    ).fetchone()["c"]

    telefone = con.execute(
        "SELECT COUNT(*) AS c FROM cofre_dados_pessoais WHERE tipo = 'telefone'"
    ).fetchone()["c"]

    endereco = con.execute(
        "SELECT COUNT(*) AS c FROM cofre_dados_pessoais WHERE tipo = 'endereco'"
    ).fetchone()["c"]

    # última leitura auditada de PII
    ultima_ler = con.execute(
        "SELECT data_hora FROM registros_auditoria"
        " WHERE acao = 'ler_pii'"
        " ORDER BY data_hora DESC LIMIT 1"
    ).fetchone()
    ultima_ler_str = ultima_ler["data_hora"] if ultima_ler else None

    return {
        "total": total,
        "email": email,
        "telefone": telefone,
        "endereco": endereco,
        "ultima_ler_pii": ultima_ler_str,
    }


def buscar_pii(texto: str) -> list[dict[str, object]]:
    """Encontra e-mails, telefones e endereços no texto de um currículo."""
    achados: list[dict[str, object]] = []
    for tipo, regex in (
        ("email", EMAIL_RE),
        ("telefone", TELEFONE_RE),
        ("endereco", ENDERECO_RE),
        ("endereco", CEPE_RE),
    ):
        for m in regex.finditer(texto):
            achados.append(
                {"tipo": tipo, "valor": m.group(0), "inicio": m.start(), "fim": m.end()}
            )
    achados.sort(key=lambda a: int(a["inicio"]))
    return achados


def redigir(texto: str) -> str:
    """Substitui toda PII encontrada por `***` (redaction na ingestão)."""
    for achado in sorted(buscar_pii(texto), key=lambda a: int(a["inicio"]), reverse=True):
        inicio, fim = int(achado["inicio"]), int(achado["fim"])
        texto = texto[:inicio] + "***" + texto[fim:]
    return texto
