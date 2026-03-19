"""
Download all Austrian Bundesgesetzblatt (BGBl.) PDFs for a given year.

Source: RIS (Rechtsinformationssystem des Bundes)
URL pattern: https://www.ris.bka.gv.at/Dokumente/BgblPdf/{YEAR}_{NR}_0/{YEAR}_{NR}_0.pdf
Note: NR has NO zero-padding (e.g. 1950_15_0, not 1950_015_0)

Usage:
    pip install requests
    python download_bgbl.py 1955
    python download_bgbl.py 1955 --max-nr 400
"""

import argparse
import time
import requests
from pathlib import Path

MAX_NR_DEFAULT = 280
DELAY          = 1.0  # seconds between requests — be polite to RIS
BASE_URL       = "https://www.ris.bka.gv.at/Dokumente/BgblPdf/{year}_{nr}_0/{year}_{nr}_0.pdf"


def pdf_dir(year: int) -> Path:
    return Path(f"bgbl_{year}")


def pdf_path(year: int, nr: int) -> Path:
    return pdf_dir(year) / f"bgbl_{year}_{nr:03d}.pdf"


def jahr_vollstaendig(year: int, max_nr: int = MAX_NR_DEFAULT) -> bool:
    """Returns True if bgbl_{year}/ exists and contains at least one PDF."""
    d = pdf_dir(year)
    return d.is_dir() and any(d.glob("*.pdf"))


def download_year(year: int, max_nr: int = MAX_NR_DEFAULT, delay: float = DELAY) -> dict:
    """
    Downloads all BGBl. PDFs for the given year into bgbl_{year}/.
    Skips files that already exist. Returns a summary dict.
    """
    output_dir = pdf_dir(year)
    output_dir.mkdir(exist_ok=True)

    session = requests.Session()
    session.headers.update({
        "User-Agent": "BGBl-Research-Downloader/1.0 (academic use; contact: your@email.at)"
    })

    downloaded = skipped = not_found = errors = 0

    print(f"  BGBl. {year}: lade PDFs herunter → {output_dir}/")

    for nr in range(1, max_nr + 1):
        dest = pdf_path(year, nr)
        if dest.exists():
            skipped += 1
            continue

        url = BASE_URL.format(year=year, nr=nr)
        try:
            resp = session.get(url, timeout=30)
            if resp.status_code == 200 and "pdf" in resp.headers.get("Content-Type", "").lower():
                dest.write_bytes(resp.content)
                downloaded += 1
            elif resp.status_code == 404:
                not_found += 1
            else:
                print(f"    [{nr:03d}] HTTP {resp.status_code}")
                errors += 1
        except requests.RequestException as e:
            print(f"    [{nr:03d}] Fehler: {e}")
            errors += 1

        time.sleep(delay)

    print(f"  BGBl. {year}: {downloaded} neu, {skipped} übersprungen, "
          f"{not_found} nicht gefunden, {errors} Fehler")
    return {"year": year, "downloaded": downloaded, "skipped": skipped,
            "not_found": not_found, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description="Download BGBl. PDFs for a given year.")
    parser.add_argument("year", type=int, help="Year to download, e.g. 1955")
    parser.add_argument("--max-nr", type=int, default=MAX_NR_DEFAULT,
                        help=f"Highest issue number to scan (default: {MAX_NR_DEFAULT})")
    args = parser.parse_args()

    result = download_year(args.year, args.max_nr)
    print(f"\nFertig. Output: {pdf_dir(result['year']).resolve()}")


if __name__ == "__main__":
    main()
