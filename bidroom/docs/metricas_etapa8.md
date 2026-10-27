# Métricas e validação — Etapa 8

Data da medição: 2026-10-27  
Ambiente: Windows, Python 3.13.14, SQLite 3.50.4, banco sintético em diretório
temporário. `data/demo.db` não foi usado nem alterado.

## O que foi medido

### 1. Carregamento de contexto: antes vs. depois

O caminho antigo chamava `carregar_contexto` uma vez por candidato; cada chamada
varria `evidencias`, `atualidade_fatos` e `evidencias_documentais` inteiras.
O caminho novo usa `carregar_contextos`: as três tabelas globais são lidas uma
vez e os fatos de cada currículo são resolvidos por candidato.

Cada candidato sintético tem 3 certificações, 3 linhas em `evidencias`, 3 em
`atualidade_fatos` e 3 em `evidencias_documentais`.

| Candidatos | Fatos/candidato | Registros globais | Antes (ms) | Lote (ms) | Speedup | Pico antes (KB) | Pico lote (KB) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 50 | 3 | 450 | 1.311,9 | 288,8 | 4,54x | 482,4 | 444,5 |
| 100 | 3 | 900 | 6.806,5 | 519,8 | 13,09x | 931,0 | 880,4 |
| 200 | 3 | 1.800 | 25.896,9 | 1.123,4 | 23,05x | 1.907,4 | 1.793,3 |

- 50 e 100: mediana de 3 repetições; 200: uma repetição (o caminho antigo é
  lento e o objetivo era demonstrar a tendência, não produzir um SLA).
- “Antes” é o padrão de chamada antigo executado com o código atual; não é uma
  medição histórica de um commit anterior.
- O pico de memória vem de `tracemalloc`, não do RSS do processo.
- A máquina não estava isolada; os valores absolutos variam. A tendência de
  crescimento e o speedup relativo são o resultado relevante.

**Big O:**

```text
antes:  O(N × (E + A + D))
depois: O(E + A + D + N × fatos_do_curriculo)
```

Onde `N` = candidatos, `E` = linhas em `evidencias`, `A` = linhas em
`atualidade_fatos`, `D` = linhas em `evidencias_documentais`.

### 2. Índices adicionados

- `curriculos(candidato_id, versao)`
- `alocacoes(candidato_id, status, disputa_id)`
- `alocacoes(disputa_id, status)`
- `evidencias_documentais(tipo_fato, fato_id, status, valido_ate)`
- `pontuacoes_demanda(disputa_id, edital_versao_id, candidato_id)`
- `conquistas(programa_id, ano_referencia, status)`
- `conquistas(candidato_id)`
- `metas_desenvolvimento(candidato_id, status)`
- `programas_reconhecimento(ano, ativo)`

### 3. Jornada integrada

O teste `tests/test_etapa8.py` executa:

1. criação de dois perfis com certificação PMP e disponibilidade confirmada;
2. envio e validação de comprovante pelo fluxo real de comprovação;
3. processamento de um `.docx` de edital, revisão e ativação;
4. cálculo do ranking dinâmico e recomendação de equipe;
5. alteração posterior da disponibilidade de um candidato;
6. recálculo e nova recomendação, que passa a escolher o outro candidato;
7. verificação de que as ações foram auditadas.

Resultado observado: **3 testes da Etapa 8 passaram** (jornada, equivalência
do carregamento em lote e unicidade de alocação entre duas conexões). A suíte
completa fechou com **144 testes passando**. O tempo local da jornada foi de aproximadamente 1,4 s, incluindo
criação do banco temporário e extração do `.docx`; esse número não é um SLA.

### 4. Métricas da amostra controlada

`scripts/metricas_etapa8.py` cria 2 perfis, 1 edital, 1 vaga e mede o caminho
real. Execução observada:

| Métrica | Valor medido |
|---|---:|
| Anexar comprovante | 28,1 ms |
| Validar comprovante | 20,1 ms |
| Recalcular ranking | 22,7 ms |
| Obter composição | 12,2 ms |
| Pontuações recalculadas | 2 |
| Objetivo automático | 10,00 |
| Objetivo da gulosa (baseline humano simples) | 10,00 |
| Diferença automático − gulosa | +0,00 |
| Fatos de Ana por status | `{'atualizado': 1}` |
| Pendências de comprovação | 1 |
| Cobertura da composição | 100% |

