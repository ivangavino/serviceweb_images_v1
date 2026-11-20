"""Small parameterized repository helpers used by mildew services."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.mildiu import TomaMildiu


def lock_take(session: Session, toma_id):
    return session.execute(
        select(TomaMildiu).where(TomaMildiu.toma_id == toma_id).with_for_update()
    ).scalar_one_or_none()


def get_take(session: Session, toma_id):
    return session.execute(select(TomaMildiu).where(TomaMildiu.toma_id == toma_id)).scalar_one_or_none()

