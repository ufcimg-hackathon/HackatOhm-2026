# Bid Room

Plataforma de gestão inteligente de currículos para disputas comerciais.
O Bid Room é o módulo de decisão da plataforma. MVP A — vertical slice funcional
(preset "TI para o setor de energia", 228 currículos ingeridos, 24–30 curados, 5 alocados).

> Frase-guia: **extrair uma vez, pontuar muitas vezes.**

## Setup (Fase 0)

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 1) Converter o dump de links do Drive no CSV de controle
.venv\Scripts\python scripts\inventario.py <caminho_do_dump> --out data/inventario_curriculos.csv

# 2) Baixar os 228 currículos para o cache local
.venv\Scripts\python scripts\cache_drive.py --csv data/inventario_curriculos.csv

# 3) Rodar a aplicação
.venv\Scripts\streamlit run app.py

# Testes
.venv\Scripts\python -m pytest -q
```

## Pipeline local (Fases 1–3)

```bash
# banco + preset v1 publicado (pesos e gates G1–G6)
.venv\Scripts\python scripts\build_db.py --db data\demo.db

# ingestão/extração dos 228 currículos (idempotente por checksum)
.venv\Scripts\python scripts\ingest.py --db data\demo.db --csv data\inventario_curriculos.csv

# score dos candidatos em todos os papéis da disputa demo
.venv\Scripts\python scripts\score.py --db data\demo.db
```

O motor lê pesos e gates **da versão publicada do preset**, não do código:

```text
pontuacao_ponderada = Σ (pontuacao_categoria × peso) / 100
confianca           = Σ (nivel_evidencia_categoria × peso) / 100
pontuacao_final     = pontuacao_ponderada × (0,6 + 0,4 × confianca / 100)
```

Cada pontuação guarda `detalhe_json` com categorias, gates, âncoras do PM e
anos de experiência — a UI da Fase 4 renderiza esse recibo sem recalcular a
nota a cada rerun.

## Telas T1–T4 (Fase 4)

```bash
.venv\Scripts\streamlit run app.py
```

- **T1 — Dashboard:** funil currículos → curadoria → alocação, status por
  `status_curacao`, faixa de score e última ingestão, lidos do banco.
- **T2 — Bid Room:** ranking por papel, filtros de triagem/evidência/score e
  colunas de explicabilidade; clique na linha abre a ficha. Quando a disputa
  está vinculada a um edital ativo, o ranking usa a nota técnica dinâmica
  (Etapa 5); sem edital, usa o preset fixo. O ranking usa `st.cache_data` e é
  invalidado após upload/alocação.
- **T3 — Perfil:** resumo pronto para proposta por regras, categorias e gates,
  fatos com trecho/nível/página de evidência, PII sempre `***` e histórico de
  versões. Em demanda com edital, mostra a nota técnica e o motivo de cada
  requisito (atendido/pendente/não atendido), separando apoio interno,
  confiança e atualidade. O upload de um `.docx` cria uma **nova versão do
  mesmo candidato** (checksum idempotente), audita `atualizar_curriculo` e
  recalcula a disputa ativa sem apagar a pontuação antiga.
- **T4 — Alocação:** 5 vagas (1 PM, 1 BA, 2 Java, 1 BI), sugestão pelo topo do
  ranking, bloqueio de candidato em duas vagas, persistência em `alocacoes` e
  score do time com bônus de cobertura e penalidades do doc §10.5. Para disputa
  com edital, a tela monta a composição por requisitos (Etapa 6), mostra a
  recomendação automática, requisitos coletivos, alternativas e riscos, permite
  revisão pelo gestor e exporta a equipe em CSV. Candidatos pendentes ou
  eliminados não aparecem como opção de vaga.

## Telas T5–T7 (Fases 5–6, Etapas 4–7)

### T5 — Editor de preset, preview, publicar, ativar e fazer rollback

- **Página Preset:** permite criar uma nova versão do preset, ajustar pesos por categoria,
  gates e piso de confiança. O save publica uma nova versão e recalcula as pontuações
  da disputa ativa.
- **Preview top 10:** compara a versão publicada vs o rascunho; mostra contagens de
  elegíveis/revisar/eliminados antes e depois; lista os candidatos que entraram/sairam
  do top; candidatos sem score na versão base aparecem como "novos" com `score_atual: None`.
- **Gates G1–G6:** configuráveis na versão do preset; G5 = "Disponibilidade => revisar";
  G2 = anos mínimos totais (proxy; não tempo no papel).
- **Publicar:** nova versão `publicada`, anterior `deprecada`, disputa repontada,
  scores recalculados via `recalcular_disputa`. `rollback_preset` retorna
  `recalculou=True` se precisar recalcular do alvo, `False` se scores já existem.
- **Manual:** na página Preset com `BIDROOM_DB=data/demo-t5.db`; verificar preview,
  salvar/publicar e rollback; conferir auditoria.

### T6 — Governança/LGPD

- Auditoria de leitura no cofre (`CofrePII`); política de retenção; máscaras PII
  nas listas; nenhum atributo protegido no score final. A aba
  **Reconhecimento** concentra programas, metas, conquistas, comprovações e o
  ranking cultural da Etapa 7.

### T7 — Edital e demanda (Etapa 4)

- **Página Edital:** recebe o edital original em `.docx`, cria uma versão
  imutável e extrai requisitos por regras offline.
- Cada requisito preserva **trecho, localização, página estimada, escopo
  (individual/equipe/empresa) e regra interpretada em JSON**. Ambiguidades
  ficam explícitas e bloqueiam a aprovação até a revisão humana.
- A revisão registra revisor, decisão, observação e preserva a interpretação
  original quando o responsável altera a regra. Requisito obrigatório rejeitado
  também bloqueia a ativação.
- **Retificação** cria nova versão, calcula o efeito (adicionados/removidos/
  alterados) e mantém a versão anterior consultável. Ao ativar, a anterior vira
  `substituida`, a demanda vinculada aponta para a nova versão e uma pendência
  explícita é criada para o time revisar a decisão anterior.
- Critérios de empresa/equipe têm escopo próprio e `pontua = false`; não viram
  pontos individuais. A cobertura mostra quando há papéis, vagas e critérios
  suficientes para substituir o preset fixo.
- Armazenamento local em `data/editais` (ou `BIDROOM_EDITAIS`). Anexos também
  aceitam `.docx` no MVP; documento sem texto extraível gera tratamento
  explícito para OCR/revisão manual.

### Etapa 5 — pontuação dinâmica da demanda

- A versão **aprovada/ativa** do edital é a fonte da nota. Cada requisito
  aprovado gera um resultado explícito com status, motivo, pontos, confiança,
  atualidade e fatos de origem.
- `pontuacoes_demanda` guarda `nota_tecnica`, `score_interno_apoio`,
  `confianca`, `atualidade`, `status` e `detalhe_json`, por demanda, candidato,
  papel, versão do edital e versão do currículo. Versões antigas continuam
  consultáveis e não são sobrescritas.
- A **nota técnica** é a soma dos pontos previstos no edital; ela não é
  normalizada para 100. O **apoio interno** é
  `50% cobertura de obrigatórios + 30% confiança + 20% atualidade`, um sinal
  heurístico que nunca substitui a nota do edital. **Confiança** não é
  probabilidade estatística.
- Requisitos **obrigatórios não atendidos** eliminam; obrigatórios pendentes
  deixam o candidato em revisão. Requisitos de **equipe/empresa** ficam em
  `coletivos` e não viram pontos individuais.
- Critérios sem fato estruturado (por exemplo, disponibilidade para viagens)
  ficam **pendentes** e aparecem no T3/T7. Na revisão do edital, o responsável
  pode usar **Alterar interpretação** e incluir
  `"resultado": "atendido"`, `"nao_atendido"` ou `"pendente"` na regra JSON
  para registrar a decisão humana.
- Frescura e comprovação usam `BIDROOM_FRESHNESS_ATENCAO_DIAS` (padrão 180) e
  `BIDROOM_FRESHNESS_DESATUALIZADO_DIAS` (padrão 365). Fato vencido ou sem
  confirmação não é tratado como atual.
- O ranking dinâmico pode ser recalculado em T2; a ficha T3 explica cada
  requisito. A alocação de time para demandas com edital usa a composição da
  Etapa 6.

### Etapa 6 — composição viável do time

- A recomendação resolve uma atribuição com **vagas por papel**,
  **exclusividade de candidato**, **limites por papel** e **requisitos
  coletivos** de equipe/empresa. O objetivo é explícito:

  ```text
  objetivo = Σ nota_técnica
           + 10 × requisito coletivo atendido
           - 10 × requisito coletivo não atendido
  ```

- Apenas candidatos `elegivel` preenchem vagas automaticamente. Pendentes e
  eliminados aparecem como pendência/risco, nunca como integrante silencioso.
  Candidato já alocado em outra disputa aberta é excluído e gera conflito
  visível; o salvamento também rejeita essa seleção.
- Requisitos coletivos de certificação/competência (por exemplo, “pelo menos
  um PMP e um CBAP”) são avaliados sobre a equipe, não somados como pontos
  individuais. Requisitos de empresa ou regras sem fato estruturado ficam em
  **revisão manual**, explicitamente.
- A tela T4 mostra a **gulosa** e a **solução otimizada**, com a diferença de
  objetivo. A busca é branch-and-bound; para manter a demo responsiva, o pool
  por papel é limitado a 14 candidatos e a busca a 200 mil nós. Se a busca for
  truncada, a tela avisa que a solução é a melhor encontrada, não uma prova de
  ótimo global.
- O gestor pode trocar integrantes; cobertura, requisitos coletivos, riscos e
  score são reavaliados a cada troca. A composição pode ser exportada em CSV
  (sem PII). Alterações de disponibilidade, evidência ou nota mudam a
  recomendação na próxima leitura; o salvamento valida a situação vigente.
- Limitações: o parser coletivo reconhece padrões de certificação/competência
  e “pelo menos N”; regras compostas fora desses padrões ficam pendentes para
  revisão humana. A empresa não é avaliada pelo acervo individual.

### Etapa 7 — cultura, metas e reconhecimento

- `programas_reconhecimento` publica nome, ano, regra, critério de desempate e
  política de visibilidade (`agregado`, `publico` ou `privado`).
- `conquistas` liga uma certificação concluída a um programa/ano. A chave
  `(candidato, certificação, programa, ano)` impede que reenvios do mesmo
  comprovante inflem a contagem. A conquista só conta no ranking quando está
  `validada` e a comprovação continua válida.
- **Conquista ≠ atualização documental:** o ano vem de `data_conclusao` (ou do
  `ano` legado). Um certificado concluído em 2025 e comprovado em 2026 continua
  sendo conquista de 2025. Renovação/rejeição/revogação são estados separados.
- `metas_desenvolvimento` conecta metas, certificação alvo e prazo às
  pendências reais do colaborador. A aba Reconhecimento mostra conquistas,
  comprovações faltantes, validade, metas e próximos passos.
- O ranking anual é calculado por certificações distintas validadas, com
  desempate publicado (`data_conclusao_mais_recente`, `nome` ou quantidade de
  certificações). A regra aparece na tela.
- **O ranking cultural não altera elegibilidade, nota da demanda ou alocação.**
  Ele é uma leitura de reconhecimento; rejeição, vencimento ou revogação
  corrigem a posição.
- Limitações: o MVP não tem login, então a visibilidade é uma política
  descritiva e a tela é administrativa. A contagem usa o ano civil; datas
  completas vêm de `data_conclusao`, e currículos antigos só com `ano` usam
  31/12 como limite conservador para desempate.

### Etapa 8 — validação integrada, métricas e performance

- `tests/test_etapa8.py` executa a jornada: perfil → comprovante validado →
  edital processado/revisado/ativado → ranking → composição → mudança de
  disponibilidade → nova recomendação. Também cobre duas conexões SQLite
  disputando a mesma alocação ativa.
- O carregamento de contexto foi trocado de três varreduras globais por
  candidato (`O(N × (E + A + D))`) para uma carga em lote
  (`O(E + A + D + N × fatos_do_curriculo)`). O caminho é usado no ranking
  dinâmico, preview, score legado e composição.
- Foram adicionados índices para currículo/versão, alocações, evidências
  documentais, pontuações da demanda e reconhecimento.
- Medições reproduzíveis:
  `.venv\Scripts\python scripts\benchmark_etapa8.py --repeticoes 1 --volumes 50,100,200`
  e `.venv\Scripts\python scripts\metricas_etapa8.py`.
- Resultados, metodologia, Big O e limitações estão em
  [`docs/metricas_etapa8.md`](docs/metricas_etapa8.md). O roteiro de
  apresentação está em [`docs/roteiro_demo_etapa8.md`](docs/roteiro_demo_etapa8.md).
- **Resultados medidos** não incluem ganho de produtividade de um gestor real,
  precisão estatística em editais reais nem carga de produção. O MVP não tem
  autenticação/RBAC e não está apto a um piloto com PII real.

### T8 — Demo/Memo/Pitch (próxima fase)

- Pra deck final do hackathon; estrutura e conteúdo a definir.

## Arquitetura

```
app.py              # UI Streamlit
presets/            # configurações versionadas de edital (YAML)
scripts/            # cache_drive, inventario, build_db, ingest, score, seed_demo,
                    # benchmark_etapa8, metricas_etapa8
