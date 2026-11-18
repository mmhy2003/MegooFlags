"""Instance-wide system state: the result of the latest release check."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from megooflags_api.db import Base


class UpdateStatus(Base):
    """The latest known MegooFlags release: a single row with id 1."""

    __tablename__ = "update_status"
    __table_args__ = (CheckConstraint("id = 1", name="single_row"),)

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    latest_version: Mapped[str | None] = mapped_column(String(50))
    # When a check last succeeded, and when one was last tried.
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
