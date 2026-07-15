"""Entrypoint operativo unificato di AI-craft.

    python -m aicraft.cli status
    python -m aicraft.cli profiles list
    python -m aicraft.cli profiles add-creator "Nome Creator"
    python -m aicraft.cli profiles add <creator_id> "Ruby Wilde" misto
    python -m aicraft.cli profiles use <profile_id>
    python -m aicraft.cli budget balance
    python -m aicraft.cli budget topup 100 --motivo "acquisto crediti"
    python -m aicraft.cli budget sync                  # allinea al saldo Higgsfield reale
    python -m aicraft.cli plan create <profile_id> 2026-07-20 2026-07-26
    python -m aicraft.cli plan add <plan_id> carosello --giorno lun
    python -m aicraft.cli plan show <plan_id>
    python -m aicraft.cli plan approve <plan_id>
    python -m aicraft.cli references sync --limit 5    # sheet -> DB -> download + mark sheet
    python -m aicraft.cli references sync --tab "VIRAL GENERAL" --category TALKING --limit 2
    python -m aicraft.cli references sync-policy       # sync bilanciato per categoria
    python -m aicraft.cli scheduler install-weekly-sync
    python -m aicraft.cli produce                       # esegue la pipeline sui piani approvati

Ogni comando apre una sessione, esegue, committa. Nessuna logica di dominio
qui: il CLI orchestra solo i moduli (profiles, budget, planning, reporting,
production).
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

from .budget import estimate as budget_estimate
from .budget import ledger as budget_ledger
from .budget import sync as budget_sync
from .budget.errors import BudgetInsufficientError
from .db.base import SessionLocal, init_db
from .db.models import ContentPiece, PlanWeek, Profile
from .planning import plan as planning
from . import profile_tracking
from .profiles import manager as profiles
from .reference_sync import allocator
from . import reporting


def _parse_date(value: str) -> dt.date:
    return dt.datetime.strptime(value, "%Y-%m-%d").date()


# --- status ---

def cmd_status(session, args):
    print(reporting.format_overview(reporting.overview(session)))


# --- profiles ---

def cmd_profiles_list(session, args):
    active = profiles.get_active_profile(session)
    active_id = active.id if active else None
    creators = {c.id: c.nome for c in profiles.list_creators(session)}
    rows = profiles.list_profiles(session)
    if not rows:
        print("Nessun profilo. Crea prima un creator e un profilo.")
        return
    for p in rows:
        mark = "*" if p.id == active_id else " "
        stato = "attivo" if p.attivo else "disabilitato"
        print(f"{mark} [{p.id}] {p.nome} — {p.tipo_contenuto} ({stato}) — creator: {creators.get(p.creator_id)}")


def cmd_profiles_add_creator(session, args):
    creator = profiles.create_creator(session, args.nome)
    print(f"Creator creato: [{creator.id}] {creator.nome}")


def cmd_profiles_add(session, args):
    profile = profiles.create_profile(
        session, creator_id=args.creator_id, nome=args.nome, tipo_contenuto=args.tipo
    )
    print(f"Profilo creato: [{profile.id}] {profile.nome} ({profile.tipo_contenuto})")


def cmd_profiles_use(session, args):
    profile = profiles.set_active_profile(session, args.profile_id)
    print(f"Profilo attivo: [{profile.id}] {profile.nome}")


def cmd_profiles_delete(session, args):
    profile = session.get(Profile, args.profile_id)
    nome = profile.nome if profile else "?"
    profiles.delete_profile(session, args.profile_id, force=args.force)
    print(f"Profilo [{args.profile_id}] {nome} eliminato.")


# --- budget ---

def cmd_budget_balance(session, args):
    print(f"Saldo crediti (CreditLedger): {budget_ledger.current_balance(session):.2f}")


def cmd_budget_topup(session, args):
    budget_ledger.record_topup(session, credits=args.credits, motivo=args.motivo)
    print(f"Ricarica di {args.credits:.2f} crediti registrata. Nuovo saldo: {budget_ledger.current_balance(session):.2f}")


def cmd_budget_sync(session, args):
    result = budget_sync.sync_from_higgsfield(session)
    print(f"Saldo reale Higgsfield: {result['real']:.2f}")
    print(f"Saldo interno prima:    {result['internal_before']:.2f}")
    print(f"Rettifica applicata:    {result['adjustment']:+.2f}")
    print(f"Nuovo saldo interno:    {budget_ledger.current_balance(session):.2f}")


# --- plan ---

def cmd_plan_create(session, args):
    plan = planning.create_plan_week(
        session, profile_id=args.profile_id, week_start=_parse_date(args.week_start), week_end=_parse_date(args.week_end)
    )
    print(f"Piano creato: [{plan.id}] profilo {plan.profile_id}, {plan.week_start}..{plan.week_end} (bozza, v{plan.version})")


def cmd_plan_add(session, args):
    plan = session.get(PlanWeek, args.plan_id)
    if plan is None:
        print(f"Piano {args.plan_id} inesistente", file=sys.stderr)
        sys.exit(1)
    piece = planning.add_content_piece(
        session,
        plan,
        content_type=args.content_type,
        scheduled_day=args.giorno,
        reference_id=args.reference,
        requested_source_category=args.category,
    )
    print(f"Aggiunto ContentPiece [{piece.id}] {piece.content_type} (giorno={piece.scheduled_day}) al piano {plan.id} (ora v{plan.version}, {plan.status})")


def cmd_plan_show(session, args):
    plan = session.get(PlanWeek, args.plan_id)
    if plan is None:
        print(f"Piano {args.plan_id} inesistente", file=sys.stderr)
        sys.exit(1)
    print(f"Piano [{plan.id}] profilo {plan.profile_id} — {plan.week_start}..{plan.week_end} — {plan.status} (v{plan.version})")
    pieces = session.scalars(select_pieces(plan.id)).all()
    if not pieces:
        print("  (nessun content piece)")
    for p in pieces:
        est = f"{p.cost_credits_estimated:.2f}" if p.cost_credits_estimated is not None else "—"
        print(f"  [{p.id}] {p.content_type} giorno={p.scheduled_day} stato={p.status} stima={est}")


def cmd_plan_approve(session, args):
    plan = session.get(PlanWeek, args.plan_id)
    if plan is None:
        print(f"Piano {args.plan_id} inesistente", file=sys.stderr)
        sys.exit(1)
    try:
        estimated = planning.approve_plan(session, plan)
    except BudgetInsufficientError as exc:
        session.rollback()
        print(f"APPROVAZIONE BLOCCATA — {exc}", file=sys.stderr)
        sys.exit(2)
    assignment = allocator.assign_references_to_plan(session, plan.id)
    print(f"Piano [{plan.id}] APPROVATO. Costo stimato: {estimated:.2f} crediti. Saldo: {budget_ledger.current_balance(session):.2f}")
    print(f"Reference assegnate: {assignment.assigned} (mancanti: {assignment.missing})")
    if assignment.missing:
        print("Libreria insufficiente: esegui `references sync` per scaricare nuovi reference.")


def cmd_plan_assign_refs(session, args):
    plan = session.get(PlanWeek, args.plan_id)
    if plan is None:
        print(f"Piano {args.plan_id} inesistente", file=sys.stderr)
        sys.exit(1)
    result = allocator.assign_references_to_plan(session, plan.id)
    print(
        f"Reference assegnate al piano [{plan.id}]: {result.assigned} "
        f"(mancanti: {result.missing})"
    )


# --- references / produce ---

def cmd_references_sync(session, args):
    from .reference_sync.sync import run_once as ref_run_once

    max_items = 0 if args.all else args.limit
    result = ref_run_once(max_items=max_items, source_tab=args.tab, source_category=args.category)
    print(
        "Reference sync completato. "
        f"Processati: {result['processed']} / {result['pending_total']} candidati "
        f"(sheet refs: {result['sheet_refs']}, cleanup: {result['cleanup_deleted']})."
    )


def cmd_references_sync_policy(session, args):
    from .reference_sync.sync import run_policy_once

    result = run_policy_once(policy=args.policy)
    rows = ", ".join(f"{p['tab']}:{p['category']}={p['processed']}/{p['limit']}" for p in result["policy"])
    print(
        "Reference sync policy completato. "
        f"Processati: {result['processed']} (sheet refs: {result['sheet_refs']}, cleanup: {result['cleanup_deleted']})."
    )
    if rows:
        print(f"Dettaglio policy: {rows}")
    retry_stale = result.get("retry_stale")
    if retry_stale:
        print(
            f"Retry automatico falliti vecchi (>{retry_stale['older_than_days']}gg): "
            f"{retry_stale['ready']}/{retry_stale['total']} tornate pronte."
        )


def cmd_produce(session, args):
    from .production.engine import run_once as prod_run_once

    result = prod_run_once(session, plan_id=args.plan)
    print(
        "Produzione completata. "
        f"Piani approvati: {result['approved_plans']}, "
        f"reference assegnate: {result['assigned_references']}, "
        f"mancanti: {result['missing_references']}, "
        f"processati: {result['processed']}, "
        f"consegnati: {result['delivered']}, "
        f"falliti: {result['failed']}."
    )


def cmd_scheduler_plist(session, args):
    from . import scheduler

    data = scheduler.plist_bytes(
        scheduler.launchd_plist(weekday=args.weekday, hour=args.hour, minute=args.minute)
    )
    print(data.decode("utf-8"))


def cmd_scheduler_install_weekly_sync(session, args):
    from . import scheduler

    path = scheduler.install_weekly_sync(weekday=args.weekday, hour=args.hour, minute=args.minute)
    print(f"LaunchAgent scritto: {path}")
    print("Per caricarlo: launchctl load ~/Library/LaunchAgents/com.aicraft.weekly-reference-sync.plist")


def cmd_scheduler_install_daily_tracking(session, args):
    from . import scheduler

    path = scheduler.install_daily_tracking(hour=args.hour, minute=args.minute)
    print(f"LaunchAgent scritto: {path}")
    print("Per caricarlo: launchctl load ~/Library/LaunchAgents/com.aicraft.daily-profile-tracking.plist")


def cmd_tracking_add(session, args):
    tracked = profile_tracking.add_tracked_profile(session, url_or_username=args.url, label=args.label)
    print(f"Profilo tracking aggiunto: [{tracked.id}] @{tracked.username} ({tracked.label})")


def cmd_tracking_sync(session, args):
    result = profile_tracking.sync_all(session)
    print(f"Tracking aggiornato: {len(result['synced'])} ok, {len(result['errors'])} errori")
    for err in result["errors"]:
        print(f"  ERRORE @{err['username']}: {err['error']}")


def cmd_tracking_report(session, args):
    result = profile_tracking.report(session)
    if not result["profiles"]:
        print("Nessun profilo tracciato.")
        return
    for row in result["profiles"]:
        snap = row["latest"] or {}
        delta = row["followers_delta"]
        delta_text = "n/d" if delta is None else f"{delta:+d}"
        print(
            f"@{row['username']} — follower {snap.get('followers_count', '—')} "
            f"({delta_text}), media {snap.get('media_count', '—')}, "
            f"best video {snap.get('best_video_metric', '—')}"
        )


def select_pieces(plan_id):
    from sqlalchemy import select

    return select(ContentPiece).where(ContentPiece.plan_week_id == plan_id).order_by(ContentPiece.id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aicraft", description="AI-craft — orchestratore contenuti IG")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Panoramica del sistema").set_defaults(func=cmd_status)

    p_prof = sub.add_parser("profiles", help="Gestione creator/profili")
    prof_sub = p_prof.add_subparsers(dest="sub", required=True)
    prof_sub.add_parser("list", help="Elenca i profili").set_defaults(func=cmd_profiles_list)
    a = prof_sub.add_parser("add-creator", help="Crea un creator")
    a.add_argument("nome")
    a.set_defaults(func=cmd_profiles_add_creator)
    a = prof_sub.add_parser("add", help="Crea un profilo")
    a.add_argument("creator_id", type=int)
    a.add_argument("nome")
    a.add_argument("tipo", choices=profiles.TIPI_CONTENUTO_VALIDI)
    a.set_defaults(func=cmd_profiles_add)
    a = prof_sub.add_parser("use", help="Imposta il profilo attivo")
    a.add_argument("profile_id", type=int)
    a.set_defaults(func=cmd_profiles_use)
    a = prof_sub.add_parser("delete", help="Elimina un profilo")
    a.add_argument("profile_id", type=int)
    a.add_argument("--force", action="store_true", help="elimina anche se ha piani/contenuti collegati")
    a.set_defaults(func=cmd_profiles_delete)

    p_bud = sub.add_parser("budget", help="Crediti/saldo")
    bud_sub = p_bud.add_subparsers(dest="sub", required=True)
    bud_sub.add_parser("balance", help="Mostra il saldo").set_defaults(func=cmd_budget_balance)
    a = bud_sub.add_parser("topup", help="Registra una ricarica crediti")
    a.add_argument("credits", type=float)
    a.add_argument("--motivo", default="ricarica")
    a.set_defaults(func=cmd_budget_topup)
    bud_sub.add_parser("sync", help="Allinea il ledger al saldo Higgsfield reale").set_defaults(func=cmd_budget_sync)

    p_plan = sub.add_parser("plan", help="Pianificazione settimanale")
    plan_sub = p_plan.add_subparsers(dest="sub", required=True)
    a = plan_sub.add_parser("create", help="Crea un piano settimanale")
    a.add_argument("profile_id", type=int)
    a.add_argument("week_start", help="YYYY-MM-DD")
    a.add_argument("week_end", help="YYYY-MM-DD")
    a.set_defaults(func=cmd_plan_create)
    a = plan_sub.add_parser("add", help="Aggiunge un content piece a un piano")
    a.add_argument("plan_id", type=int)
    a.add_argument("content_type")
    a.add_argument("--giorno", choices=("lun", "mar", "mer", "gio", "ven", "sab", "dom"))
    a.add_argument("--reference", type=int, default=None)
    a.add_argument("--category", default=None, help="Categoria sorgente desiderata (es. BOOBS, TALKING)")
    a.set_defaults(func=cmd_plan_add)
    a = plan_sub.add_parser("show", help="Mostra un piano e i suoi pezzi")
    a.add_argument("plan_id", type=int)
    a.set_defaults(func=cmd_plan_show)
    a = plan_sub.add_parser("approve", help="Approva un piano (con controllo budget)")
    a.add_argument("plan_id", type=int)
    a.set_defaults(func=cmd_plan_approve)
    a = plan_sub.add_parser("assign-refs", help="Assegna automaticamente reference pronte dal DB locale")
    a.add_argument("plan_id", type=int)
    a.set_defaults(func=cmd_plan_assign_refs)

    p_ref = sub.add_parser("references", help="Reference sync")
    ref_sub = p_ref.add_subparsers(dest="sub", required=True)
    a = ref_sub.add_parser("sync", help="Sincronizza dal Google Sheet")
    a.add_argument("--limit", type=int, default=None, help="numero massimo di reference da scaricare in questo run")
    a.add_argument("--all", action="store_true", help="scarica tutti i candidati del run, senza limite")
    a.add_argument("--tab", default=None, help="filtra un tab dello sheet (es. CAROSELLI)")
    a.add_argument("--category", default=None, help="filtra una categoria sorgente (es. TALKING, BOOBS)")
    a.set_defaults(func=cmd_references_sync)
    a = ref_sub.add_parser("sync-policy", help="Sincronizza usando AICRAFT_REFERENCE_SYNC_POLICY")
    a.add_argument("--policy", default=None, help="override policy, es. 'CAROSELLI:BOOBS=5,VIRAL GENERAL:TALKING=3'")
    a.set_defaults(func=cmd_references_sync_policy)

    p_sched = sub.add_parser("scheduler", help="Automazioni locali")
    sched_sub = p_sched.add_subparsers(dest="sub", required=True)
    a = sched_sub.add_parser("plist", help="Stampa il plist launchd del sync settimanale")
    a.add_argument("--weekday", type=int, default=2, help="launchd weekday: 1=dom, 2=lun, ... 7=sab")
    a.add_argument("--hour", type=int, default=9)
    a.add_argument("--minute", type=int, default=0)
    a.set_defaults(func=cmd_scheduler_plist)
    a = sched_sub.add_parser("install-weekly-sync", help="Installa LaunchAgent per sync settimanale")
    a.add_argument("--weekday", type=int, default=2, help="launchd weekday: 1=dom, 2=lun, ... 7=sab")
    a.add_argument("--hour", type=int, default=9)
    a.add_argument("--minute", type=int, default=0)
    a.set_defaults(func=cmd_scheduler_install_weekly_sync)
    a = sched_sub.add_parser("install-daily-tracking", help="Installa LaunchAgent per tracking IG giornaliero")
    a.add_argument("--hour", type=int, default=8)
    a.add_argument("--minute", type=int, default=30)
    a.set_defaults(func=cmd_scheduler_install_daily_tracking)

    p_track = sub.add_parser("tracking", help="Tracking profili Instagram pubblici")
    track_sub = p_track.add_subparsers(dest="sub", required=True)
    a = track_sub.add_parser("add", help="Aggiunge un profilo IG al tracking")
    a.add_argument("url")
    a.add_argument("--label", default=None)
    a.set_defaults(func=cmd_tracking_add)
    track_sub.add_parser("sync", help="Aggiorna snapshot dei profili tracciati").set_defaults(func=cmd_tracking_sync)
    track_sub.add_parser("report", help="Mini report tracking").set_defaults(func=cmd_tracking_report)

    a = sub.add_parser("produce", help="Esegue la produzione sui piani approvati")
    a.add_argument("--plan", type=int, default=None, help="limita la produzione a un piano approvato")
    a.set_defaults(func=cmd_produce)

    return parser


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = build_parser()
    args = parser.parse_args(argv)

    init_db()
    with SessionLocal() as session:
        args.func(session, args)
        session.commit()


if __name__ == "__main__":
    main()