src/
  ingestion/        # leitura do cache, checksum, versionamento
  extraction/       # parser .docx, regras, dedupe de fatos, evidência
  scoring/          # preset legado, pontuação dinâmica da demanda e score de time
  ui/               # telas streamlit T1–T7 (T8 nas próximas fases)
  governance/       # PII, auditoria, ciclo, editais, comprovação e reconhecimento
data/               # NÃO versionada (cache, CSV de controle, banco local)
tests/
```

## Convenções

- **DOMÍNIO EM PORTUGUÊS, INFRAESTRUTURA EM INGLÊS:** tabelas/colunas sem acento
  (`candidatos`, `evidencias`, `pontuacoes`); código/infra em inglês.
- UI, comentários e logs em **português**.
- `ENABLE_LLM_EXTRACTION=false` por padrão — a demo roda 100% offline por regras.

## Limitações (MVP A)

- Sem auth/SSO real, sem multi-tenant, sem integrações (ATS/HRIS/e-mail).
- Sem scraping (LinkedIn/fontes externas); extração offline-first por regras.
- Segundo preset (TI-Financeiro) é stretch, não MVP.
- A composição coletiva da Etapa 6 é offline e baseada em regras; requisitos de
  empresa e regras coletivas fora dos padrões reconhecidos exigem revisão
  humana. O ótimo é provado apenas dentro do pool limitado por papel.

## LGPD e governança

- PII em cofre separado (`cofre_dados_pessoais`), redaction na exibição.
- Minimização no score: gênero, idade e nacionalidade não entram na nota.
- Atualização versionada de currículo por checksum, idempotente, com auditoria
  (`registros_auditoria`) e score antigo sempre reproduzível
  (`curriculo_versao_id` + `versao_preset_id` no preset, ou
  `curriculo_versao_id` + `edital_versao_id` em `pontuacoes_demanda`).
- **Não prometemos sincronização automática; garantimos versão, dono, idade
  visível e auditoria.**

## Futuro público

Repositório privado durante o hackathon; sanitizar dados e segredos antes de
qualquer abertura; licença MIT só depois da sanitização.
