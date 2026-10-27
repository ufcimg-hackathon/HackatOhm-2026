#!/usr/bin/env python3
"""Benchmark reproduzível de ingestão de currículos DOCX em SQLite.

A saída padrão é pseudonimizada: não grava nomes, contatos, nomes de arquivo
nem texto integral. Competências são sinais textuais NÃO validados.
Dependências: Python 3.10+ e lxml. Entrada: diretório contendo DOCX ou ZIP com DOCX.

Uso:
    python src/analisar_curriculos.py --input data/curriculos_raw --output ./saida
    python src/analisar_curriculos.py --input data/curriculos_raw --output ./saida --repeticoes 3
"""
from __future__ import annotations
import argparse
import collections
import hashlib
import io
import json
import math
import re
import sqlite3
import statistics
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

NS = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'

# Vocabulário controlado de competências, com aliases delimitados.
# Expressões específicas primeiro. A simples menção NÃO prova domínio.
SKILL_SYNONYMS = {
    'Python': [r'python'],
    'Java': [r'java(?!script)'],
    'JavaScript': [r'java\s*script', r'javascript', r'ecmascript'],
    'TypeScript': [r'type\s*script'],
    'C++': [r'c\+\+'],
    'C#': [r'c#', r'c\s*sharp'],
    'React': [r'react\s*\.?\s*js', r'reactjs', r'react(?!ive)'],
    'Angular': [r'angular(?:\s*js)?'],
    'Vue': [r'vue\s*\.?\s*js', r'vuejs'],
    'Node.js': [r'node\s*\.?\s*js'],
    'SQL': [r'(?<![a-z])sql(?![a-z])'],
    'PostgreSQL': [r'postgres(?:ql)?', r'postgre\s*sql'],
    'MySQL': [r'mysql'],
    'SQL Server': [r'(?:microsoft|ms)\s*sql\s*server', r'(?<![a-z])sql\s*server'],
    'Oracle Database': [r'oracle\s*(?:database|db|1[0-9]g|[0-9]+c)', r'oracle\s*sql'],
    'MongoDB': [r'mongo\s*db', r'mongodb'],
    'Redis': [r'redis'],
    'Django': [r'django'],
    'Flask': [r'flask'],
    'FastAPI': [r'fastapi'],
    'Spring': [r'spring(?:\s*boot|\s*mvc|\s*framework)?'],
    'Hibernate': [r'hibernate'],
    'AWS': [r'(?<![a-z])aws(?![a-z])', r'amazon\s+web\s+services'],
    'Azure': [r'(?<![a-z])azure(?![a-z])'],
    'Google Cloud': [r'google\s+cloud', r'(?<![a-z])gcp(?![a-z])'],
    'Docker': [r'docker'],
    'Kubernetes': [r'kubernetes', r'(?<![a-z])k8s(?![a-z])'],
    'Terraform': [r'terraform'],
    'Ansible': [r'ansible'],
    'Git': [r'(?<![a-z])git(?![a-z])'],
    'Linux': [r'linux'],
    'Apache Spark': [r'apache\s+spark', r'(?<![a-z])pyspark(?![a-z])'],
    'Hadoop': [r'hadoop'],
    'Kafka': [r'kafka'],
    'Airflow': [r'airflow'],
    'Databricks': [r'databricks'],
    'Machine Learning': [r'machine\s+learning'],
    'NLP': [r'natural\s+language\s+processing', r'(?<![a-z])nlp(?![a-z])'],
    'Power BI': [r'power\s*bi'],
    'Tableau': [r'tableau'],
    'Salesforce': [r'salesforce'],
    'SAP': [r'(?<![a-z])sap(?![a-z])'],
    'Jira': [r'jira'],
    'Scrum': [r'scrum'],
    'PMP': [r'(?<![a-z])pmp(?![a-z])', r'project\s+management\s+professional'],
    'ITIL': [r'(?<![a-z])itil(?![a-z])'],
    'Excel': [r'(?<![a-z])excel(?![a-z])'],
    'PowerPoint': [r'power\s*point'],
    'HTML': [r'(?<![a-z])html\s*\d*(?![a-z])'],
    'CSS': [r'(?<![a-z])css\s*\d*(?![a-z])'],
    'REST': [r'(?<![a-z])rest(?:ful)?\s*(?:api|web\s*service|service)s?(?![a-z])', r'(?<![a-z])restful(?![a-z])'],
}
# Evita falsos positivos de substrings ("Java" em "JavaScript" etc.).
REGEXES = {name: re.compile(r'(?<![\w])(?:'+'|'.join(patterns)+r')(?![\w])', re.I)
           for name,patterns in SKILL_SYNONYMS.items()}
