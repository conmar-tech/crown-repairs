"""Finance summaries for repair orders."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Iterable


STATUS_LABELS = {
    "New": "New",
    "InWork": "In work",
    "AtJeweler": "At Jeweler",
    "Ready": "Ready",
    "PickedUp": "Picked up",
}
OPEN_STATUSES = {"New", "InWork", "AtJeweler", "Ready"}


def date_range_for_period(period: str, today: date) -> tuple[date | None, date | None]:
    if period == "week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if period == "month":
        start = today.replace(day=1)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start, next_month - timedelta(days=1)
    if period == "year":
        return today.replace(month=1, day=1), today.replace(month=12, day=31)
    return None, None


def filter_orders(
    orders: Iterable[dict],
    date_from: date | None,
    date_to: date | None,
) -> list[dict]:
    filtered = []
    for order in orders:
        order_date = order.get("createdDate")
        if not isinstance(order_date, date):
            continue
        if date_from and order_date < date_from:
            continue
        if date_to and order_date > date_to:
            continue
        filtered.append(order)
    return filtered


def build_finance_dashboard(
    orders: Iterable[dict],
    today: date,
    period: str = "month",
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict:
    if not date_from and not date_to:
        date_from, date_to = date_range_for_period(period, today)

    filtered = filter_orders(orders, date_from, date_to)
    status_counts = {status: 0 for status in STATUS_LABELS}
    status_amounts = {status: 0 for status in STATUS_LABELS}
    total_value = 0
    open_value = 0
    open_due = 0
    deposits = 0

    for order in filtered:
        status = order.get("orderStatus") or "New"
        total = int(order.get("totalPriceCents") or 0)
        deposit = int(order.get("depositPaidCents") or 0)
        due = int(order.get("balanceDueCents") or 0)
        total_value += total
        deposits += deposit
        if status in status_counts:
            status_counts[status] += 1
            status_amounts[status] += total
        if status in OPEN_STATUSES:
            open_value += total
            open_due += due

    return {
        "as_of": today.isoformat(),
        "period": period,
        "date_from": date_from.isoformat() if date_from else "",
        "date_to": date_to.isoformat() if date_to else "",
        "summary": {
            "orders": len(filtered),
            "totalValueCents": total_value,
            "openValueCents": open_value,
            "inWorkValueCents": status_amounts["InWork"] + status_amounts["AtJeweler"],
            "atJewelerValueCents": status_amounts["AtJeweler"],
            "readyValueCents": status_amounts["Ready"],
            "dueCents": open_due,
            "depositCents": deposits,
        },
        "statusCounts": status_counts,
        "statusAmounts": status_amounts,
        "series": {
            "week": build_week_series(filtered, today),
            "month": build_month_series(filtered, today),
            "year": build_year_series(filtered, today),
        },
    }


def build_week_series(orders: Iterable[dict], today: date) -> list[dict]:
    start = today - timedelta(days=today.weekday())
    totals = defaultdict(int)
    for order in orders:
        order_date = order.get("createdDate")
        if isinstance(order_date, date) and start <= order_date <= start + timedelta(days=6):
            totals[order_date] += int(order.get("totalPriceCents") or 0)
    labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return [
        {
            "label": labels[index],
            "detail": (start + timedelta(days=index)).isoformat(),
            "valueCents": totals[start + timedelta(days=index)],
        }
        for index in range(7)
    ]


def build_month_series(orders: Iterable[dict], today: date) -> list[dict]:
    start = today.replace(day=1)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    days = (next_month - start).days
    totals = defaultdict(int)
    for order in orders:
        order_date = order.get("createdDate")
        if isinstance(order_date, date) and start <= order_date < next_month:
            totals[order_date] += int(order.get("totalPriceCents") or 0)
    return [
        {
            "label": str(index + 1),
            "detail": (start + timedelta(days=index)).isoformat(),
            "valueCents": totals[start + timedelta(days=index)],
        }
        for index in range(days)
    ]


def build_year_series(orders: Iterable[dict], today: date) -> list[dict]:
    totals = defaultdict(int)
    for order in orders:
        order_date = order.get("createdDate")
        if isinstance(order_date, date) and order_date.year == today.year:
            totals[order_date.month] += int(order.get("totalPriceCents") or 0)
    labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return [
        {"label": labels[index], "detail": f"{today.year}-{index + 1:02d}", "valueCents": totals[index + 1]}
        for index in range(12)
    ]


def parse_created_date(value: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None
