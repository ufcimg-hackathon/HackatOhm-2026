# Roteiro de demonstração — Etapa 8

Objetivo: preparar a jornada completa com dados sintéticos. Este ensaio é
material de apoio; o vídeo final segue o [roteiro de até cinco minutos](../../../docs/roteiro-pitch.md).
No pitch, usar apenas uma sequência curta das telas abaixo.

## Antes de começar

Ative o ambiente da raiz e execute os comandos dentro de `src/bidroom/`.

```powershell
# suíte completa
python -m pytest -q

# validação integrada específica
python -m pytest tests/test_etapa8.py -q

# benchmark de carregamento (opcional; não usa data/demo.db)
python scripts/benchmark_etapa8.py --repeticoes 1 --volumes 50,100,200
```

Use um banco temporário ou de demonstração separado. **Não** use
`data/demo.db` para a jornada de escrita: ele é a base de referência e deve
permanecer intacto.

## Ensaio técnico detalhado (8–10 minutos)

### 1. Perfil e evidência (2 min)

1. Abra **Perfil** e escolha um candidato.
2. Mostre o fato “PMP” como `declarado` e o painel de comprovação.
3. Envie um `.docx` de comprovante e valide como revisor.
4. Mostre que o estado muda para `validado`, a validade aparece e a ação foi
   auditada.
5. Reenvie o mesmo arquivo e mostre que **não** cria outra certificação.

Mensagem: declaração, envio e validação são estados diferentes.

### 2. Edital (2 min)

1. Abra **Edital**, crie um edital fictício e envie um `.docx`.
2. Mostre cada requisito com trecho, localização, página, regra interpretada e
   ambiguidade.
3. Aprove os requisitos, aprove a versão e ative.
4. Mostre que a ativação cria a disputa e que a versão fica imutável; uma
   retificação criaria outra versão.

Mensagem: a extração é rastreável e a revisão humana acontece antes da
classificação.

### 3. Ranking e composição (2–3 min)

1. Abra **Bid Room** e mostre a nota técnica dinâmica, separada de apoio
   interno, confiança e atualidade.
2. Abra **Alocação** e mostre:
   - recomendação automática;
   - objetivo, nota técnica, bônus/penalidade coletiva e cobertura;
   - requisitos coletivos e pendências;
   - alternativas por vaga e riscos.
3. Troque um integrante e mostre a reavaliação imediata.
4. Exporte o CSV da composição.

Mensagem: a recomendação é explicável e o gestor continua no controle.

### 4. Mudança posterior (1–2 min)

1. Altere a disponibilidade de um integrante para 0% (ou rejeite uma evidência).
2. Recalcule a pendência/ranking.
3. Volte à **Alocação** e mostre que a recomendação mudou ou sinalizou
   pendência; não há duplicação nem conflito silencioso.
4. Tente salvar uma alocação inválida e mostre a rejeição.

Mensagem: o sistema reage a dados desatualizados; a decisão anterior não é
sobrescrita silenciosamente.

### 5. Reconhecimento (1 min)

1. Abra **Governança → Reconhecimento**.
2. Mostre o programa, a regra de contagem e o desempate.
3. Sincronize conquistas e mostre o ranking anual.
4. Explique que uma certificação concluída em 2025 e comprovada em 2026 conta
   em 2025.
5. Rejeite/revogue uma conquista e mostre a correção da posição.
6. Reforce: **o ranking cultural não altera elegibilidade, nota ou alocação.**

### 6. Fechamento (1 min)

Mostre:

- `docs/metricas_etapa8.md` — o que foi medido e o que é projetado;
- testes da jornada;
- limitações: sem piloto real, sem autenticação/RBAC, SQLite single-node,
  parser rule-based, pool limitado da composição.

## Limitações que devem ser ditas

- **Não é um piloto com PII real.** O MVP não tem autenticação/autorização.
- Os números de tempo vêm de banco sintético e ambiente local; não são SLA.
- A extração de edital é rule-based e exige revisão humana.
- O benchmark mede o carregamento de contexto; não mede todas as telas.
- A composição pode ser a melhor encontrada dentro do pool/limite de busca, não
  uma prova de ótimo global.
- O ganho de produtividade para um gestor humano **não foi medido**; é uma
  hipótese a validar em piloto.
