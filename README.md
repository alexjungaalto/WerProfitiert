# WerProfitiert

**Who benefits from Austrian law?**

An open-source LLM agent that downloads the Austrian *Bundesgesetzblatt* (Federal Law Gazette) as PDFs, extracts the law text, and uses Claude (Anthropic) to identify which groups of people benefited from each act.

> *"Wer profitiert?" — the question Austrian politics has always struggled to answer honestly.*

---

## How it works

1. **Downloads** PDFs for each year from the official RIS server (`ris.bka.gv.at`) using the known URL pattern
2. **Extracts** the full text from each PDF using `pdfplumber`
3. **Analyses** each act with Claude, which extracts the title, date, a plain-language summary, and a benefit score per group
4. **Writes** results to `Bericht.txt` — one entry per act, resumable if interrupted

---

## Quickstart

```bash
git clone https://github.com/yourusername/WerProfitiert.git
cd WerProfitiert
pip install -r requirements.txt
```

Store your Anthropic API key outside the repo:
```bash
echo "sk-ant-..." > ~/.anthropic_key
chmod 600 ~/.anthropic_key
```

**Schritt 1** — Gruppen in `gruppen.txt` eintragen (eine pro Zeile):
```
Bauern
Angestellte
Immobilien Besitzer
Mieter
Kirche
```

**Schritt 2** — Zeitraum in `Zeitraum.txt` eintragen:
```
1950 - 1970
```

**Schritt 3** — Skript starten:
```bash
python werprofitiert.py
```

Das Skript läuft in drei Schritten durch: PDFs herunterladen → PDFs einlesen → Analyse mit Claude. Es befüllt `Bericht.txt` laufend und kann jederzeit unterbrochen und neu gestartet werden (Resume).

**Beispielausgabe in `Bericht.txt`:**
```
BGBl. 1950/15 | 1950-04-05 | Mieterschutzgesetz
  Kurzbeschreibung: Regelt den Schutz von Mietern vor Kündigung und Mietzinserhöhung.
  Bauern              :   0% — Nicht berührt, betrifft städtische Mietverhältnisse.
  Angestellte         :  60% — Profitieren als häufigste Mietergruppe direkt.
  Immobilien Besitzer :   5% — Als Vermieter durch Einschränkungen leicht belastet.
  Mieter              :  90% — Direkter starker Nutzen durch Kündigungsschutz.
  Kirche              :   5% — Als Vermieter kirchlicher Wohnungen leicht betroffen.
```

---

## PDF download

PDFs can also be downloaded independently for any year:

```bash
python download_bgbl.py 1955
python download_bgbl.py 1955 --max-nr 400
```

Downloaded PDFs are cached in `bgbl_{year}/` and reused on subsequent runs.

---

## Using a different LLM

By default the script uses `claude-sonnet-4-5` via the Anthropic SDK. To use a different provider, swap the SDK for [LiteLLM](https://github.com/BerriAI/litellm), which provides a single interface for 100+ models:

```bash
pip install litellm
```

Then in `werprofitiert.py`, replace the two Anthropic-specific lines:

```python
# Remove:
import anthropic
client = anthropic.Anthropic(api_key=api_key)

# Replace each client.messages.create(...) call with:
import litellm
resp = litellm.completion(model=MODEL, max_tokens=1000,
    messages=[{"role": "system", "content": SYSTEM_PROMPT},
              {"role": "user",   "content": prompt}])
roh = resp.choices[0].message.content.strip()
```

Then set `MODEL` at the top of the script to your preferred provider:

| Provider | MODEL value | API key env var |
|---|---|---|
| Anthropic (default) | `claude-sonnet-4-5` | `ANTHROPIC_API_KEY` |
| Ollama (local) | `ollama/llama3.2` | — |
| OpenAI | `gpt-4o` | `OPENAI_API_KEY` |
| Google Gemini | `gemini/gemini-1.5-pro` | `GEMINI_API_KEY` |

For Ollama, make sure the server is running locally (`ollama serve`) and set:
```bash
export OLLAMA_API_BASE=http://localhost:11434
```

Note: smaller local models may produce less reliable JSON output. If parsing fails, try a larger model or add stricter instructions to the system prompt.

---

## Data source

All legislation is fetched from the **RIS — Rechtsinformationssystem des Bundes**, the official Austrian legal information system operated by the Federal Chancellery.

URL pattern: `https://www.ris.bka.gv.at/Dokumente/BgblPdf/{year}_{nr}_0/{year}_{nr}_0.pdf`

No login or API key is required to access RIS data.

---

## Limitations

- Benefit scores are LLM inferences from act text — they are approximations, not legal interpretations.
- Older acts (pre-1980) may have lower-quality text due to OCR from scanned PDFs.
- The taxonomy is user-defined and not exhaustive — adjust `gruppen.txt` to your research question.

---

## Research questions this enables

- Which decade produced the most redistributive legislation?
- Did labour protections peak and then erode? (Sozialpartnerschaft arc)
- When did women shift from "dependents" to explicit beneficiaries in the legal text?
- How did migration-related legislation change in tone post-1989?
- Did church-affiliated groups benefit disproportionately in early postwar legislation?

---

## Contributing

Pull requests welcome. Please open an issue first for substantial changes.

Areas where contributions are especially valuable:
- Improving prompt design and benefit scoring
- Visualisation of results (Plotly / D3)
- Extending to *Landesgesetzblätter* (provincial gazettes)

---

## License

MIT — see [LICENSE](LICENSE)

---

## Author

[Alex Jung](https://github.com/yourusername) — Professor of Machine Learning, Aalto University
Research interests: federated learning, algorithmic fairness, AI policy

*This project is unaffiliated with any political party or institution.*
