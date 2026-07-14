# Crown Repairs

Internal Crown Jewelry web panel for repair orders created on the Android tablet app.

The panel intentionally does not include the customer intake form. It provides only staff/admin views:

- **Orders**: repair order cards, customer/item photos, status/date/client/code filters, deletion, and status changes.
- **Clients**: searchable client list with order counts and one-click filtering back to Orders.
- **Finances**: totals, open order value, ready value, due balance, and week/month/year charts.

The UI follows the same lightweight pattern as `conmar-tech/crown-kassa`: FastAPI, Jinja templates, static CSS/JavaScript, Google Sign-In, and Cloud Run-ready packaging.

## Data Source

The app reads and updates Firestore documents in:

```text
projects/eloquent-branch-414417/databases/(default)/documents/repairOrders
```

Photos, signatures, and labels are read from Firebase Storage download URLs already written into each Firestore order by the tablet app.

Legacy customer contacts imported from scanned repair cards are stored in:

```text
projects/eloquent-branch-414417/databases/(default)/documents/clients
```

See [docs/CLIENT_IMPORT.md](docs/CLIENT_IMPORT.md) for the import process and client document shape.
Imported legacy clients are tagged as `customerType=retail` and `clientScope=repairs` so they can later sync into the shared Google SQL CRM without changing the tablet workflow.

When a status is changed in the web panel, the document is updated with:

- `orderStatus`
- `revision = revision + 1`
- `updatedAt = server timestamp`
- `adminUpdatedAt = server timestamp`
- `updatedBy`
- `lastModifiedDeviceId = "web-admin"`
- `source.lastModifiedDeviceId = "web-admin"`

If a `Ready` order is marked `PickedUp` while it still has `balanceDueCents > 0`, the UI asks for confirmation. Confirming sends `settleBalance=true`, which sets `payment.depositPaidCents` to the order total and `payment.balanceDueCents` to `0`.

Deleting an order removes the `repairOrders/{orderId}` Firestore document after a browser confirmation. Storage files are not deleted by this action.

The tablet app uses those fields to pull the cloud change back into its local Room database.

## Local Run

Requires Python 3.11+ and Google Application Default Credentials with Firestore access:

```powershell
cd C:\Projects\crown-repairs
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

gcloud auth application-default login

$env:AUTH_DISABLED = "true"
$env:FIREBASE_PROJECT_ID = "eloquent-branch-414417"
python main.py
```

Open `http://127.0.0.1:8000`.

`AUTH_DISABLED=true` is for local development only.

If local Application Default Credentials are not available yet, use sample data to verify the UI:

```powershell
$env:AUTH_DISABLED = "true"
$env:REPAIRS_SAMPLE_DATA = "true"
python main.py
```

## Environment Variables

| Variable | Purpose |
|---|---|
| `FIREBASE_PROJECT_ID` | Google/Firebase project id, defaults to `eloquent-branch-414417` |
| `REPAIR_ORDERS_COLLECTION` | Firestore collection, defaults to `repairOrders` |
| `REPAIR_CLIENTS_COLLECTION` | Firestore clients collection, defaults to `clients` |
| `APP_TIMEZONE` | Business timezone, defaults to `America/New_York` |
| `GOOGLE_CLIENT_ID` | Google OAuth web client id |
| `SESSION_SECRET` | HMAC session signing key |
| `SESSION_HOURS` | Session lifetime, defaults to 12 |
| `ALLOWED_EMAILS` | Comma-separated Google account allowlist |
| `AUTH_DISABLED` | Local-only auth bypass |
| `REPAIRS_SAMPLE_DATA` | Local-only sample orders instead of Firestore |

## Tests

```powershell
$env:PYTHONPATH = "$PWD"
python -m pytest tests -q
```

## Deployment Notes

For Cloud Run, use a service account with Firestore access to the Firebase project. The web app uses Google Cloud IAM credentials, not a Firebase Admin SDK JSON key committed to the repository.

See [docs/TECHNICAL_SPEC.md](docs/TECHNICAL_SPEC.md) for implementation details.