A amostra é pequena e controlada. Não é uma estimativa de precisão em editais
reais nem de tempo economizado por um gestor humano.

### 6. Comparação automática vs. baseline humano simples

- Na amostra de 1 vaga acima, a composição automática e a gulosa chegaram ao
  mesmo objetivo (10,00): não houve ganho a demonstrar.
- No cenário controlado de 2 papéis e requisitos coletivos da Etapa 6, o teste
  automatizado registra **ótimo 190,00 vs. gulosa 160,00** (diferença 30,00),
  porque a gulosa coloca o melhor candidato no primeiro papel e perde a
  combinação coletiva.
- Isso compara o otimizador com uma heurística, não com um estudo de tempo ou
  de decisão de gestores humanos.

### 5. Precisão da extração do edital

No edital sintético usado na jornada, o parser identificou 1 papel, 1
certificação obrigatória, 1 período e 1 regra coletiva; os testes da Etapa 4
verificam um edital maior com 4 papéis e vagas esperadas. Isso é uma
**verificação de casos controlados**, não uma medição estatística de precisão.
O parser continua baseado em regras e pode exigir revisão humana.

## Validação de falhas, concorrência, auditoria e permissões

- **Concorrência:** duas conexões SQLite não conseguem criar duas alocações
  ativas do mesmo candidato na mesma disputa; o índice único é a última defesa.
  O teste usa duas conexões independentes.
- **Viabilidade no serviço:** `salvar_alocacao` também rejeita requisito
  coletivo pendente/não atendido e excesso de limite por papel; a tela não é a
  única barreira. O teste da Etapa 6 cobre o limite e o salvamento válido.
- **Recuperação de falhas:** a fila de atualizações pendentes já é testada na
  Etapa 2; falha de recálculo permanece visível com erro e não marca a pendência
  como resolvida.
- **Auditoria:** a jornada verifica `anexar_evidencia`, `validar_evidencia`,
  `receber_edital_versao`, `ativar_edital_versao`,
  `atualizar_disponibilidade` e `calcular_pontuacoes_demanda`.
- **Permissões:** o MVP **não tem autenticação nem autorização**. A política de
  visibilidade do reconhecimento é descritiva. Isso impede um piloto com PII
  real; é uma limitação explícita, não uma pendência escondida.

## O que é medido e o que é projetado

**Medido:**

- custo de carregar contexto em 50/100/200 candidatos sintéticos;
- tempo de anexar/validar comprovante, recalcular ranking e recompor equipe em
  uma amostra pequena;
- mudança efetiva da recomendação após alteração de disponibilidade;
- existência de auditoria e invariantes de alocação.

**Projetado, não medido:**

- redução do tempo de um gestor humano para montar uma equipe;
- ganho de precisão em editais reais;
- comportamento sob vários usuários simultâneos ou carga de produção;
- benefício financeiro/comercial do uso do produto.

Esses pontos não devem ser apresentados como resultados comprovados.

## Reprodução

```powershell
.venv\Scripts\python -m pytest tests\test_etapa8.py -q
.venv\Scripts\python scripts\benchmark_etapa8.py --repeticoes 1 --volumes 50,100,200
.venv\Scripts\python scripts\metricas_etapa8.py
```

## Falhas e lacunas restantes

- Sem piloto com usuários representativos; os dados são sintéticos.
- Sem autenticação/RBAC; não apto a PII real.
- SQLite é single-node; concorrência testada no nível de invariantes, não sob
  carga.
- Extração de edital é rule-based; requisitos ambíguos continuam indo para
  revisão humana.
- A composição usa pool limitado por papel (14) e limite de busca (200 mil nós);
  o resultado é o melhor encontrado, não uma prova de ótimo global em todos os
  casos.
- O benchmark cobre o carregamento de contexto; não é um benchmark completo de
  todas as telas Streamlit.
