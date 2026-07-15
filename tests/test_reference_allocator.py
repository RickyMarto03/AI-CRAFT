import datetime as dt

from aicraft.db.models import ContentPiece, Creator, PlanWeek, Profile, ReferenceItem
from aicraft.reference_sync import allocator


def _profile_plan(session):
    creator = Creator(nome="Ruby")
    profile = Profile(creator=creator, nome="ruby.test", tipo_contenuto="misto")
    plan = PlanWeek(profile=profile, week_start=dt.date(2026, 7, 20), week_end=dt.date(2026, 7, 26))
    session.add_all([creator, profile, plan])
    session.flush()
    return profile, plan


def _ref(session, *, url, week_start, category="BOOBS", hint="carosello", order=1, media=True):
    ref = ReferenceItem(
        source_url=url,
        source_tab="CAROSELLI" if hint == "carosello" else "VIRAL GENERAL",
        source_category=category,
        content_type_hint=hint,
        week_start=week_start,
        week_end=week_start + dt.timedelta(days=6),
        sheet_order=order,
        status="ready",
        frame_paths=["/tmp/foto.jpg"] if media and hint == "carosello" else [],
        local_video_path="/tmp/video.mp4" if media and hint == "video" else None,
    )
    session.add(ref)
    session.flush()
    return ref


def test_select_candidates_usa_ultime_due_settimane_e_ordina_dal_piu_vecchio(db_session):
    _ref(db_session, url="old", week_start=dt.date(2026, 7, 6), order=1)
    mid = _ref(db_session, url="mid", week_start=dt.date(2026, 7, 13), order=1)
    new = _ref(db_session, url="new", week_start=dt.date(2026, 7, 20), order=1)
    db_session.commit()

    rows = allocator.select_candidates(db_session, content_type="carosello", selection_weeks=2)

    assert [r.id for r in rows] == [mid.id, new.id]


def test_assign_references_esclude_gia_assegnate_e_rispetta_categoria(db_session):
    profile, plan = _profile_plan(db_session)
    boobs = _ref(db_session, url="boobs", week_start=dt.date(2026, 7, 13), category="BOOBS", order=1)
    booty = _ref(db_session, url="booty", week_start=dt.date(2026, 7, 13), category="BOOTY", order=2)
    already = ContentPiece(profile=profile, content_type="carosello", plan_week=plan, reference_id=boobs.id)
    target = ContentPiece(
        profile=profile,
        content_type="carosello",
        plan_week=plan,
        requested_source_category="BOOTY",
        status="reference_ready",
    )
    db_session.add_all([already, target])
    db_session.commit()

    result = allocator.assign_references_to_plan(db_session, plan.id)

    assert result.assigned == 1
    assert target.reference_id == booty.id


def test_video_talking_prende_solo_categoria_talking_con_video_locale(db_session):
    _ref(db_session, url="talk", week_start=dt.date(2026, 7, 13), category="TALKING", hint="video")
    _ref(db_session, url="bal", week_start=dt.date(2026, 7, 13), category="BALLETTI/LIPSYNC", hint="video")
    _ref(db_session, url="no-media", week_start=dt.date(2026, 7, 20), category="TALKING", hint="video", media=False)
    db_session.commit()

    rows = allocator.select_candidates(db_session, content_type="video_talking", selection_weeks=2)

    assert [r.source_url for r in rows] == ["talk"]
