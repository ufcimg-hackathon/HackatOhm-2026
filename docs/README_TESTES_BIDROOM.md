# Testes e reprodução do BidRoom

O repositório contém dois componentes complementares, com bancos separados.

| Caminho | Conteúdo |
|---|---|
| `src/experimentos/` | Leitura DOCX/XML, catálogo de competências, SQLite, benchmark e simulação de equipe. |
| `src/experimentos/tests/` | Testes do experimento, incluindo os 228 DOCX. |
| `src/bidroom/` | Aplicativo Streamlit, serviços de ingestão, evidências, editais e composição. |
| `src/bidroom/tests/` | Testes do protótipo e da jornada integrada. |
| `data/curriculos_raw/` | 228 DOCX fornecidos para o desafio. |
| `data/resultados_referencia/` | JSONs históricos dos ensaios, preservados. |
| `saida/` | Resultados locais do experimento, ignorados pelo Git. |

## Instalação

Na raiz do repositório, com Python 3.10 ou superior:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

No PowerShell, use `python -m venv .venv` e `.venv\Scripts\Activate.ps1`.
O arquivo raiz inclui as dependências do BidRoom e o `lxml` do experimento.

## Experimento reproduzível

Execute na raiz, com o ambiente ativado:

```bash
python -m unittest discover -s src/experimentos/tests -p 'test_*.py' -v
python src/experimentos/analisar_curriculos.py --input data/curriculos_raw --output saida --repeticoes 3
python src/experimentos/benchmark_buscas.py --input data/curriculos_raw --database saida/curriculos_anonimizados.sqlite --output saida/benchmark_buscas.json --repeticoes 5
python src/experimentos/simular_equipe.py --output saida/simulacao_equipe.json
python src/experimentos/busca_linguagem_natural.py --database saida/curriculos_anonimizados.sqlite --consulta 'Python e AWS'
```

A ingestão também aceita ZIP. O experimento recria seu banco de saída a cada
execução; use a pasta `saida/` e preserve os JSONs de referência.

## Protótipo

A partir da raiz, com o mesmo ambiente ativo:

```bash
cd src/bidroom
python -m pytest -q -rs
python scripts/build_db.py --db data/demo.db
```

Para ingestão, prepare o inventário e a chave local conforme o
[README do aplicativo](../src/bidroom/README.md). Depois:

```bash
python scripts/ingest.py --db data/demo.db --csv data/inventario_curriculos.csv
python scripts/score.py --db data/demo.db
python -m streamlit run app.py
```

Os testes que dependem de `data/cache_drive/` e do inventário são ignorados
quando esses artefatos locais não existem. O corpus em `data/curriculos_raw/`
na raiz continua sendo testado pela suíte independente. Não se deve anunciar
que todos os testes do protótipo passaram quando houver casos ignorados.

## Interpretação

O ensaio de referência leu 228/228 DOCX, identificou 3.713 associações
textuais e duas duplicatas textuais adicionais. A mediana registrada foi
8,49 segundos. A comparação de consultas usa texto já extraído e índice
preexistente; não mede horas de trabalho dos gestores.

A simulação independente usa sete perfis artificiais. Menções a competências
não comprovam domínio ou certificações. Integrações externas e o impacto
operacional permanecem propostas. O protótipo não tem autenticação e não
está apto a receber dados pessoais reais em um piloto.

Os bancos, chaves e dados locais do aplicativo ficam ignorados pelo Git. Os
originais do desafio devem respeitar as condições de uso e divulgação de
quem os forneceu.
