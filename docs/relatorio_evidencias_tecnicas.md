# Relatório de evidências: ingestão independente de currículos — HackatOhm 2026

## A. Método e escopo

**Data:** 27/10/2026. **Entrada:** base `curriculos_raw.zip` fornecida pela EloGroup e encaminhada pela equipe, 228 currículos DOCX. **Independência:** a análise executada aqui não usa o protótipo anterior. **Processo:** ler ZIP → interpretar XML/DOCX (inclusive tabelas e cabeçalhos) → reconhecer padrões e normalizar aliases de tecnologias → gravar SQLite → executar consultas e ensaio de busca. Três execuções completas para temporização. O código e os JSONs acompanham este relatório. Na versão organizada para o repositório, os DOCX estão em `data/curriculos_raw/` e os scripts em `src/experimentos/`.

## B. Resultados realmente observados

| Medida | Observação |
|---|---:|
| Currículos tentados / legíveis | 228 / 228 |
| Erros técnicos | 0 |
| Textos extraídos | 4,393,921 caracteres |
| Documentos com tabelas | 153 (67.1%) |
| Tabelas encontradas | 290 |
| Duplicações binárias adicionais | 0 |
| Duplicações de texto normalizado adicionais | 2 (dois pares) |
| Currículos com ao menos 1 competência do vocabulário | 227 |
| Competências normalizadas encontradas | 49 das 51 mapeadas |
| Pares documento–competência, identificados por texto | 3713 |
| Menções textuais a PMP | 30 (consulta SQL separada) |
| Certificações efetivamente validadas | **0 (não houve validação documental neste ensaio)** |

**Detecções de campos, não provas:** 194 currículos contêm padrão de e-mail; 171 têm padrão de telefone; 183 apresentam palavra relacionada à formação; 108 contêm uma possível alegação numérica de tempo de experiência nos primeiros 4.000 caracteres.

### Tempos para o lote (medianas de 3 execuções)

| Medida | Tempo |
|---|---:|
| Leitura ZIP | 0.080 s |
| Extração DOCX/XML | 2.726 s |
| Identificação com Regex | 5.341 s |
| Montagem/índices/inserção SQLite | 0.030 s |
| **Ciclo completo incluindo despesas adicionais** | **8.492 s** |
| Custo médio de ciclo por documento no lote | 37.25 ms |

Variação observada do ciclo: 8.382–9.294 segundos. Tempos de diferentes estágios incluem pequenas despesas de instrumentação e podem não somar ao total.

### Comparação de pesquisa sobre os mesmos conteúdos já extraídos

| Consulta de competência | Mediana |
|---|---:|
| Busca linear sobre 228 textos em memória | 101.876 ms |
| Busca SQLite com índice preexistente | 0.362 ms |
| **Razão das medianas (linear / indexada)** | **281.6×** |

Foram pesquisadas 10 competências, com 50 consultas por método. Quantidades de resultados foram iguais nas 50 comparações (`all_results_equal=true`). **Esta comparação mede latência de consultas automatizadas, não horas dos gestores; o pré-processamento da base está fora do tempo individual de consulta.**

### Menções mais frequentes — não são qualificações confirmadas

| Competência | Documentos com menção |
|---|---:|
| SQL | 216 |
| Scrum | 199 |
| Jira | 187 |
| Java | 186 |
| HTML | 175 |
| SQL Server | 171 |
| JavaScript | 159 |
| Oracle Database | 150 |
| CSS | 147 |
| Linux | 140 |

## C. Demonstração com perfis simulados, separada dos currículos DOCX

Uma simulação de apenas 7 perfis fictícios para 3 funções encontrou 13 composições viáveis entre 210 arranjos sem repetição. A equipe escolhida teve **28 pontos sob regras ilustrativas**, com requisitos obrigatórios, disponibilidade e **apenas fatos marcados como documentalmente comprovados na simulação**. O script `src/experimentos/simular_equipe.py` demonstra lógica de elegibilidade e composição, mas NÃO avalia editais verdadeiros nem comprova a experiência das pessoas da base enviada.

## D. Esforço atual informado e impacto ainda não medido

