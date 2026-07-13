"""Import legacy repair clients into Firestore.

The importer reads OCR/export tables from the local Google Drive Clients folder,
normalizes client contact fields, validates addresses through Google Places, and
upserts a deduplicated `clients` collection for the repairs/tablet workflow.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib import error, request

import pandas as pd

DEFAULT_PROJECT = "eloquent-branch-414417"
DEFAULT_CLIENTS_ROOT = Path(r"M:\My Drive\Clients")
DEFAULT_GOOGLE_SHEET_TSV = Path("imports/google_sheets/customers_gid0.tsv")
DEFAULT_VALIDATION_CACHE = Path("imports/address_validation_cache.json")
DEFAULT_TABLES = {
    "Customers_cleaned.xlsx",
    "October/clients165.csv",
    "contact_error_import_20240920172321_33.csv",
}

STREET_SUFFIXES = (
    "aly",
    "alley",
    "ave",
    "avenue",
    "blvd",
    "boulevard",
    "cir",
    "circle",
    "ct",
    "court",
    "dr",
    "drive",
    "hwy",
    "highway",
    "ln",
    "lane",
    "pkwy",
    "parkway",
    "pl",
    "place",
    "rd",
    "road",
    "sq",
    "square",
    "st",
    "street",
    "ter",
    "terrace",
    "trl",
    "trail",
    "way",
)

WORK_WORDS = re.compile(
    r"\b(batt|battery|bracelet|chain|crystal|earring|estimate|fix|link|lock|"
    r"polish|resize|ring|solder|watch)\b",
    re.IGNORECASE,
)
STREET_WORDS = re.compile(r"\b(" + "|".join(map(re.escape, STREET_SUFFIXES)) + r")\b", re.IGNORECASE)
ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def only_digits(value: Any) -> str:
    return re.sub(r"\D", "", text(value))


def normalize_phone(value: Any) -> str:
    digits = only_digits(value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return ""


def normalize_name(value: Any) -> str:
    name = re.sub(r"\s+", " ", text(value))
    name = re.sub(r"\s*\(sample\)\s*", "", name, flags=re.IGNORECASE)
    return name.strip(" ,;")


def title_for_search(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def normalize_money(value: Any) -> str:
    raw = text(value).replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", raw)
    if not match:
        return ""
    try:
        return f"{float(match.group(0)):.2f}"
    except ValueError:
        return ""


def compose_address(address: Any = "", city: Any = "", state: Any = "", zip_code: Any = "") -> str:
    parts = [
        text(address),
        text(city),
        text(state).upper()[:2],
        only_digits(zip_code)[:5],
    ]
    return ", ".join(part for part in parts if part)


def looks_like_address(value: Any) -> bool:
    raw = text(value)
    if not raw or not re.search(r"^\s*\d{1,6}\s+\S+", raw):
        return False
    has_street_word = bool(STREET_WORDS.search(raw))
    has_state_or_zip = bool(re.search(r"\bMD\b", raw, flags=re.IGNORECASE) or ZIP_RE.search(raw))
    if not (has_street_word or has_state_or_zip):
        return False
    if WORK_WORDS.search(raw) and not has_state_or_zip and not has_street_word:
        return False
    return True


def address_number(value: str) -> str:
    match = re.match(r"\s*(\d{1,6})\b", value)
    return match.group(1) if match else ""


def clean_source_file(value: Any) -> str:
    return text(value).replace("\\", "/")


def record_id_from_source(source: str, row_index: int, record: dict[str, Any]) -> str:
    material = json.dumps(
        {"source": source, "rowIndex": row_index, "number": record.get("number"), "file": record.get("file")},
        sort_keys=True,
    )
    return hashlib.sha1(material.encode("utf-8")).hexdigest()[:20]


def client_doc_id(record: dict[str, Any]) -> str:
    phone_digits = record.get("phoneDigits") or ""
    if phone_digits:
        return f"phone_{phone_digits}"
    material = "|".join(
        [
            title_for_search(record.get("customer", "")),
            title_for_search(record.get("rawAddress", "")),
            record.get("number", ""),
        ]
    )
    return "client_" + hashlib.sha1(material.encode("utf-8")).hexdigest()[:20]


def load_records(root: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []

    def add_record(
        *,
        source: str,
        row_index: int,
        number: Any = "",
        customer: Any = "",
        phone: Any = "",
        address: Any = "",
        city: Any = "",
        state: Any = "",
        zip_code: Any = "",
        instructions: Any = "",
        deposit: Any = "",
        total: Any = "",
        file: Any = "",
        raw: dict[str, Any] | None = None,
    ) -> None:
        name = normalize_name(customer)
        normalized_phone = normalize_phone(phone)
        rec = {
            "source": source,
            "rowIndex": row_index,
            "number": text(number),
            "customer": name,
            "phone": normalized_phone,
            "phoneDigits": only_digits(normalized_phone),
            "address": text(address),
            "city": text(city),
            "state": text(state).upper()[:2],
            "zip": only_digits(zip_code)[:5],
            "rawAddress": compose_address(address, city, state, zip_code),
            "instructions": text(instructions),
            "deposit": normalize_money(deposit),
            "total": normalize_money(total),
            "file": clean_source_file(file),
            "raw": raw or {},
        }
        if rec["customer"] and (rec["phoneDigits"] or rec["rawAddress"]):
            rec["id"] = record_id_from_source(source, row_index, rec)
            rec["clientDocId"] = client_doc_id(rec)
            records.append(rec)

    cleaned_path = root / "Customers_cleaned.xlsx"
    cleaned = pd.read_excel(cleaned_path, dtype=str, keep_default_na=False)
    for index, row in cleaned.iterrows():
        add_record(
            source="Customers_cleaned.xlsx",
            row_index=int(index) + 2,
            number=row.get("Number", ""),
            customer=row.get("Customer", ""),
            phone=row.get("Phone", ""),
            address=row.get("Address", ""),
            city=row.get("City", ""),
            state=row.get("State", ""),
            zip_code=row.get("ZIP", ""),
            instructions=row.get("Instructions", ""),
            deposit=row.get("Deposit", ""),
            total=row.get("Total", ""),
            file=row.get("File", ""),
            raw={str(k): text(v) for k, v in row.to_dict().items()},
        )

    if DEFAULT_GOOGLE_SHEET_TSV.exists():
        sheet = pd.read_csv(DEFAULT_GOOGLE_SHEET_TSV, dtype=str, keep_default_na=False, sep="\t")
        for index, row in sheet.iterrows():
            add_record(
                source="GoogleSheet/Customers_gid0.tsv",
                row_index=int(index) + 2,
                number=row.get("Number", ""),
                customer=row.get("Customer", ""),
                phone=row.get("Phone", ""),
                address=row.get("Address", ""),
                city=row.get("City", ""),
                state=row.get("State", ""),
                zip_code=row.get("ZIP", ""),
                instructions=row.get("Instructions", ""),
                deposit=row.get("Deposit", ""),
                total=row.get("Total", ""),
                file=row.get("File", ""),
                raw={str(k): text(v) for k, v in row.to_dict().items()},
            )

    october_path = root / "October" / "clients165.csv"
    october = pd.read_csv(october_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    for index, row in october.iterrows():
        add_record(
            source="October/clients165.csv",
            row_index=int(index) + 2,
            number=row.get("number", ""),
            customer=row.get("customer", ""),
            phone=row.get("phone", ""),
            address=row.get("address", ""),
            city=row.get("city", ""),
            state=row.get("state", ""),
            zip_code=row.get("zip", ""),
            instructions=row.get("instructions", ""),
            deposit=row.get("deposit", ""),
            total=row.get("total", ""),
            file=row.get("file", ""),
            raw={str(k): text(v) for k, v in row.to_dict().items()},
        )

    error_path = root / "contact_error_import_20240920172321_33.csv"
    error_rows = pd.read_csv(error_path, dtype=str, keep_default_na=False, encoding="utf-8-sig")
    for index, row in error_rows.iterrows():
        name = row.get("Client's Name", "") or " ".join(
            part for part in [row.get("First Name", ""), row.get("Last Name", "")] if text(part)
        )
        address = row.get("Home Address", "")
        instructions = row.get("Type of Work", "")
        if not text(address) and looks_like_address(instructions):
            address = instructions
            instructions = ""
        add_record(
            source="contact_error_import_20240920172321_33.csv",
            row_index=int(index) + 2,
            customer=name,
            phone=row.get("Cell-Phone", "") or row.get("Home Number", ""),
            address=address,
            instructions=instructions,
            raw={str(k): text(v) for k, v in row.to_dict().items()},
        )

    return records


def load_places_key(args: argparse.Namespace) -> str:
    if args.skip_google:
        return ""
    if args.places_api_key:
        return args.places_api_key.strip()
    if os.getenv("GOOGLE_PLACES_API_KEY"):
        return os.environ["GOOGLE_PLACES_API_KEY"].strip()
    key_file = Path(args.places_api_key_file) if args.places_api_key_file else None
    if key_file and key_file.exists():
        for line in key_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("GOOGLE_PLACES_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def post_json(url: str, body: dict[str, Any], headers: dict[str, str], timeout: int = 15) -> dict[str, Any]:
    payload = json.dumps(body).encode("utf-8")
    req = request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def validate_one_address(raw_address: str, api_key: str, retries: int = 4, rate_delay_s: float = 0.25) -> dict[str, Any]:
    if not raw_address:
        return {"status": "missing", "rawAddress": raw_address}
    body = {
        "textQuery": raw_address,
        "languageCode": "en",
        "regionCode": "US",
    }
    headers = {
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.id,places.formattedAddress,places.addressComponents,places.types,places.displayName",
    }
    last_error = ""
    for attempt in range(retries + 1):
        try:
            response = post_json("https://places.googleapis.com/v1/places:searchText", body, headers)
            if rate_delay_s:
                time.sleep(rate_delay_s)
            places = response.get("places") or []
            if not places:
                return {"status": "not_found", "rawAddress": raw_address}
            place = places[0]
            formatted = place.get("formattedAddress", "")
            components = place.get("addressComponents") or []
            comp_types = [t for c in components for t in c.get("types", [])]
            raw_no = address_number(raw_address)
            formatted_no = address_number(formatted)
            has_street = "street_number" in comp_types and "route" in comp_types
            number_matches = bool(raw_no and formatted_no and raw_no == formatted_no)
            status = "validated"
            if raw_no and not number_matches:
                status = "number_mismatch"
            elif not has_street:
                status = "partial"
            return {
                "status": status,
                "rawAddress": raw_address,
                "formattedAddress": formatted,
                "placeId": place.get("id", ""),
                "displayName": (place.get("displayName") or {}).get("text", ""),
                "types": place.get("types") or [],
                "addressComponents": components,
                "streetNumberMatches": number_matches,
            }
        except error.HTTPError as exc:
            last_error = f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')[:300]}"
            if exc.code == 429:
                time.sleep(5 * (attempt + 1))
            elif exc.code not in {500, 502, 503, 504}:
                break
        except Exception as exc:  # noqa: BLE001 - report import validation errors in output
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(max(rate_delay_s, 0.5 * (attempt + 1)))
    return {"status": "error", "rawAddress": raw_address, "error": last_error}


def load_validation_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, dict)}


def save_validation_cache(path: Path, validations: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(validations, ensure_ascii=False, indent=2), encoding="utf-8")


def validate_addresses(
    records: list[dict[str, Any]],
    api_key: str,
    workers: int,
    cache_path: Path,
    rate_delay_s: float,
) -> dict[str, dict[str, Any]]:
    addresses = sorted({record["rawAddress"] for record in records if record.get("rawAddress")})
    cached = load_validation_cache(cache_path)
    results = {
        address: cached[address]
        for address in addresses
        if address in cached and cached[address].get("status") != "error"
    }
    missing = [address for address in addresses if address not in results]

    if not api_key:
        skipped = {addr: {"status": "skipped", "rawAddress": addr} for addr in missing}
        return {**results, **skipped}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {
            executor.submit(validate_one_address, address, api_key, rate_delay_s=rate_delay_s): address
            for address in missing
        }
        completed = 0
        total = len(futures)
        for future in as_completed(futures):
            address = futures[future]
            completed += 1
            results[address] = future.result()
            if completed % 25 == 0:
                save_validation_cache(cache_path, results)
            if completed % 50 == 0 or completed == total:
                print(
                    f"Validated {completed}/{total} addresses ({len(addresses) - total} reused from cache)",
                    flush=True,
                )
    save_validation_cache(cache_path, results)
    return results


def choose_primary(records: list[dict[str, Any]], validations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    def score(record: dict[str, Any]) -> tuple[int, int, int, int]:
        validation = validations.get(record.get("rawAddress", ""), {})
        status = validation.get("status")
        source_score = {
            "Customers_cleaned.xlsx": 4,
            "October/clients165.csv": 3,
            "GoogleSheet/Customers_gid0.tsv": 3,
            "contact_error_import_20240920172321_33.csv": 2,
        }.get(record.get("source"), 1)
        validation_score = {"validated": 4, "partial": 2, "number_mismatch": 1}.get(status, 0)
        return (
            1 if record.get("phoneDigits") else 0,
            validation_score,
            1 if record.get("rawAddress") else 0,
            source_score,
        )

    return sorted(records, key=score, reverse=True)[0]


def build_clients(records: list[dict[str, Any]], validations: dict[str, dict[str, Any]], job_id: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["clientDocId"]].append(record)

    now = utc_now()
    clients: list[dict[str, Any]] = []
    for doc_id, group in sorted(grouped.items()):
        primary = choose_primary(group, validations)
        validation = validations.get(primary.get("rawAddress", ""), {"status": "missing"})
        names = sorted({r["customer"] for r in group if r.get("customer")})
        phones = sorted({r["phone"] for r in group if r.get("phone")})
        address_candidates = []
        seen_addresses = set()
        for record in group:
            raw = record.get("rawAddress", "")
            if not raw or raw.lower() in seen_addresses:
                continue
            seen_addresses.add(raw.lower())
            address_candidates.append(
                {
                    "rawAddress": raw,
                    "formattedAddress": validations.get(raw, {}).get("formattedAddress", ""),
                    "status": validations.get(raw, {}).get("status", "missing"),
                    "source": record.get("source", ""),
                    "number": record.get("number", ""),
                    "file": record.get("file", ""),
                }
            )

        formatted = validation.get("formattedAddress", "")
        client = {
            "id": doc_id,
            "name": primary.get("customer", ""),
            "nameSearch": title_for_search(primary.get("customer", "")),
            "alternateNames": [name for name in names if name != primary.get("customer", "")],
            "phone": primary.get("phone", ""),
            "phoneDigits": primary.get("phoneDigits", ""),
            "phones": phones,
            "customerType": "retail",
            "clientScope": "repairs",
            "address": formatted or primary.get("rawAddress", ""),
            "rawAddress": primary.get("rawAddress", ""),
            "addressVerified": validation.get("status") == "validated",
            "addressVerificationStatus": validation.get("status", "missing"),
            "addressVerification": validation,
            "addressCandidates": address_candidates[:20],
            "city": primary.get("city", ""),
            "state": primary.get("state", ""),
            "zip": primary.get("zip", ""),
            "source": {
                "type": "legacy_card_import",
                "jobId": job_id,
                "primaryTable": primary.get("source", ""),
                "primaryRowIndex": primary.get("rowIndex", 0),
                "primaryNumber": primary.get("number", ""),
                "primaryFile": primary.get("file", ""),
            },
            "crmSync": {
                "target": "google_sql",
                "status": "pending",
            },
            "sourceRecords": [
                {
                    "source": r.get("source", ""),
                    "rowIndex": r.get("rowIndex", 0),
                    "number": r.get("number", ""),
                    "file": r.get("file", ""),
                    "customer": r.get("customer", ""),
                    "phone": r.get("phone", ""),
                    "rawAddress": r.get("rawAddress", ""),
                    "instructions": r.get("instructions", ""),
                    "deposit": r.get("deposit", ""),
                    "total": r.get("total", ""),
                }
                for r in group[:50]
            ],
            "legacyNumbers": sorted({r.get("number", "") for r in group if r.get("number")}),
            "legacyFiles": sorted({r.get("file", "") for r in group if r.get("file")}),
            "createdAt": now,
            "updatedAt": now,
        }
        clients.append(client)
    return clients


def utc_now() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def firestore_value(value: Any) -> dict[str, Any]:
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", value):
            return {"timestampValue": value}
        return {"stringValue": value}
    if isinstance(value, list):
        return {"arrayValue": {"values": [firestore_value(item) for item in value]}}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {str(k): firestore_value(v) for k, v in value.items()}}}
    return {"stringValue": str(value)}


def firestore_fields(data: dict[str, Any]) -> dict[str, Any]:
    return {str(key): firestore_value(value) for key, value in data.items()}


def gcloud_access_token(project: str) -> str:
    gcloud = shutil.which("gcloud") or shutil.which("gcloud.cmd") or "gcloud.cmd"
    result = subprocess.run(
        [gcloud, "auth", "print-access-token", "--project", project],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def firestore_commit(project: str, writes: list[dict[str, Any]], token: str) -> None:
    if not writes:
        return
    url = f"https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents:commit"
    headers = {"Authorization": f"Bearer {token}"}
    post_json(url, {"writes": writes}, headers, timeout=60)


def make_update_write(project: str, collection: str, doc_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "update": {
            "name": f"projects/{project}/databases/(default)/documents/{collection}/{doc_id}",
            "fields": firestore_fields(data),
        }
    }


def write_firestore(project: str, job_id: str, records: list[dict[str, Any]], clients: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    token = gcloud_access_token(project)
    now = utc_now()
    job = {
        "status": "completed",
        "aiProvider": "table_import",
        "aiModel": "pandas+google_places_text_search",
        "totalFiles": len(DEFAULT_TABLES),
        "processedFiles": len(DEFAULT_TABLES),
        "failedFiles": 0,
        "clientsCreated": len(clients),
        "sourceRoot": str(DEFAULT_CLIENTS_ROOT),
        "sourceTables": sorted(
            [
                *DEFAULT_TABLES,
                *(["GoogleSheet/Customers_gid0.tsv"] if DEFAULT_GOOGLE_SHEET_TSV.exists() else []),
            ]
        ),
        "summary": summary,
        "createdAt": now,
        "updatedAt": now,
        "startedAt": now,
        "completedAt": now,
    }
    all_writes: list[dict[str, Any]] = [make_update_write(project, "client_import_jobs", job_id, job)]
    for record in records:
        record_data = {k: v for k, v in record.items() if k not in {"raw"}}
        record_data["jobId"] = job_id
        record_data["createdAt"] = now
        all_writes.append(make_update_write(project, "client_import_records", f"{job_id}_{record['id']}", record_data))
    for client in clients:
        data = {k: v for k, v in client.items() if k != "id"}
        all_writes.append(make_update_write(project, "clients", client["id"], data))

    for index in range(0, len(all_writes), 450):
        chunk = all_writes[index : index + 450]
        firestore_commit(project, chunk, token)
        print(f"Committed Firestore writes {index + len(chunk)}/{len(all_writes)}", flush=True)


def write_reports(out_dir: Path, records: list[dict[str, Any]], clients: list[dict[str, Any]], validations: dict[str, dict[str, Any]], summary: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "address_validations.json").write_text(json.dumps(validations, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "clients.json").write_text(json.dumps(clients, ensure_ascii=False, indent=2), encoding="utf-8")

    with (out_dir / "clients.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [
            "id",
            "name",
            "phone",
            "address",
            "rawAddress",
            "addressVerified",
            "addressVerificationStatus",
            "legacyNumbers",
            "legacyFiles",
            "sourceRecordsCount",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for client in clients:
            writer.writerow(
                {
                    "id": client["id"],
                    "name": client["name"],
                    "phone": client["phone"],
                    "address": client["address"],
                    "rawAddress": client["rawAddress"],
                    "addressVerified": client["addressVerified"],
                    "addressVerificationStatus": client["addressVerificationStatus"],
                    "legacyNumbers": "; ".join(client.get("legacyNumbers", [])),
                    "legacyFiles": "; ".join(client.get("legacyFiles", [])),
                    "sourceRecordsCount": len(client.get("sourceRecords", [])),
                }
            )
    with (out_dir / "raw_records.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = [
            "id",
            "clientDocId",
            "source",
            "rowIndex",
            "number",
            "customer",
            "phone",
            "rawAddress",
            "file",
            "instructions",
            "deposit",
            "total",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in fields})


def summarize(records: list[dict[str, Any]], clients: list[dict[str, Any]], validations: dict[str, dict[str, Any]]) -> dict[str, Any]:
    source_counts = Counter(record["source"] for record in records)
    validation_counts = Counter(result.get("status", "missing") for result in validations.values())
    client_validation_counts = Counter(client.get("addressVerificationStatus", "missing") for client in clients)
    return {
        "sourceRecords": len(records),
        "clients": len(clients),
        "sourceCounts": dict(sorted(source_counts.items())),
        "recordsWithPhone": sum(1 for record in records if record.get("phoneDigits")),
        "recordsWithRawAddress": sum(1 for record in records if record.get("rawAddress")),
        "uniqueRawAddresses": len(validations),
        "addressValidationCounts": dict(sorted(validation_counts.items())),
        "clientAddressStatusCounts": dict(sorted(client_validation_counts.items())),
        "clientsWithVerifiedAddress": sum(1 for client in clients if client.get("addressVerified")),
        "clientsWithoutAddress": sum(1 for client in clients if not client.get("rawAddress")),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import legacy client tables into Firestore clients.")
    parser.add_argument("--clients-root", default=str(DEFAULT_CLIENTS_ROOT))
    parser.add_argument("--project", default=DEFAULT_PROJECT)
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--commit", action="store_true", help="Write to Firestore. Default is dry-run report only.")
    parser.add_argument("--skip-google", action="store_true", help="Skip Google Places address validation.")
    parser.add_argument("--places-api-key", default="")
    parser.add_argument("--places-api-key-file", default=r"C:\Projects\crown-tablet\local.properties")
    parser.add_argument("--validation-cache", default=str(DEFAULT_VALIDATION_CACHE))
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--rate-delay", type=float, default=0.25)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.clients_root)
    missing = [str(root / table) for table in DEFAULT_TABLES if not (root / table).exists()]
    if missing:
        raise SystemExit("Missing source tables: " + ", ".join(missing))

    job_id = "manual_clients_import_" + dt.datetime.now(dt.UTC).strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir) if args.out_dir else Path("imports") / job_id

    records = load_records(root)
    places_key = load_places_key(args)
    validations = validate_addresses(
        records,
        places_key,
        workers=args.workers,
        cache_path=Path(args.validation_cache),
        rate_delay_s=max(0.0, args.rate_delay),
    )
    clients = build_clients(records, validations, job_id)
    summary = summarize(records, clients, validations)
    write_reports(out_dir, records, clients, validations, summary)

    print(json.dumps({"jobId": job_id, "outDir": str(out_dir), **summary}, ensure_ascii=False, indent=2))
    if args.commit:
        write_firestore(args.project, job_id, records, clients, summary)
    else:
        print("Dry run only. Re-run with --commit to write Firestore.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
