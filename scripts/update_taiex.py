import csv
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
CSV_PATH = DATA_DIR / "taiex.csv"
META_PATH = DATA_DIR / "taiex_metadata.json"
FIELDNAMES = ["date", "open", "high", "low", "close", "adj_close", "volume"]
FULL_START_DATE = "1990-01-01"
REFRESH_LOOKBACK_DAYS = 10


def fetch_yahoo_chart(symbol, start_date="1990-01-01"):
    start_ts = int(datetime.fromisoformat(start_date).replace(tzinfo=timezone.utc).timestamp())
    end_ts = int(time.time())
    encoded_symbol = urllib.parse.quote(symbol, safe="")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded_symbol}"
        f"?period1={start_ts}&period2={end_ts}&interval=1d"
        "&events=history&includeAdjustedClose=true"
    )
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 taiex-signal-checker/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    error = payload.get("chart", {}).get("error")
    if error:
        raise RuntimeError(error)
    result = payload["chart"]["result"][0]
    timestamps = result["timestamp"]
    quote = result["indicators"]["quote"][0]
    adjclose = result["indicators"].get("adjclose", [{}])[0].get("adjclose", [])

    rows = []
    for i, timestamp in enumerate(timestamps):
        close = quote["close"][i]
        if close is None:
            continue
        trade_date = datetime.fromtimestamp(timestamp, timezone.utc).astimezone().date().isoformat()
        rows.append(
            {
                "date": trade_date,
                "open": quote["open"][i],
                "high": quote["high"][i],
                "low": quote["low"][i],
                "close": close,
                "adj_close": adjclose[i] if i < len(adjclose) else close,
                "volume": quote["volume"][i],
            }
        )
    return rows


def read_existing_rows():
    if not CSV_PATH.exists():
        return []
    with CSV_PATH.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def canonical_row(row):
    return {field: "" if row.get(field) is None else str(row.get(field)) for field in FIELDNAMES}


def incremental_start_date(existing_rows):
    if not existing_rows:
        return FULL_START_DATE
    latest_date = max(datetime.fromisoformat(row["date"]).date() for row in existing_rows)
    return (latest_date - timedelta(days=REFRESH_LOOKBACK_DAYS)).isoformat()


def merge_rows(existing_rows, fetched_rows):
    rows_by_date = {row["date"]: row for row in existing_rows}
    old_dates = set(rows_by_date)
    revised_dates = set()

    for row in fetched_rows:
        old_row = rows_by_date.get(row["date"])
        if old_row is not None and canonical_row(old_row) != canonical_row(row):
            revised_dates.add(row["date"])
        rows_by_date[row["date"]] = row

    rows = [rows_by_date[date] for date in sorted(rows_by_date)]
    added_dates = set(rows_by_date) - old_dates
    return rows, added_dates, revised_dates


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing_rows = read_existing_rows()
    start_date = incremental_start_date(existing_rows)
    fetched_rows = fetch_yahoo_chart("^TWII", start_date=start_date)
    if not fetched_rows:
        raise RuntimeError("No TAIEX rows returned")

    rows, added_dates, revised_dates = merge_rows(existing_rows, fetched_rows)
    has_file_changes = bool(added_dates or revised_dates) or not existing_rows or not META_PATH.exists()
    if not has_file_changes:
        status = {
            "symbol": "^TWII",
            "update_mode": "incremental",
            "fetched_start_date": start_date,
            "existing_rows": len(existing_rows),
            "fetched_rows": len(fetched_rows),
            "added_rows": 0,
            "revised_rows": 0,
            "rows": len(rows),
            "first_date": rows[0]["date"],
            "latest_date": rows[-1]["date"],
            "changed": False,
            "note": "No new or revised historical rows. Files left unchanged.",
        }
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return

    with CSV_PATH.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    metadata = {
        "symbol": "^TWII",
        "source": "Yahoo Finance chart API",
        "update_mode": "incremental",
        "fetched_start_date": start_date,
        "existing_rows": len(existing_rows),
        "fetched_rows": len(fetched_rows),
        "added_rows": len(added_dates),
        "revised_rows": len(revised_dates),
        "changed": True,
        "rows": len(rows),
        "first_date": rows[0]["date"],
        "latest_date": rows[-1]["date"],
        "updated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": (
            "Historical data only. Intraday/live index value is not included. "
            "Updates fetch only the recent missing/revisable range and merge by date."
        ),
    }
    META_PATH.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
