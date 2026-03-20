"""
WerProfitiert — Wer profitiert von österreichischen Gesetzen?
=============================================================
Konfiguration über drei Textdateien:

  gruppen.txt   — eine Gruppe pro Zeile, z.B.:
                    Bauern
                    Angestellte
                    Immobilien Besitzer

  Zeitraum.txt  — Start- und Endjahr, z.B.:
                    1950 - 1970

  Bericht.txt   — wird vom Skript laufend befüllt (Arbeitsgedächtnis).
                  Jede Zeile = ein BGBl-Gesetz mit Prozentwerten pro Gruppe.
                  Bereits analysierte Gesetze werden übersprungen (Resume).

Verwendung:
    pip install -r requirements.txt
    python werprofitiert.py
"""

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import anthropic
from tqdm import tqdm

from download_bgbl import download_year, jahr_vollstaendig, pdf_dir, pdf_path

# ── Konstanten ─────────────────────────────────────────────────────────────────

MODEL            = "claude-sonnet-4-5"
MAX_TEXT_CHARS   = 6000
RATE_LIMIT_DELAY = 0.5


CONFIG_GRUPPEN  = Path("gruppen.txt")
CONFIG_ZEITRAUM = Path("Zeitraum.txt")
BERICHT         = Path("Bericht.txt")
SCRATCHPAD      = Path("temp.txt")
KONVERTER       = Path("pdf_to_text.py")

# ── Konfiguration lesen ────────────────────────────────────────────────────────

def lies_gruppen() -> list[str]:
    if not CONFIG_GRUPPEN.exists():
        sys.exit(
            f"Fehler: '{CONFIG_GRUPPEN}' nicht gefunden.\n"
            "Bitte Datei anlegen, eine Gruppe pro Zeile, z.B.:\n"
            "  Bauern\n  Angestellte\n  Immobilien Besitzer"
        )
    gruppen = [
        line.strip()
        for line in CONFIG_GRUPPEN.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not gruppen:
        sys.exit(f"Fehler: '{CONFIG_GRUPPEN}' ist leer.")
    return gruppen


def lies_zeitraum() -> tuple[int, int]:
    if not CONFIG_ZEITRAUM.exists():
        sys.exit(
            f"Fehler: '{CONFIG_ZEITRAUM}' nicht gefunden.\n"
            "Bitte Datei anlegen mit Inhalt wie: 1950 - 1970"
        )
    inhalt = CONFIG_ZEITRAUM.read_text(encoding="utf-8").strip()
    zahlen = re.findall(r"\d{4}", inhalt)
    if len(zahlen) < 2:
        sys.exit(
            f"Fehler: '{CONFIG_ZEITRAUM}' konnte nicht gelesen werden.\n"
            f"Inhalt: '{inhalt}'\nErwartet z.B.: 1950 - 1970"
        )
    return int(zahlen[0]), int(zahlen[1])


def lies_bereits_analysiert() -> set[str]:
    """Liest Bericht.txt und gibt BGBl-Nummern zurück die schon fertig sind — für Resume."""
    if not BERICHT.exists():
        return set()
    analysiert = set()
    for line in BERICHT.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(BGBl\.[^\|]+)\|", line)
        if m:
            analysiert.add(m.group(1).strip())
    return analysiert


# ── PDF-Verarbeitung ───────────────────────────────────────────────────────────

MONATE = {
    "januar": "01", "februar": "02", "märz": "03", "april": "04",
    "mai": "05", "juni": "06", "juli": "07", "august": "08",
    "september": "09", "oktober": "10", "november": "11", "dezember": "12",
}

RECHTSTITEL = re.compile(
    r"(Bundes)?gesetz|Verordnung|Erlass|Kundmachung|Abkommen|Vertrag|Übereinkommen",
    re.IGNORECASE,
)


def metadaten_aus_pdf(text: str) -> tuple[str, str]:
    """
    Versucht Titel und Datum aus dem extrahierten PDF-Text zu lesen.
    Gibt (titel, datum) zurück — leer wenn nicht gefunden.
    """
    kopf = text[:2000]

    # Datum: "17. August 1955" oder "17.8.1955" oder "17. 8. 1955"
    datum = ""
    m = re.search(
        r"(\d{1,2})\.\s*(" + "|".join(MONATE) + r")\s+(\d{4})",
        kopf, re.IGNORECASE,
    )
    if m:
        tag  = m.group(1).zfill(2)
        mon  = MONATE[m.group(2).lower()]
        jahr = m.group(3)
        datum = f"{jahr}-{mon}-{tag}"
    else:
        m = re.search(r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})", kopf)
        if m:
            datum = f"{m.group(3)}-{m.group(2).zfill(2)}-{m.group(1).zfill(2)}"

    # Titel: erste Zeile die einen Rechtstitel-Begriff enthält
    titel = ""
    for line in kopf.splitlines():
        line = line.strip()
        if len(line) > 15 and RECHTSTITEL.search(line):
            titel = line
            break

    return titel, datum


