# Crown Repairs Technical Specification

## Purpose

`crown-repairs` is the internal web/admin panel for repair orders entered on the Samsung tablet application `crown-tablet`.

The web panel is not a customer-facing intake app. It is for staff and management to review orders, change workflow status, and inspect finance totals.

## Stack

- FastAPI application.
- Jinja2 HTML templates.
- Static JavaScript and CSS.
- Google Sign-In with signed app sessions.
- Google Cloud Firestore as the order database.
- Firebase Storage download URLs for photos and generated files.
- Docker/Cloud Run compatible runtime.

## Firestore Contract

Collection:

```text
repairOrders/{orderId}
```

Expected fields:

```text
orderId: string
customer: {
  clientId?: string
  name: string
  phone: string
  address: string
}
orderStatus: "New" | "InWork" | "AtJeweler" | "Ready" | "PickedUp"
workDescription: string
workTemplates: string[]
payment: {
  totalPriceCents: number
  depositPaidCents: number
  balanceDueCents: number
}
createdAt: timestamp
updatedAt: timestamp
revision: number
createdByDeviceId: string
lastModifiedDeviceId: string
files: {
  itemPhotoUrl?: string
  clientPhotoUrl?: string
  itemPhotoUrls?: string[]
  clientPhotoUrls?: string[]
  signatureUrl?: string
  labelPdfUrl?: string
  labelPngUrl?: string
}
```

Client collection:

```text
clients/{clientId}
```

The web panel reads this collection to build the Clients tab, then merges order-derived counts and totals into each client card. Orders without a matching client document are grouped by phone number.

## Status Update Flow

When staff changes a status in the web panel, the server updates the Firestore document directly:

```text
orderStatus = selected status
revision = increment by 1
updatedAt = server timestamp
adminUpdatedAt = server timestamp
updatedBy = signed-in Google account
lastModifiedDeviceId = "web-admin"
source.lastModifiedDeviceId = "web-admin"
```

When the web panel marks an order as `PickedUp` with `settleBalance=true`, it also writes:

```text
payment.depositPaidCents = payment.totalPriceCents
payment.balanceDueCents = 0
```

The tablet app watches/pulls these fields. Because `lastModifiedDeviceId` differs from the tablet's generated `deviceId`, the tablet treats the document as a cloud-origin update and merges it into Room.

## Orders Screen

The Orders screen provides:

- status count cards for all workflow states;
- status filters for `AtJeweler`, `InWork`, `Ready`, and `PickedUp`;
- separate filters for customer name, phone, order id/barcode, date range, and quick date periods `Today`, `Week`, and `Month`;
- paginated cards;
- client photo in the top-left of each card;
- jewelry photos as thumbnails in the bottom row;
- signature and label links when available;
- inline status change;
- `Picked Up` shortcut on Ready cards;
- Firestore document deletion after browser confirmation.

Status backgrounds:

- `New`: light beige.
- `InWork`: light red/pink.
- `AtJeweler`: warm amber.
- `Ready`: light green.
- `PickedUp`: normal white archival background.

## Clients Screen

The Clients screen reads `clients` and `repairOrders`, then shows:

- search by name or phone;
- sorting by recent order, name, or order count;
- total order count, due amount, and last order date per client;
- click-through to Orders with that client preselected as an active filter.

## Finances Screen

The Finances screen computes from repair orders:

- total value of orders in the period;
- open value for `New + InWork + AtJeweler + Ready`;
- ready value not yet picked up;
- due balance for open orders;
- deposits collected;
- week/month/year order value charts.

All money is stored and calculated in cents.

## Authentication

The app uses the same Google Sign-In pattern as `crown-kassa`.

Only emails in `ALLOWED_EMAILS` can sign in. The default allowlist is:

```text
shop@crownjewelryrepair.com,serg@crownjewelryrepair.com
```

In production, `SESSION_SECRET` and `GOOGLE_CLIENT_ID` are required. `AUTH_DISABLED=true` is only for local development.

## Deployment

Cloud Run service account needs Firestore access in project `eloquent-branch-414417`.

No Firebase service account JSON key should be committed or stored in the application directory.
