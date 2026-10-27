# Testes e evidências técnicas — BidRoom

Este material pode ser integrado ao repositório do HackatOhm 2026. **Não substitua o README de envio da equipe.** Os documentos curriculares foram fornecidos pela EloGroup como parte do desafio, conforme informado pela equipe.

## Estrutura

- `src/analisar_curriculos.py`: leitura DOCX, menções a competências, normalização inicial e inserção no SQLite.
- `src/benchmark_buscas.py`: comparação cronometrada de busca linear no texto já extraído e pesquisa em SQLite indexado.
- `src/simular_equipe.py`: simulação fictícia da lógica de composição de equipes.
- `src/busca_linguagem_natural.py`: demonstração de filtro simples por palavras e regras (não usa IA generativa).
- `src/tests/`: testes de unidade e integração com os documentos do desafio.
- `data/curriculos_raw/`: 228 DOCX do desafio.
- `data/resultados_referencia/`: JSON com dados registrados nos testes anteriores, sem alteração.
- `.github/workflows/testes.yml`: workflow de testes de unidade e integração no GitHub Actions.

## Comandos no Fedora/Linux

Na raiz do repositório:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m unittest discover -s src/tests -p 'test_*.py' -v
python src/analisar_curriculos.py --input data/curriculos_raw --output saida --repeticoes 3
python src/benchmark_buscas.py --input data/curriculos_raw --database saida/curriculos_anonimizados.sqlite --output saida/benchmark_buscas.json --repeticoes 5
python src/simular_equipe.py --output saida/simulacao_equipe.json
```

Os scripts também aceitam um arquivo ZIP de entrada, caso a base esteja compactada.

## Interpretação das métricas

- A análise inicial identificou **228/228 DOCX legíveis**, **3.713 relações textuais documento–competência** e **2 duplicatas textuais adicionais**.
- O ensaio de origem registrou **8,49 segundos** de mediana para o lote, e uma busca indexada significativamente mais rápida que a varredura de textos nesse ambiente.
- Tempo de processamento de máquina **não** significa tempo economizado por pessoas.
- Detecção de uma tecnologia por Regex **não** comprova experiência, certificação ou aderência a edital.
- A simulação de equipes utiliza sete perfis artificiais, separados dos currículos, com três funções.
- Integrações com Drive, Jira e SharePoint, validação documental automática e atualização por eventos são propostas e não estão implementadas.
- A duração varia conforme máquina, sistema, carga e execução. Não substitua registros anteriores por resultados novos sem identificá-los.

## Cuidados antes de publicar

Os currículos foram apresentados como fictícios, mas têm nomes e contatos de exemplo. Confirme com o responsável pelo desafio se podem ser disponibilizados em repositório público. Caso contrário, mantenha `data/curriculos_raw/` em repositório privado. O banco gerado está em `saida/`, excluído pelo `.gitignore`.

O regulamento do evento e as políticas institucionais devem ser observados na divulgação dos códigos e dos dados.
