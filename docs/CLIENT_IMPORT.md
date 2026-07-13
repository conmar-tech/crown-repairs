# Legacy Client Import

This project stores imported legacy repair customers in Firestore:

```text
clients/{clientId}
```

The import keeps order/work history only as source trace data. The operational
client fields are:

```text
name
nameSearch
alternateNames[]
phone
phoneDigits
phones[]
address
rawAddress
addressVerified
addressVerificationStatus
addressVerification
addressCandidates[]
source
sourceRecords[]
legacyNumbers[]
legacyFiles[]
createdAt
updatedAt
```

## Source Tables

The importer reads the local legacy tables from:

```text
M:\My Drive\Clients
```

Required sources:

- `Customers_cleaned.xlsx`
- `October/clients165.csv`
- `contact_error_import_20240920172321_33.csv`

Optional source:

- `imports/google_sheets/customers_gid0.tsv`

The optional TSV was copied from the private Google Sheet:

```text
https://docs.google.com/spreadsheets/d/1ULEzPvmzx3UM0bj_3B1pn9_yLb8cvEQksi6B4HB1zaE/edit?gid=0#gid=0
```

Do not commit files under `imports/`; they contain customer names, phones, and addresses.

## Address Verification

Addresses are checked with Google Places Text Search. OCR addresses are preserved
as `rawAddress`; when Google returns a reliable street-address result, the
formatted Google address is saved as `address` and `addressVerified=true`.

Statuses:

- `validated`: Google returned a street address and the street number matched.
- `number_mismatch`: Google returned a result, but the street number did not match.
- `partial`: Google returned a route/place-level result instead of a full address.
- `not_found`: Google returned no result.
- `missing`: no address was available in the source record.

## Run

Dry-run:

```powershell
python scripts\import_clients_to_firestore.py
```

Commit to Firestore:

```powershell
python scripts\import_clients_to_firestore.py --commit
```

The script writes:

- `client_import_jobs/{jobId}`
- `client_import_records/{jobId_recordId}`
- `clients/{clientId}`

It uses `gcloud auth print-access-token` for Firestore REST writes, so the active
Google Cloud account must have Firestore write access to project
`eloquent-branch-414417`.

## Import Result

Committed job:

```text
manual_clients_import_20260713_162856
```

Result:

- Source records: `1,590`
- Unique clients: `949`
- Raw addresses checked: `1,031`
- Clients with verified Google address: `529`
- Clients without source address: `280`

Firestore verification after import:

```text
clients_total = 949
client_import_records_for_job = 1590
```
