"""
Download all Austrian Bundesgesetzblatt (BGBl.) PDFs for a given year.

Source: RIS (Rechtsinformationssystem des Bundes)

Two URL patterns depending on year:
  Pre-2004 (scanned PDFs):
    https://www.ris.bka.gv.at/Dokumente/BgblPdf/{YEAR}_{NR}_0/{YEAR}_{NR}_0.pdf
  2004+ (authentic digital publication, Teil I / II / III):
    https://www.ris.bka.gv.at/Dokumente/BgblAuth/BGBLA_{YEAR}_I_{NR}/BGBLA_{YEAR}_I_{NR}.pdf
    https://www.ris.bka.gv.at/Dokumente/BgblAuth/BGBLA_{YEAR}_II_{NR}/BGBLA_{YEAR}_II_{NR}.pdf
    https://www.ris.bka.gv.at/Dokumente/BgblAuth/BGBLA_{YEAR}_III_{NR}/BGBLA_{YEAR}_III_{NR}.pdf

Usage:
    pip install requests
    python download_bgbl.py 1955
    python download_bgbl.py 2020
    python download_bgbl.py 1955 --max-nr 400
"""

import argparse
import time
import requests
from pathlib import Path

MAX_NR_DEFAULT  = 280
DELAY           = 1.0  # seconds between requests — be polite to RIS
BGBL_AUTH_START = 2004

BASE_URL_PDF  = "https://www.ris.bka.gv.at/Dokumente/BgblPdf/{year}_{nr}_0/{year}_{nr}_0.pdf"
BASE_URL_AUTH = "https://www.ris.bka.gv.at/Dokumente/BgblAuth/BGBLA_{year}_{teil}_{nr}/BGBLA_{year}_{teil}_{nr}.pdf"
TEILE         = ["I", "II", "III"]


def pdf_dir(year: int) -> Path:
    return Path(f"bgbl_{year}")


def pdf_path(year: int, nr: int, teil: str = "") -> Path:
    suffix = f"_{teil}" if teil else ""
    return pdf_dir(year) / f"bgbl_{year}_{nr:03d}{suffix}.pdf"


def jahr_vollstaendig(year: int) -> bool:
    """Returns True if bgbl_{year}/ exists and contains at least one PDF."""
    d = pdf_dir(year)
    return d.is_dir() and any(d.glob("*.pdf"))


def _fetch(session: requests.Session, url: str) -> bytes | None:
    """GET url; return content bytes on 200/PDF, None if 404, raise on other errors."""
    resp = session.get(url, timeout=30)
    if resp.status_code == 200 and "pdf" in resp.headers.get("Content-Type", "").lower():
        return resp.content
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return None


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
    use_auth = year >= BGBL_AUTH_START

    print(f"  BGBl. {year}: lade PDFs herunter → {output_dir}/  "
          f"({'BgblAuth (I/II/III)' if use_auth else 'BgblPdf'})")

    for nr in range(1, max_nr + 1):
        if use_auth:
            # Try all three Teile; save each that exists
            found_any = False
            for teil in TEILE:
                dest = pdf_path(year, nr, teil)
                if dest.exists():
                    skipped += 1
                    found_any = True
                    continue
                url = BASE_URL_AUTH.format(year=year, teil=teil, nr=nr)
                try:
                    data = _fetch(session, url)
                    if data is not None:
                        dest.write_bytes(data)
                        downloaded += 1
                        found_any = True
                    time.sleep(delay)
                except requests.RequestException as e:
                    print(f"    [{nr:03d}/{teil}] Fehler: {e}")
                    errors += 1
            if not found_any:
                not_found += 1
        else:
            dest = pdf_path(year, nr)
            if dest.exists():
                skipped += 1
                continue
            url = BASE_URL_PDF.format(year=year, nr=nr)
            try:
                data = _fetch(session, url)
                if data is not None:
                    dest.write_bytes(data)
                    downloaded += 1
                else:
                    not_found += 1
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
    parser.add_argument("year", type=int, help="Year to download, e.g. 1955 or 2020")
    parser.add_argument("--max-nr", type=int, default=MAX_NR_DEFAULT,
                        help=f"Highest issue number to scan (default: {MAX_NR_DEFAULT})")
    args = parser.parse_args()

    result = download_year(args.year, args.max_nr)
    print(f"\nFertig. Output: {pdf_dir(result['year']).resolve()}")


if __name__ == "__main__":
    main()
