"""Command Center (Step 6) — layer di reporting READ-ONLY.

Il blueprint (§2, §5) vuole il Command Center come dashboard che "legge lo
stesso DB, nessuna logica duplicata", da fare dopo che il motore gira
stabile da riga di comando. Questo modulo e' quella base: aggrega lo stato
del sistema leggendo il DB tramite i moduli esistenti (il saldo passa da
budget.ledger, non ricalcolato) e non introduce nuova logica di dominio.
Una eventuale UI web piu' avanti consumera' queste stesse funzioni.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .budget import ledger
from .db.models import ContentPiece, PlanWeek, Profile, ReferenceItem


def _count_by(session: Session, column) -> dict:
    rows = session.execute(select(column, func.count()).group_by(column)).all()
    return {(value if value is not None else "—"): count for value, count in rows}


def overview(session: Session) -> dict:
    return {
        "saldo_crediti": ledger.current_balance(session),
        "profili": [
            {"id": p.id, "nome": p.nome, "tipo": p.tipo_contenuto, "attivo": p.attivo}
            for p in session.scalars(select(Profile).order_by(Profile.id))
        ],
        "reference_per_stato": _count_by(session, ReferenceItem.status),
        "piani_per_stato": _count_by(session, PlanWeek.status),
        "content_per_stato": _count_by(session, ContentPiece.status),
    }


def format_overview(ov: dict) -> str:
    lines = []
    lines.append("=== AI-craft — Command Center ===")
    lines.append(f"Saldo crediti (CreditLedger): {ov['saldo_crediti']:.2f}")
    lines.append("")

    lines.append(f"Profili ({len(ov['profili'])}):")
    if not ov["profili"]:
        lines.append("  (nessuno)")
    for p in ov["profili"]:
        stato = "attivo" if p["attivo"] else "disabilitato"
        lines.append(f"  [{p['id']}] {p['nome']} — {p['tipo']} ({stato})")
    lines.append("")

    def _block(titolo, mapping):
        lines.append(f"{titolo}:")
        if not mapping:
            lines.append("  (nessuno)")
        for stato, n in sorted(mapping.items(), key=lambda kv: str(kv[0])):
            lines.append(f"  {stato:20s} {n}")
        lines.append("")

    _block("Reference per stato", ov["reference_per_stato"])
    _block("Piani per stato", ov["piani_per_stato"])
    _block("Content piece per stato", ov["content_per_stato"])

    return "\n".join(lines).rstrip()
