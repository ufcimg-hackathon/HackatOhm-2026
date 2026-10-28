-- 1) Mais citadas: mencões não comprovadas, não inferir domínio.
SELECT s.name, COUNT(*) AS curriculos_que_mencionam
FROM skills s JOIN document_skills ds ON ds.skill_id=s.id
GROUP BY s.id ORDER BY curriculos_que_mencionam DESC LIMIT 20;

-- 2) Currículos que mencionam simultaneamente Java, AWS e Docker.
-- NÃO comprova elegibilidade ou disponibilidade.
SELECT d.id
FROM documents d
JOIN document_skills ds ON ds.document_id=d.id
JOIN skills s ON s.id=ds.skill_id
WHERE s.name IN ('Java','AWS','Docker')
GROUP BY d.id
HAVING COUNT(DISTINCT s.name)=3;

-- 3) Documentos com menção a PMP, mas sem certificação verificada.
SELECT d.id, ds.evidence_status
FROM documents d
JOIN document_skills ds ON ds.document_id=d.id
JOIN skills s ON s.id=ds.skill_id
WHERE s.name='PMP' AND ds.evidence_status='declared_unverified';

-- 4) Potenciais textos duplicados sem exposição de nomes/contatos.
SELECT text_sha256, COUNT(*) AS copias
FROM documents
GROUP BY text_sha256 HAVING COUNT(*)>1;
