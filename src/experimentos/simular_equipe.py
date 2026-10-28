#!/usr/bin/env python3
"""Simulação INDEPENDENTE com dados fictícios; NÃO usa currículos reais.
Mostra pontuação com evidências e disponibilidade, e busca exaustiva para
3 funções. O resultado demonstra lógica pequena, não escala/ótimo geral.
"""
import argparse, itertools, json, time
from pathlib import Path

ROLES={
  'gerente':{'obrigatorio':'PMP'},
  'especialista_energia':{'obrigatorio':'Energia'},
  'analista':{'obrigatorio':'SQL'},
}
# habilidades comprovadas: no caso real exigiria documentos validados.
# Algumas declarações existem sem evidência, e NÃO são pontuadas.
CANDIDATOS=[
 {'id':'P01','comp':['PMP','Energia'],'declaradas':['SQL'],'anos_verificados':12,'pos_verificada':True,'projetos_energia_verificados':4,'disponivel':True},
 {'id':'P02','comp':['PMP'],'declaradas':[],'anos_verificados':8,'pos_verificada':True,'projetos_energia_verificados':1,'disponivel':True},
 {'id':'P03','comp':['Energia','SQL'],'declaradas':[],'anos_verificados':14,'pos_verificada':True,'projetos_energia_verificados':3,'disponivel':True},
 {'id':'P04','comp':['SQL'],'declaradas':[],'anos_verificados':5,'pos_verificada':False,'projetos_energia_verificados':0,'disponivel':True},
 {'id':'P05','comp':['PMP','SQL'],'declaradas':[],'anos_verificados':16,'pos_verificada':True,'projetos_energia_verificados':2,'disponivel':False},
 {'id':'P06','comp':['Energia'],'declaradas':['PMP'],'anos_verificados':11,'pos_verificada':False,'projetos_energia_verificados':3,'disponivel':True},
 {'id':'P07','comp':['SQL'],'declaradas':[],'anos_verificados':10,'pos_verificada':False,'projetos_energia_verificados':0,'disponivel':True},
]

def pontos(p,funcao):
  # Pontuação FICTÍCIA por função inspirada nos critérios ilustrativos do case.
  if funcao == 'gerente': return 10 + (5 if p['anos_verificados']>10 else 0)
  if funcao == 'especialista_energia': return min(p['projetos_energia_verificados'],4)*2 + (5 if p['pos_verificada'] else 0)
  if funcao == 'analista': return 5 if p['anos_verificados']>10 else 0
  raise ValueError(funcao)

def selecionar():
  validas=[];inicio=time.perf_counter()
  for trio in itertools.permutations(CANDIDATOS,len(ROLES)):
    if all(p['disponivel'] and req['obrigatorio'] in p['comp'] for p,req in zip(trio,ROLES.values())):
      validas.append((sum(pontos(p,funcao) for funcao,p in zip(ROLES,trio)),trio))
  duracao=(time.perf_counter()-inicio)*1000
  if not validas: return {'viavel':False,'tempo_busca_ms':duracao}
  top=max(validas,key=lambda x:x[0])
  return {'tipo':'SIMULACAO_FICTICIA_NAO_ORIGINADA_DOS_CURRICULOS',
    'papéis':len(ROLES),'candidatos':len(CANDIDATOS),
    'combinacoes_sem_repetir':len(list(itertools.permutations(CANDIDATOS,len(ROLES)))),
    'equipes_viaveis':len(validas),'pontuacao_total_ilustrativa':top[0],
    'composicao':{role:{'id':p['id'],'pontos_na_funcao':pontos(p,role)} for role,p in zip(ROLES,top[1])},
    'tempo_busca_ms':duracao,
    'aviso':'Os critérios e perfis são fictícios. O script demonstra restrições e cálculo, não valida critérios de editais reais, nem extrai comprovações dos currículos.'}

if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--output',type=Path,default=Path('saida/simulacao_equipe.json'))
    args=ap.parse_args()
    r=selecionar()
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(r,ensure_ascii=False,indent=2))
