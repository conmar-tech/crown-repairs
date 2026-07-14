from __future__ import annotations

from orders import FirestoreSettings, SampleOrderRepository


def sample_repo() -> SampleOrderRepository:
    return SampleOrderRepository(FirestoreSettings.from_env())


def test_sample_orders_support_at_jeweler_and_code_suffix_filter():
    repo = sample_repo()
    result = repo.list_orders(status="AtJeweler", code="0003")

    assert result["total"] == 1
    assert result["items"][0]["orderStatus"] == "AtJeweler"
    assert result["items"][0]["orderCodeSuffix"] == "0003"


def test_sample_clients_aggregate_order_counts():
    repo = sample_repo()
    result = repo.list_clients(sort="orders")

    assert result["total"] == 5
    assert result["items"][0]["orderCount"] == 1
    assert result["items"][0]["key"].startswith(("phone:", "name:"))


def test_sample_picked_up_can_settle_balance():
    repo = sample_repo()
    order = repo.list_orders(status="InWork")["items"][0]
    updated = repo.update_status(order["id"], "PickedUp", "test@example.com", settle_balance=True)

    assert updated is not None
    assert updated["orderStatus"] == "PickedUp"
    assert updated["depositPaidCents"] == updated["totalPriceCents"]
    assert updated["balanceDueCents"] == 0


def test_sample_payment_update_recalculates_due_for_sync():
    repo = sample_repo()
    order = repo.list_orders(status="InWork")["items"][0]
    revision = order["revision"]

    updated = repo.update_payment(
        order["id"],
        total_price_cents=25_000,
        deposit_paid_cents=7_500,
        user_email="test@example.com",
    )

    assert updated is not None
    assert updated["totalPriceCents"] == 25_000
    assert updated["depositPaidCents"] == 7_500
    assert updated["balanceDueCents"] == 17_500
    assert updated["revision"] == revision + 1
    assert updated["lastModifiedDeviceId"] == "web-admin"
