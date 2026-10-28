"""Verifica não reutilização de profissionais e pontuação baseada em comprovados."""
import unittest
from src.experimentos.simular_equipe import CANDIDATOS, ROLES, pontos, selecionar

class TestSelecao(unittest.TestCase):
    def test_arranjos_e_composicoes(self):
        r=selecionar()
        self.assertEqual(r['combinacoes_sem_repetir'], 210)
        self.assertEqual(r['equipes_viaveis'], 13)
        self.assertEqual(r['pontuacao_total_ilustrativa'], 28)
        ids=[p['id'] for p in r['composicao'].values()]
        self.assertEqual(len(ids),len(set(ids)))

    def test_pmp_declarado_sem_comprovacao_nao_atende(self):
        pessoa=next(p for p in CANDIDATOS if p['id']=='P06')
        self.assertIn('PMP',pessoa['declaradas'])
        self.assertNotIn('PMP',pessoa['comp'])

if __name__=='__main__':unittest.main()
