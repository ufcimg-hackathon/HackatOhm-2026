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
  colunas de explicabilidade; clique na linha abre a ficha. O ranking usa
  `st.cache_data` e é invalidado após upload/alocação.
- **T3 — Perfil:** resumo pronto para proposta por regras, categorias e gates,
  fatos com trecho/nível/página de evidência, PII sempre `***` e histórico de
  versões. O upload de um `.docx` cria uma **nova versão do mesmo candidato**
  (checksum idempotente), audita `atualizar_curriculo` e recalcula a disputa
  ativa sem apagar a pontuação antiga.
- **T4 — Alocação:** 5 vagas (1 PM, 1 BA, 2 Java, 1 BI), sugestão pelo topo do
  ranking, bloqueio de candidato em duas vagas, persistência em `alocacoes` e
  score do time com bônus de cobertura e penalidades do doc §10.5.

A curadoria dos 24–30 é uma lista humana externa: a demo mostra 0 até o CSV do

## Telas T5–T6 (Fases 5–6)

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

### T6 — Governança/LGPD (próxima fase)

- Auditoría de leitura no cofre (`CofrePII`); política de retenção; máscaras PII
  nas listas; nenhum atributo protegido no score final.

### T7 — Demo/Memo/Pitch (próxima fase)

- Pra deck final do hackathon; estrutura e conteúdo a definir.

```bash
.venv\Scripts\streamlit run app.py
```
   (com `BIDROOM_DB=data/demo-t5.db` para testar a T5).

time ser aplicado na Fase 8. O Build fornece o mecanismo, não inventa a lista.

## Arquitetura

```
app.py              # UI Streamlit
presets/            # configurações versionadas de edital (YAML)
scripts/            # cache_drive, inventario, build_db, ingest, score, seed_demo
src/
  ingestion/        # leitura do cache, checksum, versionamento
  extraction/       # parser .docx, regras, dedupe de fatos, evidência
  scoring/          # gates G1–G6, pesos, confiança, score de time
  ui/               # telas streamlit T1–T4 (T5–T6 nas próximas fases)
  governance/       # PII vault, redaction, auditoria
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

## LGPD e governança

- PII em cofre separado (`cofre_dados_pessoais`), redaction na exibição.
- Minimização no score: gênero, idade e nacionalidade não entram na nota.
- Atualização versionada de currículo por checksum, idempotente, com auditoria
  (`registros_auditoria`) e score antigo sempre reproduzível
  (`curriculo_versao_id` + `versao_preset_id`).
- **Não prometemos sincronização automática; garantimos versão, dono, idade
  visível e auditoria.**

## Futuro público

Repositório privado durante o hackathon; sanitizar dados e segredos antes de
qualquer abertura; licença MIT só depois da sanitização.