EMAIL_RE = re.compile(r'(?<![\w.-])[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}(?![\w.-])', re.I)
PHONE_RE = re.compile(r'(?<!\w)(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})[\s.-]?\d{3}[\s.-]?\d{4}(?!\w)')
DEGREE_RE = re.compile(r'\b(?:bachelor(?:[\W_]s)?|master(?:[\W_]s)?|ph\.?d\.?|doctorate|university|gradua[cç][aã]o|mestrado|doutorado)\b', re.I)
EXPERIENCE_RE = re.compile(r'\b(?:over|more\s+than|around|approximately|plus|mais\s+de)?\s*(\d{1,2})\s*\+?\s*(?:years?|anos?)\s+(?:of\s+)?(?:experience|experi[eê]ncia)\b', re.I)

SQL_SCHEMA = '''
PRAGMA foreign_keys = ON;
CREATE TABLE documents (
 id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE, text_sha256 TEXT NOT NULL,
 char_count INTEGER NOT NULL, paragraph_count INTEGER NOT NULL,
 table_count INTEGER NOT NULL, table_paragraph_count INTEGER NOT NULL,
 has_email INTEGER NOT NULL, has_phone INTEGER NOT NULL,
 has_degree_keyword INTEGER NOT NULL, claimed_years INTEGER,
 extraction_status TEXT NOT NULL, verification_status TEXT NOT NULL DEFAULT 'not_reviewed'
);
CREATE TABLE skills (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE
);
CREATE TABLE document_skills (
 document_id TEXT NOT NULL REFERENCES documents(id),
 skill_id INTEGER NOT NULL REFERENCES skills(id),
 evidence_status TEXT NOT NULL DEFAULT 'declared_unverified',
 PRIMARY KEY (document_id,skill_id)
);
CREATE INDEX idx_ds_skill ON document_skills(skill_id);
'''

def read_zip_files(path):
    """Carrega DOCX de uma pasta ou ZIP; preserva ordenação para testes comparáveis.

    Mantém o nome original por compatibilidade com os scripts e testes anteriores.
    """
    path=Path(path)
    files=[]; error=[]
    if path.is_dir():
        items=sorted((p for p in path.rglob('*') if p.is_file()), key=lambda p: str(p.relative_to(path)).casefold())
        for item in items:
            if item.name.startswith('~$'): continue
            if item.suffix.lower() != '.docx':
                error.append('Arquivo não DOCX detectado na pasta'); continue
            if item.stat().st_size > 50_000_000:
                error.append('DOCX excede limite de 50MB'); continue
            try: files.append(item.read_bytes())
            except Exception as exc: error.append(f'Falha de leitura: {type(exc).__name__}')
    else:
        with zipfile.ZipFile(path) as z:
            for info in sorted(z.infolist(), key=lambda x:x.filename.casefold()):
                if info.is_dir() or info.filename.startswith('__MACOSX/'): continue
                if not info.filename.lower().endswith('.docx'):
                    error.append('Arquivo não DOCX detectado no ZIP'); continue
                if info.file_size > 50_000_000:
                    error.append('DOCX excede limite de 50MB'); continue
                try: files.append(z.read(info))
                except Exception as exc: error.append(f'Falha de leitura: {type(exc).__name__}')
    return files,error

def input_size_bytes(path):
    path=Path(path)
    if path.is_dir():
        return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())
    return path.stat().st_size

def extract_docx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as doc:
        names=set(doc.namelist())
        tables=0; paras=0; table_paras=0; lines=[]
        parts=['word/document.xml']+sorted(n for n in names if re.fullmatch(r'word/(?:header|footer)\d*\.xml',n))
        for part in parts:
            if part not in names: continue
            root=etree.fromstring(doc.read(part))
            if part=='word/document.xml':
                tables=len(root.xpath('.//w:tbl', namespaces=NS))
            # Preserva ordem dos parágrafos, inclusive dentro de tabelas.
            for p in root.xpath('.//w:p', namespaces=NS):
                paras+=1
                if any(a.tag==W+'tbl' for a in p.iterancestors()): table_paras+=1
                fragments=[]
                for node in p.xpath('.//w:t | .//w:tab | .//w:br', namespaces=NS):
                    if node.tag == W+'t' and node.text: fragments.append(node.text)
                    elif node.tag in (W+'tab',W+'br'): fragments.append(' ')
                line=''.join(fragments).strip()
                if line: lines.append(line)
        return '\n'.join(lines), paras, tables, table_paras

