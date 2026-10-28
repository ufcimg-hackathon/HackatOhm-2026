# Validação da estrutura do repositório — 27/10/2026

Os testes abaixo foram executados **depois** de adaptar os caminhos para `src/experimentos/` e `data/curriculos_raw/`.

## Testes automatizados

Comando executado:

```bash
python -m unittest discover -s src/experimentos/tests -p 'test_*.py' -v
```

**Resultado observado:** 15 testes executados, todos aprovados (OK), em aproximadamente 6,48 segundos neste ambiente.

## Ingestão e banco SQLite

```bash
python src/experimentos/analisar_curriculos.py --input data/curriculos_raw --output saida --repeticoes 1
```

**Resultado observado:** 228 documentos processados sem falhas de leitura; tempo de 5,391 segundos (uma execução). As 3.713 relações entre documentos e competências continuam iguais às da análise de referência.

## Comparação de consultas

```bash
python src/experimentos/benchmark_buscas.py --input data/curriculos_raw --database saida/curriculos_anonimizados.sqlite --output saida/benchmark_buscas.json --repeticoes 2
```

**Resultado observado na verificação de layout:** 10 competências consultadas, 20 consultas por método. Medianas de 64,001 ms para varredura linear em texto já extraído e 0,264 ms para consulta SQLite indexada, com contagens idênticas. Os resultados anteriores permanecem em `data/resultados_referencia/`. Valores de latência variam entre execuções e equipamentos.

## Seleção de equipes

```bash
python src/experimentos/simular_equipe.py --output saida/simulacao_equipe.json
```

**Resultado observado:** 210 composições avaliadas e 13 viáveis, com sete perfis fictícios e três funções. O motor de otimização é uma simulação independente dos currículos.

**Limites:** os testes não medem redução de horas de gestores, não validam certificados e não comprovam a execução de integrações corporativas ou atualização automática por eventos.

## Revisão após a reorganização e limpeza

- Estrutura final: `src/experimentos/` e `src/bidroom/`.
- Experimento: 15 testes aprovados após a mudança de pasta.
- BidRoom antes da limpeza: 140 aprovados e quatro ignorados.
- BidRoom depois da limpeza: 139 aprovados e quatro ignorados.
- Um dos testes coletados era uma cópia de outro; removê-lo não retirou
  cenário de validação. Oito definições anteriores de testes com nomes
  repetidos já eram sobrescritas; uma fixture quebrada não tinha consumidores.
- Os quatro casos ignorados dependem do inventário/cache local do aplicativo;
  os 228 DOCX da raiz continuam cobertos pelo experimento.

Comandos, usando o ambiente virtual da raiz:

```bash
python -m unittest discover -s src/experimentos/tests -p 'test_*.py' -v
cd src/bidroom
python -m pytest -q -rs
```

Ambiente da revisão: Linux, Python 3.12.14, pytest 9.2.0 e Streamlit 1.66.1.
Não foi executado piloto com gestores nem medido o esforço humano de manutenção.

### Verificação dos comandos após a mudança

Os quatro comandos de análise, benchmark, simulação e busca em português
foram executados pelos novos caminhos, com saídas em diretório temporário.
A carga preservou 228 documentos e 3.713 associações; as consultas dos dois
métodos tiveram contagens iguais; a simulação manteve 210 arranjos e 13
equipes viáveis. Os tempos históricos usados no resumo permanecem intactos.

Também foram verificados os CLIs `build_db.py`, `ingest.py` e `score.py`
com `--help`, a resolução dos links Markdown e as regras de ignore para
bancos, chaves, `.env` e ambiente virtual. O `pip check` não encontrou
dependências quebradas no ambiente desta revisão.
