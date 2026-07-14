from __future__ import annotations

from datetime import date

from analytics import build_finance_dashboard, date_range_for_period


def sample_orders():
    return [
        {
            "createdDate": date(2026, 7, 6),
            "orderStatus": "New",
            "totalPriceCents": 10_000,
            "depositPaidCents": 2_000,
            "balanceDueCents": 8_000,
        },
        {
            "createdDate": date(2026, 7, 7),
            "orderStatus": "InWork",
            "totalPriceCents": 20_000,
            "depositPaidCents": 5_000,
            "balanceDueCents": 15_000,
        },
        {
            "createdDate": date(2026, 7, 8),
            "orderStatus": "Ready",
            "totalPriceCents": 30_000,
            "depositPaidCents": 10_000,
            "balanceDueCents": 20_000,
        },
        {
            "createdDate": date(2026, 7, 9),
            "orderStatus": "PickedUp",
            "totalPriceCents": 40_000,
            "depositPaidCents": 40_000,
            "balanceDueCents": 0,
        },
    ]


def test_period_ranges():
    today = date(2026, 7, 12)
    assert date_range_for_period("week", today) == (date(2026, 7, 6), date(2026, 7, 12))
    assert date_range_for_period("month", today) == (date(2026, 7, 1), date(2026, 7, 31))
    assert date_range_for_period("year", today) == (date(2026, 1, 1), date(2026, 12, 31))
    assert date_range_for_period("all", today) == (None, None)


def test_finance_summary_open_due_excludes_picked_up():
    dashboard = build_finance_dashboard(sample_orders(), today=date(2026, 7, 12), period="week")
    assert dashboard["summary"]["orders"] == 4
    assert dashboard["summary"]["totalValueCents"] == 100_000
    assert dashboard["summary"]["openValueCents"] == 60_000
    assert dashboard["summary"]["readyValueCents"] == 30_000
    assert dashboard["summary"]["dueCents"] == 43_000
    assert dashboard["summary"]["depositCents"] == 57_000
    assert dashboard["statusCounts"] == {"New": 1, "InWork": 1, "AtJeweler": 0, "Ready": 1, "PickedUp": 1}


def test_finance_date_filter():
    dashboard = build_finance_dashboard(
        sample_orders(),
        today=date(2026, 7, 12),
        period="all",
        date_from=date(2026, 7, 7),
        date_to=date(2026, 7, 8),
    )
    assert dashboard["summary"]["orders"] == 2
    assert dashboard["summary"]["totalValueCents"] == 50_000
