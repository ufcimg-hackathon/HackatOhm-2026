"""Testes de integração com o conjunto de 228 DOCX fornecido para o hackathon.

Esses testes verificam ingestão, consistência e estrutura da base, NÃO precisão
semântica contra um gabarito anotado nem autenticidade de certificações.
"""
from __future__ import annotations
import collections
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.analisar_curriculos import (
    build_records, insert_sqlite, read_zip_files, REGEXES, SQL_SEARCH,
)

DATASET = Path(__file__).resolve().parents[2] / 'data' / 'curriculos_raw'

class TestIntegracaoCurriculos(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DATASET.is_dir():
            raise AssertionError('Base ausente: copie os 228 DOCX em data/curriculos_raw/')
        cls.raw, cls.zip_errors = read_zip_files(DATASET)
        cls.records, cls.errors, cls.timing = build_records(cls.raw)
        cls.temporary = tempfile.TemporaryDirectory()
        cls.db = insert_sqlite(cls.records, Path(cls.temporary.name) / 'teste.sqlite')

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, 'db'):
            cls.db.close()
        if hasattr(cls, 'temporary'):
            cls.temporary.cleanup()

    def test_integridade_de_leitura(self):
        self.assertEqual(len(self.raw), 228)
        self.assertEqual(len(self.records), 228)
        self.assertEqual(self.zip_errors, [])
        self.assertEqual(self.errors, [])

    def test_integridade_do_banco(self):
        n = self.db.execute('SELECT COUNT(*) FROM documents').fetchone()[0]
        links = self.db.execute('SELECT COUNT(*) FROM document_skills').fetchone()[0]
        skill_count = self.db.execute('''SELECT COUNT(DISTINCT skill_id) FROM document_skills''').fetchone()[0]
        self.assertEqual(n, 228)
        self.assertEqual(links, 3713)
        self.assertEqual(skill_count, 49)

    def test_tabelas_e_duplicatas_de_texto(self):
        tables = sum(r['table_count'] > 0 for r in self.records)
        duplicated = sum(n-1 for n in collections.Counter(r['text_sha256'] for r in self.records).values())
        self.assertEqual(tables, 153)
        self.assertEqual(duplicated, 2)

    def test_consultas_conferem_com_o_texto(self):
        from src.analisar_curriculos import extract_docx
        texts = [extract_docx(b)[0] for b in self.raw]
        for skill in ('Python', 'JavaScript', 'AWS', 'PMP', 'Power BI'):
            literal_count = sum(bool(REGEXES[skill].search(t)) for t in texts)
            db_count = len(self.db.execute(SQL_SEARCH, (skill,)).fetchall())
            with self.subTest(skill=skill):
                self.assertEqual(literal_count, db_count)

    def test_status_nunca_comprovado_por_padrao(self):
        invalid = self.db.execute('''SELECT COUNT(*) FROM document_skills
            WHERE evidence_status != 'declared_unverified' ''').fetchone()[0]
        self.assertEqual(invalid, 0)

    def test_banco_nao_guarda_texto_ou_contato(self):
        columns_document = {row[1] for row in self.db.execute('PRAGMA table_info(documents)')}
        columns_relation = {row[1] for row in self.db.execute('PRAGMA table_info(document_skills)')}
        # 'name' em skills é o nome da TECNOLOGIA, não de uma pessoa.
        for forbidden in ('email','phone','name','filename','full_text','raw_text'):
            self.assertNotIn(forbidden, columns_document)
            self.assertNotIn(forbidden, columns_relation)

if __name__ == '__main__':
    unittest.main()