O case informa aproximadamente 40 disputas anuais e 15–25 **horas conjuntas**
de dois a três gestores por disputa: **600–1.000 horas-pessoa por ano**, sem
multiplicar novamente pelo número de pessoas. Esses números descrevem a
premissa do problema apresentada no desafio, ainda sujeita a validação com
a empresa.

A expectativa é tornar rápidas as três primeiras etapas do fluxo:
**localizar profissionais, encontrar comprovações e consultar disponibilidade**.
Com a base alimentada e atualizada, perfis indexados, documentos vinculados
às competências e informações de disponibilidade poderão ser recuperados
por consulta. Isso reduz buscas em pastas, pedidos repetidos de documentos
e contatos manuais, concentrando a atuação do gestor na formação da equipe
e na revisão da proposta.

## E. Revisão crítica da proposta inicial (o que mudaria)

1. **Manter os dois processos ligados:** alimentação confiável da base e uso por edital. É a principal aderência ao case, e mais importante do que empilhar tecnologias.
2. **Reduzir as afirmações do piloto:** a leitura já foi comprovada; extração semântica, precisão de cargos/empresas/datas e checagem de diplomas **não** foram comprovadas. Evitar chamar a base de 100% correta.
3. **Tratar comprovação como entidade independente do currículo:** declaração, evidência recebida, validação humana, validade e aceitação específica no edital são etapas distintas. Nem uma regex nem um LLM verificam automaticamente a autenticidade de um atestado.
4. **Não confundir quantidade de palavras técnicas com mérito:** currículos extensos, com tabelas de tecnologias, podem mencionar muitos termos sem demonstrar nível, período, projeto ou atividade. O vocabulário atual é útil para **busca inicial**, não para ranquear profissional automaticamente.
5. **Deixar gamificação como mecanismo de manutenção, não eixo central:** medir o esforço de atualização e revisão; começar por eventos como conclusão de projetos ou renovação de certificações, com lembretes seletivos.
6. **Concentrar o pitch numa história verificável:** arquivo importado → dados estruturados → busca → requisito do edital → evidência/status → sugestão fictícia de equipe → impacto por hipótese transparente.
7. **Privacidade:** a base contém padrões de contatos pessoais. O banco entregue é pseudonimizado e as métricas públicas são agregadas; ao publicar em GitHub público, confirmar se os arquivos fictícios podem ser redistribuídos e se contatos não correspondem a terceiros reais; não divulgar hashes ou registros individuais sem conferir o contexto.
8. **Diversidade de fontes:** a amostra tem somente DOCX, em grande parte currículos técnicos em inglês. Não generalizar os resultados para PDFs digitalizados, currículos em português ou o acervo de uma empresa real.
9. **Pontuação por edital e função:** não somar automaticamente pontos pertencentes a papéis diferentes; separar restrições obrigatórias, limites, disponibilidade, não acúmulo e evidências. Uma heurística de pessoas isoladas pode falhar ao compor a equipe.
10. **Dados de mercado e impacto:** horas e volume são premissas do case; ganho de vitória, faturamento e taxa de sucesso não foram observados. Manter resultados efetivos separados de projeções.

## G. Organização do fluxo e responsabilidades

O ganho esperado decorre da reutilização de informações já estruturadas:
a localização de profissionais consulta os perfis indexados; a busca de
comprovações recupera documentos vinculados às competências e sua vigência;
a consulta de disponibilidade usa as informações atualizadas dos sistemas
corporativos integrados.

As duas etapas seguintes — formar equipes e preparar a proposta — usam
esses dados organizados, com supervisão do gestor e revisão documental.
Essa divisão concentra o trabalho humano na análise e na decisão.

### Implantação e responsabilidades

1. Importar uma amostra e revisar a extração, a identidade e as evidências, com RH.
2. Testar uma demanda com comercial e gestores; confirmar disponibilidade e
   requisitos antes da recomendação. O colaborador atualiza seus fatos e
   comprovantes; um revisor designado confere o documento e sua validade.
3. Expandir a base e conectar RH, projetos e treinamento somente após validar
   permissões, finalidade, retenção e custo operacional. Acesso por função,
   auditoria e correção de dados são condições para trabalhar com PII real.

O ranking cultural reconhece contribuições validadas e não altera a nota
técnica do edital. Dados declarados não devem ser apresentados como
comprovação; campos ausentes e conflitos permanecem visíveis.
