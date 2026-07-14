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
