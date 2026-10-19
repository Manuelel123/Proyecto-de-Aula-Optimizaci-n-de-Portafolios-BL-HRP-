"""Owner-scoped access to tracked portfolios.

Every read takes the InsForge ``user_id`` of the signed-in user, so a route
can never load another user's portfolio: it simply gets ``None`` (a 404).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select

from optimizacion_portafolios.storage import db
from optimizacion_portafolios.storage.models import (
    MODELS,
    VERSION_REASONS,
    Holding,
    Portfolio,
    PortfolioVersion,
    Transaction,
)


@dataclass(frozen=True)
class HoldingData:
    ticker: str
    target_weight: float
    units: float
    entry_price: float


@dataclass(frozen=True)
class TransactionData:
    ticker: str
    side: str
    units: float
    price: float
    amount: float
    cost: float = 0.0


def list_portfolios(user_id: str, include_archived: bool = True) -> list[Portfolio]:
    query = select(Portfolio).where(Portfolio.user_id == user_id)
    if not include_archived:
        query = query.where(Portfolio.status == "active")
    query = query.order_by(Portfolio.status, Portfolio.created_at.desc())
    return list(db.session.scalars(query))


def get_portfolio(user_id: str, portfolio_id: int) -> Portfolio | None:
    """The portfolio if it exists and belongs to ``user_id``."""
    return db.session.scalar(
        select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.user_id == user_id)
    )


def active_portfolios() -> list[Portfolio]:
    """Every active portfolio of every user (for the daily snapshot command)."""
    return list(db.session.scalars(select(Portfolio).where(Portfolio.status == "active")))


def _version(
    number: int,
    effective_date: date,
    reason: str,
    holdings: list[HoldingData],
    transactions: list[TransactionData],
    *,
    params: dict | None,
    value_before: float | None,
    value_after: float,
    cost: float,
    turnover: float,
    note: str | None,
) -> PortfolioVersion:
    if reason not in VERSION_REASONS:
        raise ValueError(f"Unknown version reason: {reason}")
    if not holdings:
        raise ValueError("A portfolio version needs at least one holding.")
    return PortfolioVersion(
        number=number,
        effective_date=effective_date,
        reason=reason,
        note=note,
        params=params or {},
        value_before=value_before,
        value_after=value_after,
        cost=cost,
        turnover=turnover,
        holdings=[Holding(**vars(holding)) for holding in holdings],
        transactions=[Transaction(**vars(transaction)) for transaction in transactions],
    )


def create_portfolio(
    user_id: str,
    *,
    name: str,
    model: str,
    benchmark: str,
    initial_capital: float,
    tracking_start: date,
    holdings: list[HoldingData],
    transactions: list[TransactionData] = (),
    params: dict | None = None,
    commission_bps: float = 10.0,
    cost: float = 0.0,
) -> Portfolio:
    """Create a portfolio and its version 1 (the fixing) in one commit."""
    if model not in MODELS:
        raise ValueError(f"Unknown model: {model}")
    portfolio = Portfolio(
        user_id=user_id,
        name=name,
        model=model,
        benchmark=benchmark,
        initial_capital=initial_capital,
        commission_bps=commission_bps,
        tracking_start=tracking_start,
        versions=[
            _version(
                1, tracking_start, "fix", list(holdings), list(transactions),
                params=params, value_before=None, value_after=initial_capital - cost,
                cost=cost, turnover=1.0, note=None,
            )
        ],
    )
    db.session.add(portfolio)
    db.session.commit()
    return portfolio


def add_version(
    portfolio: Portfolio,
    *,
    effective_date: date,
    reason: str,
    holdings: list[HoldingData],
    transactions: list[TransactionData],
    value_before: float,
    value_after: float,
    cost: float,
    turnover: float,
    params: dict | None = None,
    note: str | None = None,
) -> PortfolioVersion:
    current = portfolio.current_version
    if current is not None and effective_date < current.effective_date:
        raise ValueError("A new version cannot start before the current one.")
    version = _version(
        (current.number if current else 0) + 1, effective_date, reason,
        list(holdings), list(transactions), params=params,
        value_before=value_before, value_after=value_after, cost=cost,
        turnover=turnover, note=note,
    )
    portfolio.versions.append(version)
    db.session.commit()
    return version


def archive_portfolio(portfolio: Portfolio, when) -> None:
    portfolio.status = "archived"
    portfolio.archived_at = when
    db.session.commit()
