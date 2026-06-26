"""
Download Massive.com daily options candlestick flat files (last 5 years)
and concatenate into a single CSV.

WARNING: Options data is very large — potentially 50-100+ GB uncompressed
for 5 years. Consider narrowing START_DATE or filtering by underlying ticker.

Output:
    ~/dev/hades/stockdata/options_daily.csv
"""

import gzip
import os
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import boto3
from botocore.client import Config
from dotenv import load_dotenv
from tqdm import tqdm

load_dotenv(Path(__file__).parent.parent.parent / ".env")

ACCESS_KEY = os.environ["MASSIVE_ACCESS_KEY"]
SECRET_KEY = os.environ["MASSIVE_SECRET_KEY"]

# ── Config ────────────────────────────────────────────────────────────────────

ENDPOINT = "https://files.massive.com"
BUCKET   = "flatfiles"
PREFIX   = "us_options_opra/day_aggs_v1/"

END_DATE   = date.today() - timedelta(days=1)
START_DATE = END_DATE.replace(year=END_DATE.year - 1)

OUTPUT_DIR  = Path(__file__).parent.parent
OUTPUT_FILE = OUTPUT_DIR / "options_daily.csv"

WORKERS = 50

# ── S3 client (thread-local so each thread gets its own connection) ───────────

_thread_local = threading.local()

def get_s3():
    if not hasattr(_thread_local, "client"):
        _thread_local.client = boto3.client(
            "s3",
            endpoint_url=ENDPOINT,
            aws_access_key_id=ACCESS_KEY,
            aws_secret_access_key=SECRET_KEY,
            config=Config(signature_version="s3v4"),
        )
    return _thread_local.client

# ── Helpers ───────────────────────────────────────────────────────────────────

def list_files_in_range(start: date, end: date) -> list[dict]:
    paginator = get_s3().get_paginator("list_objects_v2")
    files = []
    for page in paginator.paginate(Bucket=BUCKET, Prefix=PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            filename = key.split("/")[-1]
            date_str = filename.replace(".csv.gz", "").replace(".csv", "")
            try:
                file_date = date.fromisoformat(date_str)
            except ValueError:
                continue
            if start <= file_date <= end:
                files.append({"key": key, "date": file_date, "size": obj["Size"]})
    files.sort(key=lambda x: x["date"])
    return files


def download_file(file_info: dict, tmpdir: str) -> tuple[dict, Path | None]:
    key = file_info["key"]
    suffix = ".csv.gz" if key.endswith(".gz") else ".csv"
    dest_csv = Path(tmpdir) / f"{file_info['date'].isoformat()}.csv"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=tmpdir)
    try:
        get_s3().download_fileobj(BUCKET, key, tmp)
        tmp.close()
        if suffix == ".csv.gz":
            with gzip.open(tmp.name, "rb") as f_in, open(dest_csv, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
            os.unlink(tmp.name)
        else:
            shutil.move(tmp.name, dest_csv)
        return file_info, dest_csv
    except Exception as e:
        tmp.close()
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)
        return file_info, None


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Listing files from {START_DATE} to {END_DATE}...")
    files = list_files_in_range(START_DATE, END_DATE)

    if not files:
        print("No files found. Check your credentials and date range.")
        return

    total_mb = sum(f["size"] for f in files) / 1_000_000
    print(f"Found {len(files)} files ({total_mb:.0f} MB compressed). Downloading with {WORKERS} threads...")
    print("Note: uncompressed size will be significantly larger.")

    results: dict[str, Path | None] = {}

    with tempfile.TemporaryDirectory() as tmpdir:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {pool.submit(download_file, f, tmpdir): f for f in files}
            with tqdm(total=len(files), unit="day") as bar:
                for future in as_completed(futures):
                    file_info, csv_path = future.result()
                    results[file_info["date"].isoformat()] = csv_path
                    bar.update(1)

        header_written = False
        with open(OUTPUT_FILE, "w") as out_f:
            for file_info in files:
                csv_path = results.get(file_info["date"].isoformat())
                if csv_path is None or not csv_path.exists():
                    print(f"  WARNING: skipping {file_info['key']} (download failed)")
                    continue
                date_str = file_info["date"].isoformat()
                with open(csv_path, "r") as in_f:
                    header = in_f.readline()
                    if not header_written:
                        out_f.write("date," + header)
                        header_written = True
                    for line in in_f:
                        out_f.write(date_str + "," + line)

    print(f"\nDone! Output: {OUTPUT_FILE}")
    print(f"Size: {OUTPUT_FILE.stat().st_size / 1_000_000:.1f} MB")


if __name__ == "__main__":
    main()
