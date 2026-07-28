# Jökulsárlón Pinger

Monitora la disponibilità del **18 agosto 2026** per i tour in barca alla laguna glaciale di Jökulsárlón e ti avvisa via **WhatsApp** (Twilio) non appena si liberano posti.

Gira **gratis 24/7** su GitHub Actions (cron ogni 15 minuti).

---

## Tour monitorati

| Tour | URL |
|------|-----|
| Zodiac Boat Tour | https://icelagoon.is/tours/zodiac-boat-tour/ |
| Amphibian Tour | https://icelagoon.is/tours/amphibian-tours/ |
| Adventure Tour | https://www.icelagoon.com/adventure-tour/ |

Tutti e tre usano il sistema di prenotazione **Bokun**. Lo script usa Playwright per rendere la pagina, intercettare le chiamate API di Bokun e leggere la disponibilità del 18 agosto.

---

## Setup (10 minuti)

### 1. Twilio WhatsApp (se non l'hai già attivo dall'Oktoberfest)
Se hai già Twilio attivo dal progetto Oktoberfest puoi riusare le stesse credenziali.

Altrimenti:
1. Crea account su [twilio.com](https://www.twilio.com) (trial gratuito)
2. Vai su **Messaging → Try it out → Send a WhatsApp message**
3. Segui le istruzioni per collegare il tuo numero WhatsApp al sandbox Twilio
4. Nota: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM` (`whatsapp:+14155238886`), `WA_TO` (`whatsapp:+39...`)

### 2. Crea il repo su GitHub

```bash
cd jokulsarlon_pinger
git init
git add .
git commit -m "Initial commit"
# Crea repo su github.com (pubblico = minuti illimitati), poi:
git remote add origin https://github.com/TUO_USERNAME/jokulsarlon_pinger.git
git push -u origin main
```

### 3. Aggiungi i GitHub Secrets

Su GitHub → repo → **Settings → Secrets and variables → Actions → New repository secret**:

| Nome | Valore |
|------|--------|
| `TWILIO_ACCOUNT_SID` | Il tuo Account SID (inizia con AC...) |
| `TWILIO_AUTH_TOKEN` | Il tuo Auth Token |
| `TWILIO_FROM` | `whatsapp:+14155238886` (sandbox Twilio) |
| `WA_TO` | `whatsapp:+39XXXXXXXXXX` (il tuo numero) |

### 4. Attiva le GitHub Actions

Vai su **Actions** nel repo → abilita i workflow se richiesto → clicca **"Run workflow"** per un test manuale.

---

## Come funziona

```
GitHub Actions (cron */15 * * * *)
    │
    ├── checkout repo (include state.json)
    ├── pip install + playwright install chromium
    ├── python checker.py
    │       │
    │       ├── Per ogni tour (Zodiac, Amphibian, Adventure):
    │       │       ├── Playwright apre la pagina
    │       │       ├── Intercetta chiamate API Bokun (JSON availability)
    │       │       ├── Cerca il 18 agosto 2026 nei dati
    │       │       ├── Se DISPONIBILE e non ancora notificato → WhatsApp via Twilio
    │       │       └── Aggiorna state.json
    │       │
    │       └── Se 6+ errori consecutivi → WhatsApp di avviso
    │
    └── git commit state.json (solo se cambiato) → git push
```

---

## Test locale

```bash
pip install -r requirements.txt
playwright install chromium

export TWILIO_ACCOUNT_SID="ACxxxxxxxx"
export TWILIO_AUTH_TOKEN="xxxxxxxx"
export TWILIO_FROM="whatsapp:+14155238886"
export WA_TO="whatsapp:+39XXXXXXXXXX"

python checker.py
```

Per testare senza inviare veri messaggi, imposta `SIMULATE_AVAILABLE=1` (simula disponibilità) e commenta temporaneamente la chiamata `send_whatsapp` in `main()`.

---

## Note tecniche

- I siti usano **Bokun** con widget JavaScript in iframe — non è possibile rilevare la disponibilità da HTML statico
- Playwright renderizza la pagina completa e intercetta le risposte JSON dell'API Bokun
- Se l'API non restituisce dati per il mese di agosto, lo script prova a navigare il calendario cliccando "mese successivo"
- `state.json` viene committato ad ogni run per ricordare quali tour sono già stati notificati (evita spam WhatsApp)
