"""Fase 0 — testes de fumaça da estrutura e das convenções."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_estrutura_minima():
    esperados = [
        "app.py",
        "requirements.txt",
        ".env.example",
        "README.md",
        ".gitignore",
        "scripts/inventario.py",
        "scripts/cache_drive.py",
        "scripts/build_db.py",
        "scripts/seed_demo.py",
        "scripts/reset_demo.py",
        "src/ingestion/__init__.py",
        "src/extraction/__init__.py",
        "src/scoring/__init__.py",
        "src/ui/__init__.py",
        "src/governance/__init__.py",
    ]
    for caminho in esperados:
        assert (ROOT / caminho).exists(), f"falta {caminho}"


def test_gitignore_cobre_dados_e_secrets():
    texto = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for padrao in ["data/", ".env", "*.db", "*.docx"]:
        assert padrao in texto, f"falta {padrao} no .gitignore"


def test_inventario_carrega_pares():
    from scripts.inventario import carregar_pares

    dump = "./drive-link 1MJmt-wZNGumCdMShy70mkpfAFzKaeq6A/view https://drive.google.com/file/d/FILE_ABC/view\n"
    dump += "Example\\_Resume.docx"
    pares = carregar_pares(dump)
    assert pares == [("FILE_ABC", "Example_Resume.docx")]


def test_inventario_aceita_extensao_maiuscula():
    # 4 dos 228 arquivos do case têm extensão .DOCX (case-insensitive obrigatório)
    from scripts.inventario import carregar_pares

    dump = "https://drive.google.com/file/d/FILE_UPPER/view\nResume_2.DOCX"
    assert carregar_pares(dump) == [("FILE_UPPER", "Resume_2.DOCX")]


def test_sanitizar_nome_caminho_windows():
    from scripts.nome_arquivo import sanitizar

    # caso do crash: backslash no meio do nome vindo do Drive
    assert sanitizar("Adhi Gopalam \\- SM.docx") == "Adhi Gopalam - SM.docx"
    # ponto final solto é proibido no Windows
    assert sanitizar("Nome final.docx.").endswith(".docx")
    # escape de underscore é desfeito
    assert sanitizar("A\\_B.docx") == "A_B.docx"
