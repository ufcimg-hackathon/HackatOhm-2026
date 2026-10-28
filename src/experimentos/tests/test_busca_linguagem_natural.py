"""Testes da camada demonstrativa de linguagem natural baseada em regras."""
import sqlite3
import unittest
from src.experimentos.busca_linguagem_natural import interpretar, procurar

class TestBusca(unittest.TestCase):
    def test_interpreta_conjuncao(self):
        self.assertEqual(interpretar('Quem menciona Python e AWS?'),(['Python','AWS'],'AND'))

    def test_interpreta_disjuncao(self):
        self.assertEqual(interpretar('Java ou JavaScript'),(['Java','JavaScript'],'OR'))

    def test_consulta_sql_combinada(self):
        con=sqlite3.connect(':memory:')
        con.executescript('''CREATE TABLE documents (id TEXT PRIMARY KEY);
        CREATE TABLE skills (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE document_skills (document_id TEXT, skill_id INTEGER);
        INSERT INTO documents VALUES ('CV-0001'),('CV-0002'),('CV-0003');
        INSERT INTO skills VALUES (1,'Python'),(2,'AWS');
        INSERT INTO document_skills VALUES ('CV-0001',1),('CV-0001',2),('CV-0002',1),('CV-0003',2);''')
        r=procurar(con,'Quem menciona Python e AWS?')
        self.assertEqual(r['resultados'],['CV-0001'])
        r2=procurar(con,'Quem menciona Python ou AWS?')
        self.assertEqual(r2['resultados'],['CV-0001','CV-0002','CV-0003'])
        con.close()

if __name__=='__main__': unittest.main()
