"""Tracking leggero dei profili Instagram delle creator.

Non e' analytics privato e non usa API business: legge metriche pubbliche
accessibili tramite la stessa sessione Instagram locale gia' usata per il
download reference (instagrapi). Lo scopo e' un mini report giornaliero:
follower, delta, numero media e miglior video recente.
"""

from __future__ import annotations

import datetime as dt
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from .db.models import InstagramProfileSnapshot, TrackedInstagramProfile
from .reference_sync import downloader


def username_from_url(value: str) -> str:
    text = (value or "").strip()
    if not text:
        raise ValueError("serve un URL o username Instagram")
    if text.startswith("@"):
        return text[1:].strip().lower()
    if "instagram.com" not in text and "/" not in text:
        return text.strip().lower()
    parsed = urlparse(text if "://" in text else "https://" + text)
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        raise ValueError(f"URL Instagram senza username: {value!r}")
    username = parts[0]
    if username in {"p", "reel", "reels", "tv", "stories"}:
        raise ValueError(f"URL non sembra un profilo Instagram: {value!r}")
    return username.lower()


def add_tracked_profile(
    session: Session,
    *,
    url_or_username: str,
    label: str | None = None,
    profile_id: int | None = None,
) -> TrackedInstagramProfile:
    username = username_from_url(url_or_username)
    existing = session.query(TrackedInstagramProfile).filter(TrackedInstagramProfile.username == username).one_or_none()
    if existing:
        existing.active = True
        if label:
            existing.label = label
        if profile_id is not None:
            existing.profile_id = profile_id
        existing.updated_at = dt.datetime.utcnow()
        session.flush()
        return existing
    tracked = TrackedInstagramProfile(
        username=username,
        url=f"https://www.instagram.com/{username}/",
        label=label or username,
        profile_id=profile_id,
        active=True,
    )
    session.add(tracked)
    session.flush()
    return tracked


def deactivate_tracked_profile(session: Session, tracked_id: int) -> None:
    tracked = session.get(TrackedInstagramProfile, tracked_id)
    if tracked is None:
        raise ValueError(f"Profilo tracking {tracked_id} inesistente")
    tracked.active = False
    tracked.updated_at = dt.datetime.utcnow()
    session.flush()


def sync_tracked_profile(session: Session, tracked: TrackedInstagramProfile, *, client=None, media_amount: int = 24) -> InstagramProfileSnapshot:
    client = client or downloader._get_client()
    info = client.user_info_by_username(tracked.username)
    user_id = getattr(info, "pk", None) or getattr(info, "id", None)
    medias = client.user_medias(user_id, amount=media_amount) if user_id else []
    best = _best_video(medias)
    snapshot = InstagramProfileSnapshot(
        tracked_profile_id=tracked.id,
        followers_count=_int_attr(info, "follower_count", "followers_count"),
        following_count=_int_attr(info, "following_count", "followings_count"),
        media_count=_int_attr(info, "media_count"),
        best_video_url=_media_url(best) if best else None,
        best_video_shortcode=_str_attr(best, "code") if best else None,
        best_video_metric=_video_metric(best) if best else None,
        best_video_likes=_int_attr(best, "like_count") if best else None,
        best_video_comments=_int_attr(best, "comment_count") if best else None,
        best_video_caption=(_str_attr(best, "caption_text") or "")[:500] if best else None,
        raw={
            "username": tracked.username,
            "full_name": _str_attr(info, "full_name"),
            "is_private": bool(getattr(info, "is_private", False)),
        },
    )
    session.add(snapshot)
    tracked.updated_at = dt.datetime.utcnow()
    session.flush()
    return snapshot


def sync_all(session: Session, *, client=None) -> dict:
    rows = session.query(TrackedInstagramProfile).filter(TrackedInstagramProfile.active.is_(True)).order_by(TrackedInstagramProfile.id).all()
    synced = []
    errors = []
    for tracked in rows:
        try:
            snap = sync_tracked_profile(session, tracked, client=client)
            synced.append({"id": tracked.id, "username": tracked.username, "snapshot_id": snap.id})
        except Exception as exc:  # noqa: BLE001 - un profilo rotto non deve bloccare gli altri
            errors.append({"id": tracked.id, "username": tracked.username, "error": str(exc)})
    return {"synced": synced, "errors": errors}


def report(session: Session) -> dict:
    profiles = session.query(TrackedInstagramProfile).order_by(TrackedInstagramProfile.active.desc(), TrackedInstagramProfile.label).all()
    rows = []
    for tracked in profiles:
        snaps = (
            session.query(InstagramProfileSnapshot)
            .filter(InstagramProfileSnapshot.tracked_profile_id == tracked.id)
            .order_by(InstagramProfileSnapshot.captured_at.desc(), InstagramProfileSnapshot.id.desc())
            .limit(2)
            .all()
        )
        latest = snaps[0] if snaps else None
        previous = snaps[1] if len(snaps) > 1 else None
        rows.append({
            "id": tracked.id,
            "label": tracked.label,
            "username": tracked.username,
            "url": tracked.url,
            "active": tracked.active,
            "latest": _snapshot_dict(latest),
            "followers_delta": (
                (latest.followers_count or 0) - (previous.followers_count or 0)
                if latest and previous and latest.followers_count is not None and previous.followers_count is not None
                else None
            ),
        })
    return {"profiles": rows}


def _best_video(medias: list):
    videos = [m for m in medias if _is_video(m)]
    return max(videos, key=_video_metric, default=None)


def _is_video(media) -> bool:
    if media is None:
        return False
    media_type = getattr(media, "media_type", None)
    product_type = str(getattr(media, "product_type", "") or "").lower()
    return media_type == 2 or "clips" in product_type or "reel" in product_type


def _video_metric(media) -> int:
    return _int_attr(media, "play_count", "view_count", "like_count") or 0


def _media_url(media) -> str | None:
    code = _str_attr(media, "code")
    return f"https://www.instagram.com/reel/{code}/" if code else None


def _snapshot_dict(snapshot: InstagramProfileSnapshot | None) -> dict | None:
    if snapshot is None:
        return None
    return {
        "id": snapshot.id,
        "captured_at": snapshot.captured_at.isoformat() if snapshot.captured_at else None,
        "followers_count": snapshot.followers_count,
        "following_count": snapshot.following_count,
        "media_count": snapshot.media_count,
        "best_video_url": snapshot.best_video_url,
        "best_video_shortcode": snapshot.best_video_shortcode,
        "best_video_metric": snapshot.best_video_metric,
        "best_video_likes": snapshot.best_video_likes,
        "best_video_comments": snapshot.best_video_comments,
        "best_video_caption": snapshot.best_video_caption,
    }


def _int_attr(obj, *names) -> int | None:
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _str_attr(obj, name) -> str | None:
    value = getattr(obj, name, None)
    return str(value) if value is not None else None
