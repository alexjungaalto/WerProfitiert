#!/usr/bin/env python3
import sys
import pdfplumber

def main():
    if len(sys.argv) < 2:
        sys.stderr.write("Fehler: Kein PDF-Dateipfad angegeben\n")
        sys.exit(1)
    
    pdf_path = sys.argv[1]
    
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    sys.stdout.write(text)
                    sys.stdout.write("\n")
    except FileNotFoundError:
        sys.stderr.write(f"Fehler: Datei nicht gefunden: {pdf_path}\n")
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"Fehler beim Verarbeiten der PDF: {str(e)}\n")
        sys.exit(1)

if __name__ == "__main__":
    main()