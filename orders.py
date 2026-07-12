"""Firestore access for tablet repair orders."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from google.api_core.exceptions import GoogleAPICallError, NotFound
from google.auth.exceptions import GoogleAuthError
from google.cloud import firestore


VALID_STATUSES = ("New", "InWork", "Ready", "PickedUp")
STATUS_ORDER = {status: index for index, status in enumerate(VALID_STATUSES)}


class RepairsRepositoryError(RuntimeError):
    """Raised when Firestore cannot be reached or decoded."""


@dataclass(frozen=True)
class FirestoreSettings:
    project_id: str
    collection: str
    timezone: ZoneInfo

    @classmethod
    def from_env(cls) -> "FirestoreSettings":
        return cls(
            project_id=(
                os.getenv("FIREBASE_PROJECT_ID")
                or os.getenv("GOOGLE_CLOUD_PROJECT")
                or os.getenv("GCLOUD_PROJECT")
                or "eloquent-branch-414417"
            ),
            collection=os.getenv("REPAIR_ORDERS_COLLECTION", "repairOrders"),
            timezone=ZoneInfo(os.getenv("APP_TIMEZONE", "America/New_York")),
        )


class FirestoreOrderRepository:
    def __init__(self, settings: FirestoreSettings):
        self.settings = settings
        self._client: firestore.Client | None = None

    @property
    def client(self) -> firestore.Client:
        if self._client is None:
            self._client = firestore.Client(project=self.settings.project_id)
        return self._client

    @property
    def collection(self):
        return self.client.collection(self.settings.collection)

    def health(self) -> dict:
        try:
            first = list(self.collection.limit(1).stream(timeout=10))
        except (GoogleAPICallError, GoogleAuthError) as exc:
            raise RepairsRepositoryError(str(exc)) from exc
        return {
            "status": "ok",
            "project": self.settings.project_id,
            "collection": self.settings.collection,
            "reachable": True,
            "sampled": len(first),
        }

    def all_orders(self) -> list[dict]:
        try:
            snapshots = self.collection.order_by(
                "createdAt",
                direction=firestore.Query.DESCENDING,
            ).limit(2000).stream(timeout=10)
            return [self._decode(snapshot) for snapshot in snapshots]
        except (GoogleAPICallError, GoogleAuthError) as exc:
            raise RepairsRepositoryError(str(exc)) from exc

    def list_orders(
        self,
        *,
        status: str | None = None,
        query: str = "",
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        orders = self.all_orders()
        filtered = [
            order
            for order in orders
            if self._matches(order, query=query, date_from=date_from, date_to=date_to)
        ]
        status_counts = {item: 0 for item in VALID_STATUSES}
        for order in filtered:
            if order["orderStatus"] in status_counts:
                status_counts[order["orderStatus"]] += 1

        if status:
            filtered = [order for order in filtered if order["orderStatus"] == status]

        total = len(filtered)
        return {
            "items": filtered[offset : offset + limit],
            "total": total,
            "limit": limit,
            "offset": offset,
            "statusCounts": status_counts,
        }

    def update_status(self, order_id: str, status: str, user_email: str) -> dict | None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Unsupported status: {status}")

        reference = self.collection.document(order_id)
        try:
            snapshot = reference.get(timeout=10)
            if not snapshot.exists:
                return None

            reference.update(
                {
                    "orderStatus": status,
                    "revision": firestore.Increment(1),
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                    "adminUpdatedAt": firestore.SERVER_TIMESTAMP,
                    "updatedBy": user_email,
                    "lastModifiedDeviceId": "web-admin",
                    "source.lastModifiedDeviceId": "web-admin",
                },
                timeout=10,
            )
            return self._decode(reference.get(timeout=10))
        except NotFound:
            return None
        except (GoogleAPICallError, GoogleAuthError) as exc:
            raise RepairsRepositoryError(str(exc)) from exc

    def _decode(self, snapshot) -> dict:
        data = snapshot.to_dict() or {}
        customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
        payment = data.get("payment") if isinstance(data.get("payment"), dict) else {}
        files = data.get("files") if isinstance(data.get("files"), dict) else {}
        created_at = self._timestamp_string(data.get("createdAt"))
        updated_at = self._timestamp_string(data.get("updatedAt"))
        item_photos = self._string_list(files.get("itemPhotoUrls"))
        if not item_photos and files.get("itemPhotoUrl"):
            item_photos = [str(files.get("itemPhotoUrl"))]
        client_photos = self._string_list(files.get("clientPhotoUrls"))
        if not client_photos and files.get("clientPhotoUrl"):
            client_photos = [str(files.get("clientPhotoUrl"))]
        status = str(data.get("orderStatus") or "New")
        if status not in VALID_STATUSES:
            status = "New"

        order = {
            "id": snapshot.id,
            "orderId": str(data.get("orderId") or snapshot.id),
            "customerName": str(customer.get("name") or ""),
            "customerPhone": str(customer.get("phone") or ""),
            "customerAddress": str(customer.get("address") or ""),
            "orderStatus": status,
            "workDescription": str(data.get("workDescription") or "Repair order"),
            "workTemplates": self._string_list(data.get("workTemplates")),
            "totalPriceCents": self._int(payment.get("totalPriceCents")),
            "depositPaidCents": self._int(payment.get("depositPaidCents")),
            "balanceDueCents": self._int(payment.get("balanceDueCents")),
            "itemPhotoUrls": item_photos,
            "clientPhotoUrls": client_photos,
            "signatureUrl": str(files.get("signatureUrl") or ""),
            "labelPdfUrl": str(files.get("labelPdfUrl") or ""),
            "labelPngUrl": str(files.get("labelPngUrl") or ""),
            "createdAt": created_at,
            "updatedAt": updated_at,
            "createdDate": self._business_date(data.get("createdAt")),
            "revision": self._int(data.get("revision")),
            "firestorePath": snapshot.reference.path,
            "lastModifiedDeviceId": str(data.get("lastModifiedDeviceId") or ""),
            "updatedBy": str(data.get("updatedBy") or ""),
        }
        order["statusRank"] = STATUS_ORDER.get(order["orderStatus"], 0)
        return order

    def _matches(
        self,
        order: dict,
        *,
        query: str,
        date_from: date | None,
        date_to: date | None,
    ) -> bool:
        order_date = order.get("createdDate")
        if date_from and isinstance(order_date, date) and order_date < date_from:
            return False
        if date_to and isinstance(order_date, date) and order_date > date_to:
            return False
        needle = query.strip().lower()
        if not needle:
            return True
        phone_digits = "".join(ch for ch in order.get("customerPhone", "") if ch.isdigit())
        query_digits = "".join(ch for ch in needle if ch.isdigit())
        haystack = " ".join(
            [
                order.get("orderId", ""),
                order.get("customerName", ""),
                order.get("customerPhone", ""),
                order.get("customerAddress", ""),
                order.get("workDescription", ""),
                " ".join(order.get("workTemplates", [])),
            ]
        ).lower()
        return needle in haystack or (query_digits and query_digits in phone_digits)

    def _business_date(self, value) -> date | None:
        if isinstance(value, datetime):
            return value.astimezone(self.settings.timezone).date()
        return None

    def _timestamp_string(self, value) -> str:
        if isinstance(value, datetime):
            return value.astimezone(self.settings.timezone).isoformat()
        return ""

    @staticmethod
    def _string_list(value) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        return []

    @staticmethod
    def _int(value) -> int:
        if isinstance(value, bool):
            return 0
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(float(value))
            except ValueError:
                return 0
        return 0


class SampleOrderRepository:
    def __init__(self, settings: FirestoreSettings):
        self.settings = settings
        self._orders: list[dict] | None = None

    def health(self) -> dict:
        return {
            "status": "ok",
            "project": self.settings.project_id,
            "collection": self.settings.collection,
            "reachable": True,
            "sample": True,
        }

    def all_orders(self) -> list[dict]:
        if self._orders is not None:
            return [dict(order) for order in self._orders]

        today = datetime.now(self.settings.timezone).date()
        self._orders = [
            self._sample(
                "RJ20260712-101500-001",
                today,
                "New",
                "Maria Johnson",
                "Ring",
                ["Ring", "Resize", "Polish"],
                18500,
                5000,
            ),
            self._sample(
                "RJ20260711-164210-044",
                today - timedelta(days=1),
                "InWork",
                "Alex Smith",
                "Chain",
                ["Chain", "Soldering"],
                12000,
                4000,
            ),
            self._sample(
                "RJ20260710-120305-018",
                today - timedelta(days=2),
                "Ready",
                "Elena Garcia",
                "Watch",
                ["Watch", "Watch battery", "Polish"],
                8500,
                8500,
            ),
            self._sample(
                "RJ20260708-091100-112",
                today - timedelta(days=4),
                "PickedUp",
                "Victor Lee",
                "Bracelet",
                ["Bracelet", "Clasp replacement"],
                22000,
                22000,
            ),
        ]
        return [dict(order) for order in self._orders]

    def list_orders(
        self,
        *,
        status: str | None = None,
        query: str = "",
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        helper = FirestoreOrderRepository(self.settings)
        filtered = [
            order
            for order in self.all_orders()
            if helper._matches(order, query=query, date_from=date_from, date_to=date_to)
        ]
        status_counts = {item: 0 for item in VALID_STATUSES}
        for order in filtered:
            status_counts[order["orderStatus"]] += 1
        if status:
            filtered = [order for order in filtered if order["orderStatus"] == status]
        return {
            "items": filtered[offset : offset + limit],
            "total": len(filtered),
            "limit": limit,
            "offset": offset,
            "statusCounts": status_counts,
        }

    def update_status(self, order_id: str, status: str, user_email: str) -> dict | None:
        if self._orders is None:
            self.all_orders()
        for order in self._orders or []:
            if order["id"] == order_id:
                order["orderStatus"] = status
                order["lastModifiedDeviceId"] = "web-admin"
                order["updatedBy"] = user_email
                order["revision"] += 1
                return dict(order)
        return None

    def _sample(
        self,
        order_id: str,
        created_date: date,
        status: str,
        name: str,
        item_type: str,
        templates: list[str],
        total: int,
        deposit: int,
    ) -> dict:
        created = datetime.combine(created_date, datetime.min.time(), tzinfo=self.settings.timezone).replace(hour=11)
        photo = "/static/crown-gold-transparent.png"
        return {
            "id": order_id,
            "orderId": order_id,
            "customerName": name,
            "customerPhone": "(212) 555-0144",
            "customerAddress": "608 5th Ave, New York, NY",
            "orderStatus": status,
            "workDescription": ", ".join(templates),
            "workTemplates": templates,
            "totalPriceCents": total,
            "depositPaidCents": deposit,
            "balanceDueCents": max(total - deposit, 0),
            "itemPhotoUrls": [photo],
            "clientPhotoUrls": [photo],
            "signatureUrl": "",
            "labelPdfUrl": "",
            "labelPngUrl": "",
            "createdAt": created.isoformat(),
            "updatedAt": created.isoformat(),
            "createdDate": created_date,
            "revision": 1,
            "firestorePath": f"repairOrders/{order_id}",
            "lastModifiedDeviceId": "sample",
            "updatedBy": "",
            "statusRank": STATUS_ORDER[status],
            "sampleItemType": item_type,
        }
