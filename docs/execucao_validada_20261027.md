# Validação da estrutura do repositório — 27/10/2026

Os testes abaixo foram executados **depois** de adaptar os caminhos para `src/` e `data/curriculos_raw/`.

## Testes automatizados

Comando executado:

```bash
python -m unittest discover -s src/tests -p 'test_*.py' -v
```

**Resultado observado:** 15 testes executados, todos aprovados (OK), em aproximadamente 6,48 segundos neste ambiente.

## Ingestão e banco SQLite

```bash
python src/analisar_curriculos.py --input data/curriculos_raw --output saida --repeticoes 1
```

**Resultado observado:** 228 documentos processados sem falhas de leitura; tempo de 5,391 segundos (uma execução). As 3.713 relações entre documentos e competências continuam iguais às da análise de referência.

## Comparação de consultas

```bash
python src/benchmark_buscas.py --input data/curriculos_raw --database saida/curriculos_anonimizados.sqlite --output saida/benchmark_buscas.json --repeticoes 2
```

**Resultado observado na verificação de layout:** 10 competências consultadas, 20 consultas por método. Medianas de 64,001 ms para varredura linear em texto já extraído e 0,264 ms para consulta SQLite indexada, com contagens idênticas. Os resultados anteriores permanecem em `data/resultados_referencia/`. Valores de latência variam entre execuções e equipamentos.

## Seleção de equipes

```bash
python src/simular_equipe.py --output saida/simulacao_equipe.json
```

**Resultado observado:** 210 composições avaliadas e 13 viáveis, com sete perfis fictícios e três funções. O motor de otimização é uma simulação independente dos currículos.

**Limites:** os testes não medem redução de horas de gestores, não validam certificados e não comprovam a execução de integrações corporativas ou atualização automática por eventos.