def detect_features(text):
    found=[]
    for skill,rx in REGEXES.items():
        if rx.search(text): found.append(skill)
    m=EXPERIENCE_RE.findall(text[:4000])
    claimed=max(map(int,m)) if m else None
    return {
        'skills':found, 'has_email':bool(EMAIL_RE.search(text)),
        'has_phone':bool(PHONE_RE.search(text[:3000])),
        'has_degree_keyword':bool(DEGREE_RE.search(text)),
        'claimed_years':claimed
    }

def percentile(values, p):
    if not values: return None
    v=sorted(values); k=(len(v)-1)*p; lo=math.floor(k);hi=math.ceil(k)
    return v[lo]+(v[hi]-v[lo])*(k-lo)

def build_records(raw_documents):
    records=[]; err=[]; measurements={'extract_seconds':0.,'detect_seconds':0.}
    for i,data in enumerate(raw_documents,1):
        try:
            t=time.perf_counter(); raw,pars,tables,tblpars=extract_docx(data)
            measurements['extract_seconds']+=time.perf_counter()-t
            t=time.perf_counter(); features=detect_features(raw)
            measurements['detect_seconds']+=time.perf_counter()-t
            normalized=re.sub(r'\s+',' ',raw.casefold()).strip()
            records.append({
                'id':f'CV-{i:04d}', 'sha256':hashlib.sha256(data).hexdigest(),
                'text_sha256':hashlib.sha256(normalized.encode('utf8')).hexdigest(),
                'char_count':len(raw), 'paragraph_count':pars, 'table_count':tables,
                'table_paragraph_count':tblpars,**features
            })
        except Exception as e:
            err.append({'id':f'CV-{i:04d}','error_type':type(e).__name__})
    return records,err,measurements

