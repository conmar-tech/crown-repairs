"""Firestore access for tablet repair orders."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha1
from zoneinfo import ZoneInfo

try:
    from google.api_core.exceptions import GoogleAPICallError, NotFound
    from google.auth.exceptions import GoogleAuthError
    from google.cloud import firestore
except ImportError:  # pragma: no cover - exercised only in minimal local test envs.
    class GoogleAPICallError(Exception):
        pass

    class GoogleAuthError(Exception):
        pass

    class NotFound(Exception):
        pass

    firestore = None


VALID_STATUSES = ("New", "InWork", "AtJeweler", "Ready", "PickedUp")
STATUS_ORDER = {status: index for index, status in enumerate(VALID_STATUSES)}


class RepairsRepositoryError(RuntimeError):
    """Raised when Firestore cannot be reached or decoded."""


def parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


@dataclass(frozen=True)
class FirestoreSettings:
    project_id: str
    collection: str
    clients_collection: str
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
            clients_collection=os.getenv("REPAIR_CLIENTS_COLLECTION", "clients"),
            timezone=ZoneInfo(os.getenv("APP_TIMEZONE", "America/New_York")),
        )


class FirestoreOrderRepository:
    def __init__(self, settings: FirestoreSettings):
        self.settings = settings
        self._client: firestore.Client | None = None

    @property
    def client(self) -> firestore.Client:
        if firestore is None:
            raise RepairsRepositoryError("google-cloud-firestore is not installed")
        if self._client is None:
            self._client = firestore.Client(project=self.settings.project_id)
        return self._client

    @property
    def collection(self):
        return self.client.collection(self.settings.collection)

    @property
    def clients_collection(self):
        return self.client.collection(self.settings.clients_collection)

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
            return [
                self._decode(snapshot)
                for snapshot in snapshots
                if not self._is_deleted(snapshot.to_dict() or {})
            ]
        except (GoogleAPICallError, GoogleAuthError) as exc:
            raise RepairsRepositoryError(str(exc)) from exc

    def all_clients(self) -> list[dict]:
        try:
            snapshots = self.clients_collection.order_by("nameSearch").limit(5000).stream(timeout=15)
            return [self._decode_client(snapshot) for snapshot in snapshots]
        except (GoogleAPICallError, GoogleAuthError) as exc:
            raise RepairsRepositoryError(str(exc)) from exc

    def list_orders(
        self,
        *,
        status: str | None = None,
        query: str = "",
        name: str = "",
        phone: str = "",
        code: str = "",
        period: str = "",
        client_key: str = "",
        client_name: str = "",
        client_phone: str = "",
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        if period and not date_from and not date_to:
            date_from, date_to = self._period_range(period)
        orders = self.all_orders()
        filtered = [
            order
            for order in orders
            if self._matches(
                order,
                query=query,
                name=name,
                phone=phone,
                code=code,
                client_key=client_key,
                client_name=client_name,
                client_phone=client_phone,
                date_from=date_from,
                date_to=date_to,
            )
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

    def update_status(
        self,
        order_id: str,
        status: str,
        user_email: str,
        settle_balance: bool = False,
    ) -> dict | None:
        if status not in VALID_STATUSES:
            raise ValueError(f"Unsupported status: {status}")

        reference = self.collection.document(order_id)
        try:
            snapshot = reference.get(timeout=10)
            if not snapshot.exists:
                return None
            data = snapshot.to_dict() or {}
            payment = data.get("payment") if isinstance(data.get("payment"), dict) else {}
            total_price_cents = self._int(payment.get("totalPriceCents"))
            updates = {
                "orderStatus": status,
                "revision": firestore.Increment(1),
                "updatedAt": firestore.SERVER_TIMESTAMP,
                "adminUpdatedAt": firestore.SERVER_TIMESTAMP,
                "updatedBy": user_email,
                "lastModifiedDeviceId": "web-admin",
                "source.lastModifiedDeviceId": "web-admin",
            }
            if status == "PickedUp" and settle_balance:
                updates["payment.depositPaidCents"] = total_price_cents
                updates["payment.balanceDueCents"] = 0

            reference.update(updates, timeout=10)
            return self._decode(reference.get(timeout=10))
        except NotFound:
            return None
        except (GoogleAPICallError, GoogleAuthError) as exc:
            raise RepairsRepositoryError(str(exc)) from exc

    def update_payment(
        self,
        order_id: str,
        total_price_cents: int,
        deposit_paid_cents: int,
        user_email: str,
    ) -> dict | None:
        total_price_cents = max(self._int(total_price_cents), 0)
        deposit_paid_cents = max(self._int(deposit_paid_cents), 0)
        balance_due_cents = max(total_price_cents - deposit_paid_cents, 0)

        reference = self.collection.document(order_id)
        try:
            snapshot = reference.get(timeout=10)
            if not snapshot.exists:
                return None

            reference.update(
                {
                    "payment.totalPriceCents": total_price_cents,
                    "payment.depositPaidCents": deposit_paid_cents,
                    "payment.balanceDueCents": balance_due_cents,
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

    def delete_order(self, order_id: str, user_email: str = "") -> bool:
        reference = self.collection.document(order_id)
        try:
            snapshot = reference.get(timeout=10)
            if not snapshot.exists:
                return False
            reference.update(
                {
                    "isDeleted": True,
                    "deletedAt": firestore.SERVER_TIMESTAMP,
                    "deletedBy": user_email,
                    "revision": firestore.Increment(1),
                    "updatedAt": firestore.SERVER_TIMESTAMP,
                    "adminUpdatedAt": firestore.SERVER_TIMESTAMP,
                    "updatedBy": user_email,
                    "lastModifiedDeviceId": "web-admin",
                    "source.lastModifiedDeviceId": "web-admin",
                },
                timeout=10,
            )
            return True
        except NotFound:
            return False
        except (GoogleAPICallError, GoogleAuthError) as exc:
            raise RepairsRepositoryError(str(exc)) from exc

    def list_clients(
        self,
        *,
        query: str = "",
        sort: str = "recent",
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        clients = self._build_client_summaries(self.all_clients(), self.all_orders())
        needle = query.strip().lower()
        digits = self._only_digits(query)
        if needle or digits:
            clients = [
                client for client in clients
                if self._client_matches(client, needle=needle, digits=digits)
            ]

        sort_key = sort if sort in {"name", "orders", "recent"} else "recent"
        if sort_key == "name":
            clients.sort(key=lambda item: (item["nameSearch"], item["name"].lower(), item["phoneDigits"]))
        elif sort_key == "orders":
            clients.sort(key=lambda item: (-item["orderCount"], item["nameSearch"], item["phoneDigits"]))
        else:
            clients.sort(key=lambda item: (-item["lastOrderMillis"], -item["orderCount"], item["nameSearch"]))

        total = len(clients)
        return {
            "items": clients[offset : offset + limit],
            "total": total,
            "limit": limit,
            "offset": offset,
            "sort": sort_key,
        }

    def _decode(self, snapshot) -> dict:
        data = snapshot.to_dict() or {}
        customer = data.get("customer") if isinstance(data.get("customer"), dict) else {}
        item_details = data.get("itemDetails") if isinstance(data.get("itemDetails"), dict) else {}
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
            "clientId": str(customer.get("clientId") or data.get("clientId") or ""),
            "customerName": str(customer.get("name") or ""),
            "customerPhone": str(customer.get("phone") or ""),
            "customerAddress": str(customer.get("address") or ""),
            "orderStatus": status,
            "itemDetails": {
                "material": str(item_details.get("material") or ""),
                "carat": str(item_details.get("carat") or ""),
                "size": str(item_details.get("size") or ""),
                "length": str(item_details.get("length") or ""),
            },
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
            "syncState": str(data.get("syncState") or "Synced"),
            "isDeleted": self._is_deleted(data),
            "deletedAt": self._timestamp_string(data.get("deletedAt")),
            "deletedBy": str(data.get("deletedBy") or ""),
        }
        order["statusRank"] = STATUS_ORDER.get(order["orderStatus"], 0)
        order["itemType"] = self._item_type(order["workTemplates"])
        order["serviceNames"] = self._service_names(order["workTemplates"])
        order["manualWorkNotes"] = self._manual_work_notes(order["workDescription"], order["workTemplates"])
        order["orderCodeSuffix"] = self._order_suffix(order["orderId"])
        return order

    def _decode_client(self, snapshot) -> dict:
        data = snapshot.to_dict() or {}
        name = str(data.get("name") or "")
        phone = str(data.get("phone") or "")
        phone_digits = str(data.get("phoneDigits") or self._only_digits(phone))
        source = data.get("source") if isinstance(data.get("source"), dict) else {}
        return {
            "id": snapshot.id,
            "key": f"id:{snapshot.id}",
            "name": name,
            "nameSearch": str(data.get("nameSearch") or self._search_text(name)),
            "alternateNames": self._string_list(data.get("alternateNames")),
            "phone": phone,
            "phoneDigits": phone_digits,
            "phones": self._string_list(data.get("phones")) or ([phone] if phone else []),
            "address": str(data.get("address") or ""),
            "rawAddress": str(data.get("rawAddress") or ""),
            "clientScope": str(data.get("clientScope") or ""),
            "customerType": str(data.get("customerType") or ""),
            "addressVerified": bool(data.get("addressVerified")),
            "addressVerificationStatus": str(data.get("addressVerificationStatus") or ""),
            "latestClientPhotoUrl": str(data.get("latestClientPhotoUrl") or data.get("clientPhotoUrl") or ""),
            "lastOrderId": str(data.get("lastOrderId") or ""),
            "sourceType": str(source.get("type") or ""),
            "updatedAt": self._timestamp_string(data.get("updatedAt")),
            "lastSeenAt": self._timestamp_string(data.get("lastSeenAt")),
            "orderCount": 0,
            "orderIds": [],
            "lastOrderAt": "",
            "lastOrderMillis": 0,
            "totalValueCents": 0,
            "openValueCents": 0,
            "dueCents": 0,
            "statusCounts": {item: 0 for item in VALID_STATUSES},
        }

    def _matches(
        self,
        order: dict,
        *,
        query: str,
        name: str,
        phone: str,
        code: str,
        client_key: str,
        client_name: str,
        client_phone: str,
        date_from: date | None,
        date_to: date | None,
    ) -> bool:
        order_date = order.get("createdDate")
        if date_from and isinstance(order_date, date) and order_date < date_from:
            return False
        if date_to and isinstance(order_date, date) and order_date > date_to:
            return False
        if not self._matches_client_filter(order, client_key, client_name, client_phone):
            return False
        if not self._matches_name_phone_code(order, name=name, phone=phone, code=code):
            return False

        needle = query.strip().lower()
        if not needle:
            return True
        phone_digits = self._only_digits(order.get("customerPhone", ""))
        query_digits = self._only_digits(needle)
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

    def _matches_name_phone_code(self, order: dict, *, name: str, phone: str, code: str) -> bool:
        name_needle = name.strip().lower()
        if name_needle and name_needle not in order.get("customerName", "").lower():
            return False

        phone_digits = self._only_digits(phone)
        order_phone_digits = self._only_digits(order.get("customerPhone", ""))
        if phone_digits and phone_digits not in order_phone_digits:
            return False

        code_needle = code.strip()
        if not code_needle:
            return True
        code_lower = code_needle.lower()
        code_digits = self._only_digits(code_needle)
        order_id = order.get("orderId", "")
        order_digits = self._only_digits(order_id)
        return (
            code_lower in order_id.lower()
            or (code_digits and code_digits in order_digits)
            or (len(code_digits) == 4 and order.get("orderCodeSuffix") == code_digits)
        )

    def _matches_client_filter(
        self,
        order: dict,
        client_key: str,
        client_name: str,
        client_phone: str,
    ) -> bool:
        key = client_key.strip()
        if not key:
            return True

        order_client_id = order.get("clientId", "")
        order_phone_digits = self._only_digits(order.get("customerPhone", ""))
        order_name = self._search_text(order.get("customerName", ""))
        client_phone_digits = self._only_digits(client_phone)
        client_name_search = self._search_text(client_name)

        if key.startswith("id:"):
            client_id = key.split(":", 1)[1]
            return (
                bool(client_id and order_client_id == client_id)
                or bool(client_phone_digits and client_phone_digits == order_phone_digits)
            )
        if key.startswith("phone:"):
            digits = key.split(":", 1)[1]
            return bool(digits and digits == order_phone_digits)
        if key.startswith("name:"):
            name_key = key.split(":", 1)[1]
            return bool(name_key and name_key == order_name)
        return (
            bool(client_phone_digits and client_phone_digits == order_phone_digits)
            or bool(client_name_search and client_name_search == order_name)
        )

    def _build_client_summaries(self, clients: list[dict], orders: list[dict]) -> list[dict]:
        by_key: dict[str, dict] = {}
        by_id: dict[str, dict] = {}
        by_phone: dict[str, dict] = {}

        for client in clients:
            summary = dict(client)
            summary["statusCounts"] = dict(client.get("statusCounts") or {})
            by_key[summary["key"]] = summary
            by_id[summary["id"]] = summary
            if summary.get("phoneDigits"):
                by_phone.setdefault(summary["phoneDigits"], summary)

        for order in orders:
            client = None
            if order.get("clientId"):
                client = by_id.get(order["clientId"])
            phone_digits = self._only_digits(order.get("customerPhone", ""))
            if client is None and phone_digits:
                client = by_phone.get(phone_digits)
            if client is None:
                client = self._synthetic_client(order)
                by_key[client["key"]] = client
                if client.get("phoneDigits"):
                    by_phone.setdefault(client["phoneDigits"], client)
            self._add_order_to_client(client, order)

        return list(by_key.values())

    def _synthetic_client(self, order: dict) -> dict:
        phone_digits = self._only_digits(order.get("customerPhone", ""))
        name = str(order.get("customerName") or "")
        key = f"phone:{phone_digits}" if phone_digits else f"name:{self._search_text(name)}"
        if key == "name:":
            key = "client:" + sha1(str(order.get("orderId", "")).encode("utf-8")).hexdigest()[:12]
        return {
            "id": "",
            "key": key,
            "name": name,
            "nameSearch": self._search_text(name),
            "alternateNames": [],
            "phone": str(order.get("customerPhone") or ""),
            "phoneDigits": phone_digits,
            "phones": [str(order.get("customerPhone") or "")] if order.get("customerPhone") else [],
            "address": str(order.get("customerAddress") or ""),
            "rawAddress": "",
            "clientScope": "repairs",
            "customerType": "retail",
            "addressVerified": False,
            "addressVerificationStatus": "",
            "latestClientPhotoUrl": (order.get("clientPhotoUrls") or [""])[0],
            "lastOrderId": "",
            "sourceType": "repair_order",
            "updatedAt": "",
            "lastSeenAt": "",
            "orderCount": 0,
            "orderIds": [],
            "lastOrderAt": "",
            "lastOrderMillis": 0,
            "totalValueCents": 0,
            "openValueCents": 0,
            "dueCents": 0,
            "statusCounts": {item: 0 for item in VALID_STATUSES},
        }

    def _add_order_to_client(self, client: dict, order: dict) -> None:
        order_id = order.get("orderId", "")
        if order_id and order_id not in client["orderIds"]:
            client["orderIds"].append(order_id)
            client["orderCount"] += 1
        client["lastOrderId"] = order_id or client.get("lastOrderId", "")
        if not client.get("name") and order.get("customerName"):
            client["name"] = order["customerName"]
            client["nameSearch"] = self._search_text(order["customerName"])
        if not client.get("phone") and order.get("customerPhone"):
            client["phone"] = order["customerPhone"]
            client["phoneDigits"] = self._only_digits(order["customerPhone"])
        if not client.get("address") and order.get("customerAddress"):
            client["address"] = order["customerAddress"]
        if not client.get("latestClientPhotoUrl") and order.get("clientPhotoUrls"):
            client["latestClientPhotoUrl"] = order["clientPhotoUrls"][0]

        total = self._int(order.get("totalPriceCents"))
        due = self._int(order.get("balanceDueCents"))
        client["totalValueCents"] += total
        client["dueCents"] += due
        if order.get("orderStatus") != "PickedUp":
            client["openValueCents"] += total
        status = order.get("orderStatus")
        if status in client["statusCounts"]:
            client["statusCounts"][status] += 1

        created_at = parse_iso_datetime(order.get("createdAt", ""))
        if created_at:
            millis = int(created_at.timestamp() * 1000)
            if millis > client["lastOrderMillis"]:
                client["lastOrderMillis"] = millis
                client["lastOrderAt"] = order.get("createdAt", "")

    def _client_matches(self, client: dict, *, needle: str, digits: str) -> bool:
        haystack = " ".join(
            [
                client.get("name", ""),
                client.get("nameSearch", ""),
                " ".join(client.get("alternateNames", [])),
                client.get("phone", ""),
                " ".join(client.get("phones", [])),
                client.get("address", ""),
                client.get("lastOrderId", ""),
            ]
        ).lower()
        phone_digits = client.get("phoneDigits", "")
        return (needle and needle in haystack) or (digits and digits in phone_digits)

    def _period_range(self, period: str) -> tuple[date | None, date | None]:
        today = datetime.now(self.settings.timezone).date()
        if period == "today":
            return today, today
        if period == "week":
            return today - timedelta(days=6), today
        if period == "month":
            return today.replace(day=1), today
        return None, None

    def _business_date(self, value) -> date | None:
        if isinstance(value, datetime):
            return value.astimezone(self.settings.timezone).date()
        return None

    def _timestamp_string(self, value) -> str:
        if isinstance(value, datetime):
            return value.astimezone(self.settings.timezone).isoformat()
        return ""

    @staticmethod
    def _is_deleted(data: dict) -> bool:
        return bool(data.get("isDeleted")) or data.get("deletedAt") is not None

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

    @staticmethod
    def _only_digits(value: str) -> str:
        return "".join(ch for ch in str(value) if ch.isdigit())

    @staticmethod
    def _search_text(value: str) -> str:
        return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in str(value)).split())

    @staticmethod
    def _item_type(templates: list[str]) -> str:
        prefix = "Item:"
        for template in templates:
            if template.startswith(prefix):
                return template.removeprefix(prefix).strip()
        return templates[0] if templates else ""

    @staticmethod
    def _service_names(templates: list[str]) -> list[str]:
        return [item for item in templates if item and not item.startswith("Item:")]

    @staticmethod
    def _manual_work_notes(description: str, templates: list[str]) -> str:
        prefix = ", ".join(templates)
        if not prefix or not description.startswith(prefix):
            return ""
        return description.removeprefix(prefix).removeprefix(":").strip()

    @staticmethod
    def _order_suffix(order_id: str) -> str:
        digits = ""
        for char in reversed(str(order_id)):
            if not char.isdigit():
                break
            digits = char + digits
        return digits[-4:]


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
            return [dict(order) for order in self._orders if not order.get("isDeleted")]

        today = datetime.now(self.settings.timezone).date()
        self._orders = [
            self._sample(
                "R260712-0001",
                today,
                "New",
                "Maria Johnson",
                "Ring",
                ["Item: Ring", "Resize", "Polish"],
                18500,
                5000,
            ),
            self._sample(
                "C260711-0002",
                today - timedelta(days=1),
                "InWork",
                "Alex Smith",
                "Chain",
                ["Item: Chain", "Soldering"],
                12000,
                4000,
            ),
            self._sample(
                "W260710-0003",
                today - timedelta(days=2),
                "AtJeweler",
                "Dana Brown",
                "Watch",
                ["Item: Watch", "Battery"],
                9500,
                2500,
            ),
            self._sample(
                "R260710-0004",
                today - timedelta(days=2),
                "Ready",
                "Elena Garcia",
                "Ring",
                ["Item: Ring", "Resize", "Polish"],
                8500,
                8500,
            ),
            self._sample(
                "B260708-0005",
                today - timedelta(days=4),
                "PickedUp",
                "Victor Lee",
                "Bracelet",
                ["Item: Bracelet", "Clasp replacement"],
                22000,
                22000,
            ),
        ]
        return [dict(order) for order in self._orders if not order.get("isDeleted")]

    def list_orders(
        self,
        *,
        status: str | None = None,
        query: str = "",
        name: str = "",
        phone: str = "",
        code: str = "",
        period: str = "",
        client_key: str = "",
        client_name: str = "",
        client_phone: str = "",
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        helper = FirestoreOrderRepository(self.settings)
        if period and not date_from and not date_to:
            date_from, date_to = helper._period_range(period)
        filtered = [
            order
            for order in self.all_orders()
            if helper._matches(
                order,
                query=query,
                name=name,
                phone=phone,
                code=code,
                client_key=client_key,
                client_name=client_name,
                client_phone=client_phone,
                date_from=date_from,
                date_to=date_to,
            )
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

    def update_status(
        self,
        order_id: str,
        status: str,
        user_email: str,
        settle_balance: bool = False,
    ) -> dict | None:
        if self._orders is None:
            self.all_orders()
        for order in self._orders or []:
            if order["id"] == order_id:
                order["orderStatus"] = status
                if status == "PickedUp" and settle_balance:
                    order["depositPaidCents"] = order["totalPriceCents"]
                    order["balanceDueCents"] = 0
                order["lastModifiedDeviceId"] = "web-admin"
                order["updatedBy"] = user_email
                order["revision"] += 1
                order["statusRank"] = STATUS_ORDER.get(status, 0)
                return dict(order)
        return None

    def update_payment(
        self,
        order_id: str,
        total_price_cents: int,
        deposit_paid_cents: int,
        user_email: str,
    ) -> dict | None:
        if self._orders is None:
            self.all_orders()
        total_price_cents = max(FirestoreOrderRepository._int(total_price_cents), 0)
        deposit_paid_cents = max(FirestoreOrderRepository._int(deposit_paid_cents), 0)
        for order in self._orders or []:
            if order["id"] == order_id:
                order["totalPriceCents"] = total_price_cents
                order["depositPaidCents"] = deposit_paid_cents
                order["balanceDueCents"] = max(total_price_cents - deposit_paid_cents, 0)
                order["lastModifiedDeviceId"] = "web-admin"
                order["updatedBy"] = user_email
                order["revision"] += 1
                return dict(order)
        return None

    def delete_order(self, order_id: str, user_email: str = "") -> bool:
        if self._orders is None:
            self.all_orders()
        for order in self._orders or []:
            if order["id"] == order_id:
                order["isDeleted"] = True
                order["deletedAt"] = datetime.now(self.settings.timezone).isoformat()
                order["deletedBy"] = user_email
                order["lastModifiedDeviceId"] = "web-admin"
                order["updatedBy"] = user_email
                order["revision"] += 1
                return True
        return False

    def all_clients(self) -> list[dict]:
        helper = FirestoreOrderRepository(self.settings)
        return helper._build_client_summaries([], self.all_orders())

    def list_clients(
        self,
        *,
        query: str = "",
        sort: str = "recent",
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        helper = FirestoreOrderRepository(self.settings)
        clients = self.all_clients()
        needle = query.strip().lower()
        digits = helper._only_digits(query)
        if needle or digits:
            clients = [
                client for client in clients
                if helper._client_matches(client, needle=needle, digits=digits)
            ]
        if sort == "name":
            clients.sort(key=lambda item: item["nameSearch"])
        elif sort == "orders":
            clients.sort(key=lambda item: (-item["orderCount"], item["nameSearch"]))
        else:
            clients.sort(key=lambda item: (-item["lastOrderMillis"], item["nameSearch"]))
        return {
            "items": clients[offset : offset + limit],
            "total": len(clients),
            "limit": limit,
            "offset": offset,
            "sort": sort if sort in {"name", "orders", "recent"} else "recent",
        }

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
        phone_suffix = FirestoreOrderRepository._order_suffix(order_id).rjust(4, "0")
        return {
            "id": order_id,
            "orderId": order_id,
            "clientId": "",
            "customerName": name,
            "customerPhone": f"(212) 555-{phone_suffix}",
            "customerAddress": "608 5th Ave, New York, NY",
            "orderStatus": status,
            "itemDetails": self._sample_item_details(item_type),
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
            "syncState": "Synced",
            "isDeleted": False,
            "deletedAt": "",
            "deletedBy": "",
            "itemType": FirestoreOrderRepository._item_type(templates),
            "serviceNames": FirestoreOrderRepository._service_names(templates),
            "manualWorkNotes": "",
            "orderCodeSuffix": FirestoreOrderRepository._order_suffix(order_id),
        }

    @staticmethod
    def _sample_item_details(item_type: str) -> dict:
        if item_type == "Ring":
            return {"material": "Yellow Gold", "carat": "14K", "size": "7", "length": ""}
        if item_type == "Chain":
            return {"material": "White Gold", "carat": "18K", "size": "", "length": "18 in"}
        if item_type == "Bracelet":
            return {"material": "Silver", "carat": "", "size": "", "length": "7 in"}
        return {"material": "", "carat": "", "size": "", "length": ""}
