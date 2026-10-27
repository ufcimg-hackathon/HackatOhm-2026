"""Fase 3 — gates G1–G6 (aplicados antes de pontuar; handoff §6.1).

Regras eliminatórias não viram pontos: elas decidem se o candidato continua
na disputa. Gates de revisão sinalizam falta de informação (não eliminam).
"""
from __future__ import annotations

from dataclasses import dataclass

from src.scoring.catalogo import PerfilPapel
from src.scoring.contexto import ContextoCandidato

NIVEIS_PORTUGUES_PROFISSIONAL = {
    "fluente",
    "fluent",
    "avancado",
    "avançado",
    "advanced",
    "nativo",
    "native",
}


@dataclass
class ResultadoGate:
    gate: str
    status: str  # passou | elimina | revisar
    motivo: str

    def como_dict(self) -> dict:
        return {"gate": self.gate, "status": self.status, "motivo": self.motivo}


def _usuarios_n2(ctx: ContextoCandidato, core: tuple[str, ...]) -> int:
    return sum(
        1
        for nome in core
        if (conf := ctx.competencia(nome)) is not None and conf >= 50
    )


def avaliar_gates(
    ctx: ContextoCandidato,
    perfil: PerfilPapel,
    config: dict | None,
    elegivel_brasil: str | None = None,
) -> list[ResultadoGate]:
    config = config or {}
    resultados: list[ResultadoGate] = []

    # G1 — pelo menos 3 de 5 core; 2 em N2+
    cfg = config.get("G1", {})
    if cfg.get("ativo", True):
        min_core = int(cfg.get("min_core", 3))
        min_n2 = int(cfg.get("min_core_n2", 2))
        presentes = [n for n in perfil.core if ctx.competencia(n) is not None]
        em_n2 = _usuarios_n2(ctx, perfil.core)
        if len(presentes) < min_core:
            resultados.append(
                ResultadoGate(
                    "G1",
                    "elimina",
                    f"{len(presentes)}/{len(perfil.core)} competências core"
                    f" (mínimo {min_core})",
                )
            )
        elif em_n2 < min_n2:
            resultados.append(
                ResultadoGate(
                    "G1",
                    "elimina",
                    f"{em_n2} core em N2+ (mínimo {min_n2})",
                )
            )
        else:
            resultados.append(
                ResultadoGate(
                    "G1", "passou", f"{len(presentes)} core, {em_n2} em N2+"
                )
            )

    # G2 — anos mínimos no papel (sênior exige 5)
    cfg = config.get("G2", {})
    if cfg.get("ativo", True):
        anos_min = float(
            cfg.get("anos_minimos", {}).get(perfil.sigla, perfil.anos_minimos)
        )
        if ctx.anos_experiencia < anos_min:
            resultados.append(
                ResultadoGate(
                    "G2",
                    "elimina",
                    f"{ctx.anos_experiencia:.1f} anos (mínimo {anos_min:.0f})",
                )
            )
        else:
            resultados.append(
                ResultadoGate(
                    "G2", "passou", f"{ctx.anos_experiencia:.1f} anos no currículo"
                )
            )

    # G3 — português profissional (não informado => revisar)
    cfg = config.get("G3", {})
    if cfg.get("ativo", True):
        aceitos = {
            n.lower()
            for n in cfg.get("niveis_aceitos", NIVEIS_PORTUGUES_PROFISSIONAL)
        }
        portugues = ctx.idioma("Português")
        if portugues is None or not portugues.nivel:
            resultados.append(
                ResultadoGate("G3", "revisar", "Português não informado")
            )
        elif portugues.nivel.lower() in aceitos:
            resultados.append(
                ResultadoGate("G3", "passou", f"Português {portugues.nivel}")
            )
        else:
            resultados.append(
                ResultadoGate(
                    "G3",
                    "revisar",
                    f"Português {portugues.nivel} — confirmar nível profissional",
                )
            )

    # G4 — autorizado a trabalhar no Brasil (só elimina se explícito "não")
    cfg = config.get("G4", {})
    if cfg.get("ativo", True):
        elig = (elegivel_brasil or ctx.elegivel_brasil or "nao_informado").lower()
        if elig == "nao":
            resultados.append(
                ResultadoGate("G4", "elimina", "Elegibilidade Brasil: não")
            )
        elif elig == "sim":
            resultados.append(
                ResultadoGate("G4", "passou", "Elegibilidade Brasil: sim")
            )
        else:
            resultados.append(
                ResultadoGate(
                    "G4",
                    "passou",
                    "Elegibilidade Brasil não informada — assumido sim",
                )
            )

    # G5 — disponibilidade 100% (sem fonte na base => revisar)
    cfg = config.get("G5", {})
    if cfg.get("ativo", True):
        resultados.append(
            ResultadoGate("G5", "revisar", "Disponibilidade não informada")
        )

    # G6 — nenhuma core em N0; 2 em N2+
    cfg = config.get("G6", {})
    if cfg.get("ativo", True):
        presentes = [
            (nome, ctx.competencia(nome))
            for nome in perfil.core
            if ctx.competencia(nome) is not None
        ]
        em_n0 = [nome for nome, conf in presentes if conf == 0]
        em_n2 = sum(1 for _nome, conf in presentes if conf >= 50)
        if em_n0:
            resultados.append(
                ResultadoGate("G6", "elimina", f"core em N0: {', '.join(em_n0)}")
            )
        elif em_n2 < int(cfg.get("min_core_n2", 2)):
            resultados.append(
                ResultadoGate("G6", "elimina", f"{em_n2} core em N2+ (mínimo 2)")
            )
        else:
            resultados.append(
                ResultadoGate("G6", "passou", f"{em_n2} core em N2+")
            )

    return resultados


def status_geral(gates: list[ResultadoGate]) -> str:
    """elegivel | revisar | eliminado."""
    if any(g.status == "elimina" for g in gates):
        return "eliminado"
    if any(g.status == "revisar" for g in gates):
        return "revisar"
    return "elegivel"
