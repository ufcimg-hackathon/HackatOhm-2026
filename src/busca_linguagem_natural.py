#!/usr/bin/env python3
"""Demonstra consulta em português a um vocabulário CONTROLADO de competências.

Exemplos:
    python busca_linguagem_natural.py --database saida/curriculos_anonimizados.sqlite --consulta 'Encontre currículos que mencionem Python e AWS'
    python busca_linguagem_natural.py --database saida/curriculos_anonimizados.sqlite --consulta 'Java ou JavaScript'

Importante: NÃO é RAG, NÃO usa LLM e NÃO comprova competências. Reconhece apenas
aliases expressamente definidos no extrator e retorna identificadores pseudônimos.
"""
from __future__ import annotations
import argparse
import json
import re
import sqlite3
from pathlib import Path
try:
    from .analisar_curriculos import REGEXES
except ImportError:  # execução direta: python src/busca_linguagem_natural.py
    from analisar_curriculos import REGEXES


def interpretar(pergunta: str):
    termos = [nome for nome, padrao in REGEXES.items() if padrao.search(pergunta)]
    # Convenção simples e explícita: qualquer palavra isolada "ou" indica OR.
    operador = 'OR' if re.search(r'\bou\b', pergunta, flags=re.I) else 'AND'
    return termos, operador


def procurar(con: sqlite3.Connection, pergunta: str, limite: int = 20):
    termos, operador = interpretar(pergunta)
    if not termos:
        return {'pergunta':pergunta,'termos_reconhecidos':[], 'aviso':
                'Nenhuma competência conhecida identificada; refine a pergunta.', 'resultados':[]}
    placeholders = ','.join('?' for _ in termos)
    having = 'COUNT(DISTINCT s.name) = ?' if operador == 'AND' else 'COUNT(DISTINCT s.name) >= 1'
    sql = (f'''SELECT d.id FROM documents d
      JOIN document_skills ds ON ds.document_id = d.id
      JOIN skills s ON s.id = ds.skill_id
      WHERE s.name IN ({placeholders}) GROUP BY d.id HAVING {having}
      ORDER BY d.id LIMIT ?''')
    parametros = [*termos]
    if operador == 'AND': parametros.append(len(termos))
    parametros.append(limite)
    ids = [r[0] for r in con.execute(sql, parametros)]
    return {'pergunta':pergunta, 'termos_reconhecidos':termos, 'operador':operador,
            'resultados':ids, 'resultados_exibidos':len(ids),
            'aviso':'Resultado por menção de termos (regex), sem inferência semântica nem comprovação. O limite pode truncar resultados.'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--database', type=Path, required=True)
    ap.add_argument('--consulta', required=True)
    ap.add_argument('--limite', type=int, default=20)
    args = ap.parse_args()
    if args.limite < 1: ap.error('--limite deve ser >= 1')
    with sqlite3.connect(args.database) as con:
        print(json.dumps(procurar(con,args.consulta,args.limite),ensure_ascii=False,indent=2))


if __name__ == '__main__': main()
