# HackatOhm 2026 — Elogroup

Projeto Bid Room (sala de decisões): Pipeline de extração e ranking de currículos com Python/Streamlit.

O Bid Room é um sistema Python/Streamlit que estrutura dados de currículos .docx em um banco SQLite governado, aplicando uma taxonomia fechada de competências, experiências, certificações, formações e idiomas. Detecta e cifra PII (e-mail, telefone, endereço, CEP), versiona currículos por candidato, e oferece dashboards para alocação de talentos contra editais de vagas. O motor de scoring usa pesos e gatilhos configuráveis em presets; a composição de equipes respeita limites por papel. O pipeline aceita três fontes de entrada: carga em lote, sincronização da pasta data/inbox/ ou upload direto pela interface. As decisões de design priorizam rastreabilidade e idempotência sobre automação avançada: não há LLM, a extração é determinística.

## Links

- [Regulamento](docs/regulamento-hackatohm-2026.pdf)
- [Guia de entrega e vídeo pitch](docs/Guia_entrega_video_HackatOhm2026.pdf)
- [Modelo de resumo](docs/Modelo_resumo_HackatOhm2026-1.docx)
- [Cronograma](https://www.eng-eletrica.bh.cefetmg.br/hackatohm/acompanhe/)
- [Site oficial](https://abre.ai/hackatohm)

## Equipe

| Nome | Curso |
| ---- | ----- |
| Carlos Reis | Engenharia de Computação — 6º período |
| Luca Magni | Engenharia de Computação — 6º período |
| Pedro Oliveira | Engenharia de Computação — 6º período |

## Tech
- Python
- Streamlit
- SQLite
- Regras de extração
- Taxonomia de habilidades

## Funcionalidades

1. Extração de dados de currículos
- Parsing do arquivo .docx usando python-docx (texto + tabelas).
- Estimação de páginas baseada no volume de texto (~3.000 chars/página).
- Extração por regras (taxonomia fechada, sem LLM)

2. Tratamento de PII (dados pessoais)
- Detecção de e-mail, telefone, endereço e CEP no texto.
- Cifragem dos dados detectados com Fernet (chave em .env / BIDROOM_PII_KEY).
- Redação (***) dos dados no texto bruto, trechos de evidência e campos estruturados.
- Armazenamento criptografado em tabela cofre_dados_pessoais.

3. Persistência no SQLite

4. Pipeline de ingestão
- Carga em lote.
- Sincronização da pasta data/inbox/.
- Upload pela UI.

5. Pontuação e ranking
- Motor de scoring.
- Gatilhos G1–G6 (configuram requisitos mínimos)
- Composição de equipes: algoritmo guloso + otimizador.
- Ranking cultural: contagem de conquistas por programa/ano e desempate por certificado alvo. 

6. Gestão de editais e vagas
- Criação de editais.
- Requisitos individuais ou coletivos; ambiguidade; status (pendente, aprovado, rejeitado).
- Anexos de edital.
- Criação de disputas (processos de alocação) com status aberta/fechada.
- Alocações: um candidato por disputa/papel, reservas de vagas e índice único que impede duas alocações ativas do mesmo candidato na mesma disputa.

8. Interface Streamlit (app.py)
- Perfil do candidato: visualização de fatos, versões, evidências, upload de novo arquivo.
- Dashboard (T1): métricas gerais — total de currículos, curados/alocados, com erro.
- Alocação (T2): lista de disputas, recomendações automáticas, pontuações, cobertura, alternativas por vaga, riscos.
- Edital (T3): criação de editais, envio de anexo .docx, revisão de requisitos, ativação.
- Governança (T4–T6): sincronização da pasta data/inbox/, fila de revisão, verificação de vencimentos, reconhecimento.
- Cache global na memória (recarregado ao mudar de tela ou após operações).

## Próximos passos
- Sem LLM: toda extração é rule-based, ou seja, termos fora da taxonomia vão para base de dados *residual* e não pontuam.
- Aceita currículos apenas no formato .docx: não há suporte a PDF, .doc antigo, OCR ou imagens.
- Nomes não são detectados: o sistema usa pseudônimos sequenciais (Candidato 001, Candidato 002).
- PII cobre apenas e-mail, telefone, endereço, CEP: nomes não são redigidos.
- Sistema sem autenticação/RBAC: qualquer um que tenha a URL pode visualizar.
- Pool limitado da composição (14 por papel, busca até 200 mil nós).

## Video
[https://github.com/user-attachments/assets/4026c4ab-86eb-4db4-b78c-7409e1040bc6](https://github.com/user-attachments/assets/69536b39-e324-4d6c-87d6-5fd1957e78ac)