def insert_sqlite(records, path):
    if path.exists(): path.unlink()
    con=sqlite3.connect(path)
    with con:
        con.executescript(SQL_SCHEMA)
        con.executemany('INSERT INTO skills(name) VALUES (?)',[(s,) for s in SKILL_SYNONYMS])
        skills={n:s for s,n in con.execute('SELECT id,name FROM skills')}
        con.executemany('INSERT INTO documents(id,sha256,text_sha256,char_count,paragraph_count,table_count,table_paragraph_count,has_email,has_phone,has_degree_keyword,claimed_years,extraction_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            [tuple(int(r[k]) if k.startswith('has_') else r[k] for k in ('id','sha256','text_sha256','char_count','paragraph_count','table_count','table_paragraph_count','has_email','has_phone','has_degree_keyword','claimed_years'))+('readable',) for r in records])
        con.executemany('INSERT INTO document_skills(document_id,skill_id) VALUES (?,?)',[(r['id'],skills[s]) for r in records for s in r['skills']])
    return con

SQL_SEARCH = '''SELECT d.id FROM documents d
 JOIN document_skills ds ON ds.document_id=d.id
 JOIN skills s ON s.id=ds.skill_id
 WHERE s.name = ? ORDER BY d.id'''

def query_benchmark(con, n=500):
    names=list(SKILL_SYNONYMS)
    t=time.perf_counter(); durations=[]; counts=[]
    for i in range(n):
        s=names[i%len(names)]
        start=time.perf_counter()
        ids=con.execute(SQL_SEARCH,(s,)).fetchall()
        durations.append((time.perf_counter()-start)*1000)
        counts.append(len(ids))
    return {'queries':n,'total_seconds':time.perf_counter()-t,
            'median_ms':statistics.median(durations),'p95_ms':percentile(durations,.95),
            'max_ms':max(durations), 'total_matches_across_queries':sum(counts)}

def summarize(records, errors, zipped_errors, times, db_con, query_stats, archive_size, n_attempted, iterations):
    n=len(records)
    skills=collections.Counter(s for r in records for s in r['skills'])
    text_dupes=collections.Counter(r['text_sha256'] for r in records)
    binary_dupes=collections.Counter(r['sha256'] for r in records)
    chars=[r['char_count'] for r in records]
    numskills=[len(r['skills']) for r in records]
    tbs=[r['table_count'] for r in records]
    data={
        'analysis_utc':datetime.now(timezone.utc).isoformat(),
        'method':'DOCX word/document.xml + header/footer, lxml; regex skills; SQLite; pseudonymized',
        'archive_size_bytes':archive_size,'documents_attempted':n_attempted,
        'documents_ok':n,'documents_failed':len(errors), 'non_docx_or_zip_issues':len(zipped_errors),
        'unique_binary_sha256':len(binary_dupes),'duplicate_binary_document_count':sum(v-1 for v in binary_dupes.values()),
        'unique_normalized_text_sha256':len(text_dupes),'duplicate_normalized_text_document_count':sum(v-1 for v in text_dupes.values()),
        'text_chars_total':sum(chars),'text_chars_mean':statistics.mean(chars) if chars else 0,
        'text_chars_median':statistics.median(chars) if chars else 0,
        'text_chars_min':min(chars) if chars else 0,'text_chars_max':max(chars) if chars else 0,
        'documents_with_tables':sum(x>0 for x in tbs),
        'total_tables':sum(tbs),'total_paragraphs':sum(r['paragraph_count'] for r in records),
        'table_paragraphs':sum(r['table_paragraph_count'] for r in records),
        'documents_with_email_pattern':sum(r['has_email'] for r in records),
        'documents_with_phone_pattern':sum(r['has_phone'] for r in records),
        'documents_with_degree_keyword':sum(r['has_degree_keyword'] for r in records),
        'documents_with_experience_years_claim':sum(r['claimed_years'] is not None for r in records),
        'documents_with_known_skill':sum(bool(r['skills']) for r in records),
        'total_document_skill_pairs':sum(numskills),'distinct_skill_vocabulary_found':len(skills),
        'skills_per_document_median':statistics.median(numskills) if numskills else 0,
        'skills_per_document_mean':statistics.mean(numskills) if numskills else 0,
        'top_skills':[{'skill':s,'documents_mentioning':v} for s,v in skills.most_common(25)],
        'timing_seconds':times,'search_benchmark':query_stats,
        'db_documents':db_con.execute('select count(*) from documents').fetchone()[0],
        'db_skill_relations':db_con.execute('select count(*) from document_skills').fetchone()[0],
        'repetitions':iterations,
        'limitations':[
          'Competência encontrada por regex significa menção textual, não domínio comprovado.',
          'O código não valida nomes, cargos, organizações, diplomas, comprovações nem precisão contra gabarito humano.',
          'O tempo de execução automatizada não comprova redução do tempo humano em licitações.',
          'Não foi usado OCR, NER ou modelo de linguagem: resultados não os validam.',
          'A base fornecida tem DOCX; não representa necessariamente a diversidade de uma empresa.',
          'Dados de contato aparecem nos originais. Banco gerado não inclui nomes, e-mails, telefones ou texto original.'
        ]}
    return data

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input',required=True,type=Path)
    ap.add_argument('--output',required=True,type=Path)
    ap.add_argument('--repeticoes',type=int,default=3)
    args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    total=[]; final=None
    for rep in range(args.repeticoes):
        total_start=time.perf_counter()
        t=time.perf_counter(); docs,zip_err=read_zip_files(args.input)
        t_read=time.perf_counter()-t
        t=time.perf_counter(); records,errors,intern=build_records(docs)
        t_parse=time.perf_counter()-t
        t=time.perf_counter(); db=insert_sqlite(records,args.output/'curriculos_anonimizados.sqlite')
        t_insert=time.perf_counter()-t
        q=query_benchmark(db)
        t_total=time.perf_counter()-total_start
        ts={'zip_read_seconds':t_read,'extract_and_detect_seconds':t_parse,
            'xml_extract_seconds':intern['extract_seconds'],
            'regex_detect_seconds':intern['detect_seconds'],
            'sqlite_setup_insert_index_seconds':t_insert,
            'query_500_seconds':q['total_seconds'],
            'end_to_end_seconds':t_total,
            'avg_end_to_end_milliseconds_per_document':t_total/len(docs)*1000 if docs else None}
        total.append(ts)
        final=(records,errors,zip_err,db,q,docs)
        print(f"Repetição {rep+1}/{args.repeticoes}: arquivos={len(docs)}, ok={len(records)}, extração+detecção={t_parse:.3f}s, DB={t_insert:.3f}s, total={t_total:.3f}s",flush=True)
        db.close()
    records,errors,zip_err,old_db,q,docs=final
    db=sqlite3.connect(args.output/'curriculos_anonimizados.sqlite')
    summaries={k:{'samples':[round(t[k],5) for t in total],
                  'median':round(statistics.median(t[k] for t in total),5),
                  'min':round(min(t[k] for t in total),5),
                  'max':round(max(t[k] for t in total),5)} for k in total[0]}
    out=summarize(records,errors,zip_err,summaries,db,q,input_size_bytes(args.input),len(docs),args.repeticoes)
    (args.output/'metricas.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf8')
    (args.output/'falhas.json').write_text(json.dumps({'parse':errors,'zip':zip_err},ensure_ascii=False,indent=2),encoding='utf8')
    db.close()
    print('Métricas gravadas em:',args.output/'metricas.json')
    return 0 if not errors else 2

if __name__=='__main__': sys.exit(main())