def txt_pfad(pdf: Path) -> Path:
    return pdf.with_suffix(".txt")


# ── Claude-generierter PDF-Konverter ──────────────────────────────────────────

KONVERTER_PROMPT = """\
Schreibe ein Python-Skript das:
1. Einen PDF-Dateipfad als erstes Kommandozeilenargument entgegennimmt
2. Den gesamten Text des PDFs nach stdout ausgibt (UTF-8)
3. Robust mit verschiedenen PDF-Formaten umgeht (gescannte PDFs, digitale PDFs)
4. Nur pdfplumber verwendet (ist bereits installiert)
5. Bei Fehler eine kurze Fehlermeldung nach stderr schreibt und mit Exit-Code 1 endet

Gib NUR den reinen Python-Code aus — kein Markdown, keine Erklärungen.\
"""

def generiere_konverter(client: anthropic.Anthropic) -> None:
    """Lässt Claude pdf_to_text.py schreiben; testet es und korrigiert Fehler (max 10 Versuche)."""
    if KONVERTER.exists():
        return

    print("  Generiere PDF-Konverter via Claude…")
    test_pdf = next(Path(".").glob("bgbl_*/*.pdf"), None)
    prompt   = KONVERTER_PROMPT
    code     = ""

    for versuch in range(1, 11):
        resp = client.messages.create(
            model      = MODEL,
            max_tokens = 2000,
            messages   = [{"role": "user", "content": prompt}],
        )
        code = resp.content[0].text.strip()
        # Strip markdown fences if Claude added them anyway
        if code.startswith("```"):
            code = re.sub(r"^```[^\n]*\n?", "", code)
            code = re.sub(r"\n?```\s*$", "", code)

        KONVERTER.write_text(code, encoding="utf-8")

        if test_pdf is None:
            print(f"  Konverter gespeichert (kein Test-PDF vorhanden)")
            return

        result = subprocess.run(
            [sys.executable, str(KONVERTER), str(test_pdf)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            print(f"  Konverter bereit (Versuch {versuch})")
            return

        fehler = (result.stderr or "Kein Output erzeugt").strip()[:500]
        print(f"  Versuch {versuch} fehlgeschlagen: {fehler}")
        prompt = (
            f"Das Skript hat folgenden Fehler produziert:\n{fehler}\n\n"
            f"Hier ist der fehlerhafte Code:\n{code}\n\n"
            "Schreibe das Skript neu und behebe den Fehler. "
            "Gib NUR den reinen Python-Code aus."
        )

    print("  WARNUNG: Konverter konnte nicht generiert werden — nutze pdfplumber als Fallback")
    KONVERTER.write_text(
        "import sys, io, pdfplumber\n"
        "with pdfplumber.open(sys.argv[1]) as pdf:\n"
        "    print(' '.join(p.extract_text() or '' for p in pdf.pages))\n",
        encoding="utf-8",
    )


def revise_konverter(client: anthropic.Anthropic, fehler: str) -> bool:
    """Lässt Claude den Konverter anhand des Fehlers überarbeiten. Gibt True zurück wenn erfolgreich."""
    if not KONVERTER.exists():
        return False
    test_pdf = next(Path(".").glob("bgbl_*/*.pdf"), None)
    if not test_pdf:
        return False

    code   = KONVERTER.read_text(encoding="utf-8")
    prompt = (
        f"Das PDF-Konverter-Skript hat folgenden Fehler produziert:\n{fehler}\n\n"
        f"Hier ist der aktuelle Code:\n{code}\n\n"
        "Schreibe das Skript neu und behebe den Fehler. "
        "Gib NUR den reinen Python-Code aus."
    )

    for versuch in range(1, 6):
        resp = client.messages.create(
            model      = MODEL,
            max_tokens = 2000,
            messages   = [{"role": "user", "content": prompt}],
        )
        neuer_code = resp.content[0].text.strip()
        if neuer_code.startswith("```"):
            neuer_code = re.sub(r"^```[^\n]*\n?", "", neuer_code)
            neuer_code = re.sub(r"\n?```\s*$",    "", neuer_code)

        KONVERTER.write_text(neuer_code, encoding="utf-8")

        result = subprocess.run(
            [sys.executable, str(KONVERTER), str(test_pdf)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            print(f"    Konverter korrigiert (Versuch {versuch})")
            return True

        fehler = (result.stderr or "Kein Output").strip()[:500]
        prompt = (
            f"Immer noch fehlerhaft:\n{fehler}\n\nCode:\n{neuer_code}\n\n"
            "Behebe den Fehler. Gib NUR den reinen Python-Code aus."
        )

    return False


def stelle_text_sicher(datensatz: dict, client: anthropic.Anthropic) -> str | None:
    """
    Stellt sicher dass ein .txt für diesen Datensatz vorhanden ist:
      1. .txt vorhanden → direkt zurückgeben
      2. PDF vorhanden → konvertieren (mit Konverter-Revision bei Fehler)
      3. PDF fehlt     → einzeln herunterladen, dann konvertieren
    Gibt den Text zurück oder None wenn nicht möglich.
    """
    pdf = datensatz["path"]
    txt = txt_pfad(pdf)

    if txt.exists():
        inhalt = txt.read_text(encoding="utf-8")
        if not inhalt.startswith("[Fehler"):
            return inhalt

    # PDF herunterladen falls fehlend
    if not pdf.exists():
        bgbl_nr = datensatz["bgbl_nr"]
        m = re.search(r"(\d{4})/(\d+)(?:\s+Teil\s+(\S+))?", bgbl_nr)
        if not m:
            return None
        year, nr, teil = int(m.group(1)), int(m.group(2)), m.group(3) or ""
        print(f"    PDF fehlt, lade herunter: {bgbl_nr}")
        try:
            import requests
            from download_bgbl import BASE_URL_AUTH, BASE_URL_PDF, BGBL_AUTH_START
            session = requests.Session()
            url = (
                BASE_URL_AUTH.format(year=year, teil=teil or "I", nr=nr)
                if year >= BGBL_AUTH_START
                else BASE_URL_PDF.format(year=year, nr=nr)
            )
            resp = session.get(url, timeout=30)
            if resp.status_code == 200 and "pdf" in resp.headers.get("Content-Type", "").lower():
                pdf.parent.mkdir(exist_ok=True)
                pdf.write_bytes(resp.content)
            else:
                print(f"    Download fehlgeschlagen (HTTP {resp.status_code})")
                return None
        except Exception as e:
            print(f"    Download-Fehler: {e}")
            return None

    # PDF → .txt konvertieren (mit Revision bei Fehler)
    for versuch in range(1, 4):
        result = subprocess.run(
            [sys.executable, str(KONVERTER), str(pdf)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            txt.write_text(result.stdout, encoding="utf-8")
            return result.stdout

        fehler = (result.stderr or "Kein Output").strip()
        print(f"    Konvertierung fehlgeschlagen (Versuch {versuch}): {fehler[:120]}")
        if not revise_konverter(client, fehler):
            break

    txt.write_text("[Fehler bei PDF-Extraktion]", encoding="utf-8")
    return None


def sammle_datensaetze(von_jahr: int, bis_jahr: int) -> list[dict]:
    """Erstellt Datensätze aus lokalen PDF-Ordnern (bgbl_{year}/)."""
    datensaetze = []
    for jahr in range(von_jahr, bis_jahr + 1):
        d = pdf_dir(jahr)
        if not d.is_dir():
            print(f"  {jahr}: kein Ordner bgbl_{jahr}/ gefunden")
            continue
        pdfs = sorted(d.glob("*.pdf"))
        for pdf in pdfs:
            m = re.search(r"bgbl_(\d{4})_(\d+)(?:_(I{1,3}|IV|V?I{0,3}|II?I?))?\.pdf", pdf.name)
            if m:
                nr   = int(m.group(2))
                teil = m.group(3) or ""
                bgbl_nr = f"BGBl. {jahr}/{nr} Teil {teil}" if teil else f"BGBl. {jahr}/{nr}"
                datensaetze.append({
                    "jahr":    jahr,
                    "bgbl_nr": bgbl_nr,
                    "path":    pdf,
                })
        print(f"  {jahr}: {len(pdfs)} PDFs gefunden")
    return datensaetze


# ── LLM-Analyse ───────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Du bist ein Experte für österreichisches Recht und Sozialpolitik.
Du analysierst Gesetzestexte aus dem Bundesgesetzblatt und bewertest,
wie stark verschiedene gesellschaftliche Gruppen von jedem Gesetz profitieren.
Antworte ausschließlich mit einem gültigen JSON-Objekt, ohne Präambel oder Markdown."""


def erstelle_prompt(bgbl_nr: str, titel: str, datum: str, text: str, gruppen: list[str]) -> str:
    gekuerzt     = text[:MAX_TEXT_CHARS] + ("…[gekürzt]" if len(text) > MAX_TEXT_CHARS else "")
    gruppen_json = json.dumps(gruppen, ensure_ascii=False)

    return f"""Analysiere folgenden österreichischen Gesetzestext:

BGBl-Nummer : {bgbl_nr}
Titel (Hint): {titel or "unbekannt — bitte aus dem Text ermitteln"}
Datum (Hint): {datum or "unbekannt — bitte aus dem Text ermitteln"}

Gesetzestext (ggf. gekürzt):
{gekuerzt}

Bewerte für jede der folgenden Gruppen, wie stark sie von diesem Gesetz profitiert.
Gruppen: {gruppen_json}

Antworte mit folgendem JSON:
{{
  "titel": "Offizieller Titel des Gesetzes laut Text",
  "datum": "YYYY-MM-DD oder leer wenn nicht erkennbar",
  "kurzbeschreibung": "1-2 Sätze: Was regelt dieses Gesetz?",
  "gruppen": {{
    "<Gruppenname>": {{
      "prozent": <0-100>,
      "erklaerung": "1 kurzer Satz warum"
    }}
  }}
}}

Regeln für 'prozent':
- 0     = diese Gruppe wird durch das Gesetz nicht berührt
- 1–30  = geringer indirekter Nutzen
- 31–60 = moderater, spürbarer Nutzen
- 61–100 = direkter, starker Nutzen
Die Werte summieren sich NICHT auf 100 — jede Gruppe wird unabhängig bewertet.
Sei differenziert: Die meisten Gesetze betreffen nur 1-3 Gruppen wirklich stark."""


def analysiere_gesetz(client: anthropic.Anthropic, datensatz: dict, gruppen: list[str]) -> dict | None:
    bgbl_nr = datensatz["bgbl_nr"]

    text = stelle_text_sicher(datensatz, client)
    if not text or text.startswith("[Fehler"):
        print(f"  [Fehler] Kein Text verfügbar für {bgbl_nr}")
        return None

    titel, datum = metadaten_aus_pdf(text)

    prompt = erstelle_prompt(
        bgbl_nr = bgbl_nr,
        titel   = titel,
        datum   = datum,
        text    = text,
        gruppen = gruppen,
    )

    try:
        resp = client.messages.create(
            model      = MODEL,
            max_tokens = 4096,
            system     = SYSTEM_PROMPT,
            messages   = [{"role": "user", "content": prompt}],
        )
        roh = resp.content[0].text.strip()
        if roh.startswith("```"):
            roh = roh.split("```")[1]
            if roh.startswith("json"):
                roh = roh[4:]
        return json.loads(roh)
    except Exception as e:
        print(f"  [LLM-Fehler] {bgbl_nr}: {e}")
        return None


# ── Bericht.txt formatieren ───────────────────────────────────────────────────

def formatiere_zeile(datensatz: dict, analyse: dict, gruppen: list[str]) -> str:
    bgbl   = datensatz["bgbl_nr"]
    datum  = analyse.get("datum", "")
    titel  = analyse.get("titel", "")
    beschr = analyse.get("kurzbeschreibung", "")
    ga     = analyse.get("gruppen", {})

    max_len = max((len(g) for g in gruppen), default=10)

    zeilen = [
        f"{bgbl} | {datum} | {titel}",
        f"  Kurzbeschreibung: {beschr}",
    ]
    for gruppe in gruppen:
        info    = ga.get(gruppe, {})
        prozent = info.get("prozent", 0)
        erkl    = info.get("erklaerung", "—")
        pad     = " " * (max_len - len(gruppe))
        zeilen.append(f"  {gruppe}{pad} : {prozent:3d}% — {erkl}")

    zeilen.append("")
    return "\n".join(zeilen)


def initialisiere_bericht(gruppen: list[str], von_jahr: int, bis_jahr: int) -> None:
    header = "\n".join([
        "=" * 72,
        "WerProfitiert — Analysebericht",
        f"Zeitraum : {von_jahr} – {bis_jahr}",
        f"Gruppen  : {', '.join(gruppen)}",
        f"Erstellt : {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Modell   : {MODEL}",
        "=" * 72,
        "",
        "Legende: Prozentwerte sind unabhängig pro Gruppe (summieren nicht auf 100).",
        "         0% = nicht betroffen, 100% = maximaler direkter Nutzen.",
        "",
    ])
    BERICHT.write_text(header, encoding="utf-8")


# ── Scratchpad ────────────────────────────────────────────────────────────────

def aktualisiere_scratchpad(von_jahr: int, bis_jahr: int, gesamt: int,
                             analysiert: int, fehler: int, aktuell: str) -> None:
    verbleibend = gesamt - analysiert
    fortschritt = f"{analysiert}/{gesamt}" if gesamt else "0/0"
    inhalt = "\n".join([
        "WerProfitiert — Status",
        f"Stand      : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Zeitraum   : {von_jahr} – {bis_jahr}",
        f"Gesamt     : {gesamt}",
        f"Analysiert : {analysiert}  ({fortschritt})",
        f"Fehler     : {fehler}",
        f"Verbleibend: {verbleibend}",
        f"Aktuell    : {aktuell or '—'}",
        "",
    ])
    SCRATCHPAD.write_text(inhalt, encoding="utf-8")


# ── Hauptprogramm ─────────────────────────────────────────────────────────────

def main():
    gruppen            = lies_gruppen()
    von_jahr, bis_jahr = lies_zeitraum()
    bereits            = lies_bereits_analysiert()

    print(f"\nWerProfitiert")
    print(f"  Zeitraum : {von_jahr} – {bis_jahr}")
    print(f"  Gruppen  : {', '.join(gruppen)}")
    print(f"  Bericht  : {BERICHT}")
    if bereits:
        print(f"  Resume   : {len(bereits)} Gesetze bereits analysiert, werden übersprungen")
    print()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        key_file = Path.home() / ".anthropic_key"
        if key_file.exists():
            api_key = key_file.read_text().strip()
    if not api_key:
        sys.exit(
            "Fehler: API-Key nicht gefunden.\n"
            "Entweder ANTHROPIC_API_KEY setzen oder Key in ~/.anthropic_key speichern."
        )
    client = anthropic.Anthropic(api_key=api_key)

    print("Schritt 0: PDF-Konverter vorbereiten…")
    generiere_konverter(client)
    print()

    print("Schritt 1: PDFs herunterladen (falls noch nicht vorhanden)…")
    for jahr in range(von_jahr, bis_jahr + 1):
        if jahr_vollstaendig(jahr):
            print(f"  {jahr}: bgbl_{jahr}/ bereits vorhanden, übersprungen")
        else:
            download_year(jahr)
    print()

    print("Schritt 2: Datensätze einlesen…")
    alle = sammle_datensaetze(von_jahr, bis_jahr)
    gesamt = len(alle)
    print(f"  → {gesamt} BGBl-Einträge gefunden\n")

    if not BERICHT.exists():
        initialisiere_bericht(gruppen, von_jahr, bis_jahr)

    zu_tun = [g for g in alle if g["bgbl_nr"].strip() not in bereits]
    print(f"Schritt 3: {len(zu_tun)} Gesetze zu analysieren ({gesamt - len(zu_tun)} bereits erledigt)…\n")

    fehler        = 0
    analysiert    = gesamt - len(zu_tun)  # already done from previous runs

    for datensatz in tqdm(zu_tun, unit="Gesetz"):
        bgbl_nr = datensatz["bgbl_nr"].strip()

        aktualisiere_scratchpad(von_jahr, bis_jahr, gesamt, analysiert, fehler, bgbl_nr)

        analyse = analysiere_gesetz(client, datensatz, gruppen)

        if analyse is None:
            fehler += 1
            platzhalter = f"{bgbl_nr} | | \n  [Analysefehler]\n\n"
            with BERICHT.open("a", encoding="utf-8") as f:
                f.write(platzhalter)
        else:
            zeile = formatiere_zeile(datensatz, analyse, gruppen)
            with BERICHT.open("a", encoding="utf-8") as f:
                f.write(zeile + "\n")

        bereits.add(bgbl_nr)
        analysiert += 1
        time.sleep(RATE_LIMIT_DELAY)

    aktualisiere_scratchpad(von_jahr, bis_jahr, gesamt, analysiert, fehler, "Fertig")
    print(f"\nFertig!")
    print(f"  Analysiert : {len(zu_tun) - fehler}")
    print(f"  Fehler     : {fehler}")
    print(f"  Bericht    : {BERICHT.resolve()}")
    print(f"  Status     : {SCRATCHPAD.resolve()}")


if __name__ == "__main__":
    main()
