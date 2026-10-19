"""Tables of tracked ("optimal") portfolios.

``DailySnapshot`` keeps one row per portfolio and business day (value,
benchmark value, weights and, for the days it was computed, the metrics), so
the history survives later changes in the provider's data.

A ``Portfolio`` is a sequence of ``PortfolioVersion`` rows: fixing the
portfolio creates version 1, and every rebalance, removal or addition creates
the next one with its ``Holding`` rows (target weight, units, entry price) and
the ``Transaction`` rows that turned the previous holdings into the new ones.
The current version is the one with the highest ``number`` (no pointer column,
so there is no circular foreign key to keep in sync).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import JSON, Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from optimizacion_portafolios.storage import db

MODELS = ("hrp", "black_litterman")
STATUSES = ("active", "archived")
VERSION_REASONS = ("fix", "rebalance", "reoptimize", "remove", "add")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Portfolio(db.Model):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(120))
    model: Mapped[str] = mapped_column(String(20))
    benchmark: Mapped[str] = mapped_column(String(32))
    initial_capital: Mapped[float] = mapped_column(Float)
    commission_bps: Mapped[float] = mapped_column(Float, default=10.0)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    tracking_start: Mapped[date] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    versions: Mapped[list[PortfolioVersion]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        order_by="PortfolioVersion.number",
    )
    alert_rules: Mapped[list[AlertRule]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan", order_by="AlertRule.id"
    )
    alert_events: Mapped[list[AlertEvent]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        order_by="AlertEvent.triggered_on.desc()",
        lazy="dynamic",
    )
    snapshots: Mapped[list[DailySnapshot]] = relationship(
        back_populates="portfolio",
        cascade="all, delete-orphan",
        order_by="DailySnapshot.day",
        lazy="dynamic",
    )

    @property
    def current_version(self) -> PortfolioVersion | None:
        return self.versions[-1] if self.versions else None

    @property
    def is_active(self) -> bool:
        return self.status == "active"


class PortfolioVersion(db.Model):
    __tablename__ = "portfolio_versions"
    __table_args__ = (UniqueConstraint("portfolio_id", "number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    effective_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Model inputs (universe, window, weight limits, views, objective, rf...).
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    # Portfolio value just before and after the change (after = before - cost).
    value_before: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_after: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    turnover: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    portfolio: Mapped[Portfolio] = relationship(back_populates="versions")
    holdings: Mapped[list[Holding]] = relationship(
        back_populates="version",
        cascade="all, delete-orphan",
        order_by="Holding.target_weight.desc()",
    )
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )

    @property
    def target_weights(self) -> dict[str, float]:
        return {holding.ticker: holding.target_weight for holding in self.holdings}

    @property
    def units(self) -> dict[str, float]:
        return {holding.ticker: holding.units for holding in self.holdings}


class Holding(db.Model):
    __tablename__ = "holdings"
    __table_args__ = (UniqueConstraint("version_id", "ticker"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("portfolio_versions.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(32))
    target_weight: Mapped[float] = mapped_column(Float)
    units: Mapped[float] = mapped_column(Float)
    entry_price: Mapped[float] = mapped_column(Float)

    version: Mapped[PortfolioVersion] = relationship(back_populates="holdings")


class Transaction(db.Model):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("portfolio_versions.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))  # "buy" or "sell"
    units: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    amount: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float, default=0.0)

    version: Mapped[PortfolioVersion] = relationship(back_populates="transactions")


class DailySnapshot(db.Model):
    __tablename__ = "daily_snapshots"
    __table_args__ = (UniqueConstraint("portfolio_id", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Float)
    benchmark_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    weights: Mapped[dict] = mapped_column(JSON, default=dict)
    # Scalar metrics as of ``day``; None for days backfilled without them.
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    portfolio: Mapped[Portfolio] = relationship(back_populates="snapshots")


class AlertRule(db.Model):
    """User-editable thresholds of one alert kind (see ``analytics.alerts``)."""

    __tablename__ = "alert_rules"
    __table_args__ = (UniqueConstraint("portfolio_id", "kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    yellow: Mapped[float | None] = mapped_column(Float, nullable=True)
    red: Mapped[float | None] = mapped_column(Float, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    portfolio: Mapped[Portfolio] = relationship(back_populates="alert_rules")


class AlertEvent(db.Model):
    """A period during which a rule stayed at one level (open while unresolved)."""

    __tablename__ = "alert_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    level: Mapped[str] = mapped_column(String(8))
    triggered_on: Mapped[date] = mapped_column(Date)
    resolved_on: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    updated_on: Mapped[date] = mapped_column(Date)

    portfolio: Mapped[Portfolio] = relationship(back_populates="alert_events")

    @property
    def is_open(self) -> bool:
        return self.resolved_on is None
