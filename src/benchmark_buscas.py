#!/usr/bin/env python3
"""Compara busca linear em textos já extraídos vs consulta indexada SQLite.
ATENÇÃO: não compara desempenho de gestores e não mede processo comercial.
"""
from pathlib import Path
import argparse, collections, json, sqlite3, statistics, time
try:
    from .analisar_curriculos import read_zip_files, extract_docx, REGEXES, SQL_SEARCH, percentile
except ImportError:  # execução direta
    from analisar_curriculos import read_zip_files, extract_docx, REGEXES, SQL_SEARCH, percentile

PESQUISAS=['Python','Java','JavaScript','React','AWS','Docker','Kubernetes','SQL Server','PMP','Power BI']

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--input',required=True,type=Path)
    ap.add_argument('--database',required=True,type=Path)
    ap.add_argument('--output',required=True,type=Path)
    ap.add_argument('--repeticoes',type=int,default=5)
    args=ap.parse_args()
    documents,_=read_zip_files(args.input)
    texts=[extract_docx(b)[0] for b in documents]
    cn=sqlite3.connect(args.database)
    linear_times=[]; db_times=[]; checks=[]
    for term in PESQUISAS:
        regex=REGEXES[term]
        for _ in range(args.repeticoes):
            t=time.perf_counter(); match=sum(bool(regex.search(text)) for text in texts)
            linear_times.append((time.perf_counter()-t)*1000)
            t=time.perf_counter(); rows=cn.execute(SQL_SEARCH,(term,)).fetchall()
            db_times.append((time.perf_counter()-t)*1000)
            if len(rows)!=match:
                raise AssertionError(f'Divergência {term}: texto {match}, banco {len(rows)}')
        checks.append({'skill':term,'documents_matching':match,'same_result':True})
    result={
        'comparative_query_types':len(PESQUISAS),
        'total_queries_per_method':len(linear_times),
        'linear_text_search_median_ms':statistics.median(linear_times),
        'linear_text_search_p95_ms':percentile(linear_times,.95),
        'sqlite_search_median_ms':statistics.median(db_times),
        'sqlite_search_p95_ms':percentile(db_times,.95),
        'speedup_ratio_of_medians':statistics.median(linear_times)/statistics.median(db_times),
        'all_results_equal':all(c['same_result'] for c in checks),
        'checks':checks,
        'scope':'A busca linear percorre texto DOCX já extraído e mantido em RAM. SQLite consulta índice preexistente. Custos de indexação não estão no tempo de consulta. Não mede trabalho humano.'
    }
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:round(v,4) if isinstance(v,float) else v for k,v in result.items() if k!='checks'},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
