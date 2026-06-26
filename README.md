# Stan-monitor 🏠

Prati sajtove za izdavanje stanova u Beogradu i šalje **Telegram** poruku
kad izađe nov oglas po tvojim kriterijumima. Vrti se besplatno na
**GitHub Actions** (ne treba server ni da ti je PC upaljen).

---

## Šta radi
- Na svakih ~30 min proveri 4zida i nekretnine.rs (lako se dodaju i drugi).
- Filtrira po ceni, lokaciji (Novi Beograd — Paviljoni / Fontana / Stari
  Merkator i blokovi okolo), strukturi, nameštenosti, spratu i liftu.
- Pamti koje je oglase već poslao (`seen.json`) pa te ne spamuje dvaput.

Kriterijume menjaš u bloku **KONFIGURACIJA** na vrhu `monitor.py`.

---

## Podešavanje (jednom, ~10 min)

### 1) Napravi Telegram bota
1. U Telegramu otvori **@BotFather** → pošalji `/newbot` → daj mu ime.
2. Dobiješ **token** (nešto kao `12345:ABC-…`). Sačuvaj ga.
3. Pošalji svom novom botu bilo koju poruku (npr. „zdravo").
4. Saznaj svoj **chat id**: otvori u browseru
   `https://api.telegram.org/bot<TVOJ_TOKEN>/getUpdates`
   i nađi `"chat":{"id": ... }`. Taj broj je `TELEGRAM_CHAT_ID`.
   (Alternativa: piši botu **@userinfobot** koji ti odmah javi tvoj id.)

### 2) Stavi kod na GitHub
- Napravi nov repo i ubaci sve fajlove iz ovog foldera.

### 3) Dodaj tajne (secrets)
Repo → **Settings → Secrets and variables → Actions → New repository secret**:
- `TELEGRAM_BOT_TOKEN` → tvoj token
- `TELEGRAM_CHAT_ID` → tvoj chat id

### 4) Uključi Actions
Repo → tab **Actions** → ako pita, klikni *enable*. Workflow „Provera stanova"
se sad vrti po rasporedu. Možeš ga i ručno pokrenuti dugmetom **Run workflow**.

---

## Lokalno testiranje (na svom PC-u)
```bash
pip install -r requirements.txt

python monitor.py --test      # bez interneta, dokaz da filter radi
python monitor.py --dry-run   # skine sajtove ali NE šalje, samo ispiše
python monitor.py             # pravi rad (treba ti TELEGRAM_* u env)
```

> **Napomena o kalibraciji:** scraperi su podešeni „na slepo" jer sajtovi
> povremeno menjaju strukturu. Posle prvog `--dry-run` pogledaj koliko je
> oglasa našao za svaki sajt; ako piše `nađeno: 0`, treba malo doterati
> selektore — pošalji ispis i lako se sredi.
