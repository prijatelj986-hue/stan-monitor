#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Stan-monitor — prati sajtove za izdavanje stanova u Beogradu i šalje
Telegram obaveštenje kad izađe nov oglas koji odgovara kriterijumima.

Pokretanje:
    python monitor.py            # pravi rad (skida sajtove, šalje Telegram)
    python monitor.py --dry-run  # skida sajtove, ali NE šalje (samo ispiše)
    python monitor.py --test     # bez interneta: ubaci lažne oglase, dokaz da motor radi
"""

import os
import sys
import json
import time
import html
import pathlib
import requests
from bs4 import BeautifulSoup

# ════════════════════════════════════════════════════════════════════
#  KONFIGURACIJA  —  menjaj SAMO ovaj blok
# ════════════════════════════════════════════════════════════════════

CENA_MAX     = 450      # gornja granica (EUR). Sve iznad se odbacuje.
CENA_IDEALNO = 400      # ako je <= ovoga, oglas dobija oznaku ⭐ IDEALNO

# Oglas mora da sadrži BAR JEDNU od ovih reči u lokaciji/naslovu.
# Prefilovano za istočni/centralni Novi Beograd (Paviljoni, Fontana,
# Stari Merkator i blokovi ~5 min kolima okolo). Slobodno dodaj/skini.
LOKACIJE = [
    "paviljoni", "studentski grad", "fontana", "merkator",
    "blok 1", "blok 2", "blok 3", "blok 4", "blok 5", "blok 6",
    "blok 7", "blok 8", "blok 9", "blok 11",
    "blok 19", "blok 20", "blok 21", "blok 22", "blok 23",
    "blok 28", "blok 29", "blok 30", "blok 31",
    "blok 32", "blok 33", "blok 34", "blok 37", "blok 38",
    "pariske komune", "goce delčeva", "goce delceva",
    "bulevar mihajla pupina", "tošin bunar", "tosin bunar",
]

SOBE_MIN          = 1.5    # 1.5 = jednoiposoban naviše -> garantuje odvojenu spavaću
STRIKTNO_NAMESTEN = True   # True = preskoči oglase koji NISU namešteni
                           # (oglasi bez podatka o nameštenosti i dalje prolaze, ali sa upozorenjem)
SPRAT_MAX         = 3      # najviši dozvoljen sprat
LIFT_OBAVEZAN     = True   # True = traži lift (prizemlje i 1. sprat prolaze i bez lifta, zbog kolica)

# terasa i parking se NE filtriraju — samo se prikažu kao oznaka u poruci,
# jer "slobodna zona" parkinga skoro nikad nije polje na oglasu (zavisi od ulice).

SAJTOVI = ["4zida", "nekretnine"]   # koje izvore da proverava

# ════════════════════════════════════════════════════════════════════
#  (ispod ovoga ne moraš ništa da diraš)
# ════════════════════════════════════════════════════════════════════

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept-Language": "sr,en;q=0.9",
}

STATE_FILE = pathlib.Path(__file__).parent / "seen.json"
TG_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TG_CHAT    = os.environ.get("TELEGRAM_CHAT_ID", "")


# ─────────────────────────── stanje (već viđeni oglasi) ────────────────
def load_seen() -> set:
    try:
        return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()

def save_seen(seen: set) -> None:
    STATE_FILE.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=0),
                          encoding="utf-8")


# ─────────────────────────── filter ───────────────────────────────────
def prolazi_filter(o: dict) -> bool:
    """Vraća True ako oglas zadovoljava kriterijume."""
    if o.get("cena") is None or o["cena"] > CENA_MAX:
        return False

    tekst = f"{o.get('lokacija','')} {o.get('naslov','')}".lower()
    if not any(k in tekst for k in LOKACIJE):
        return False

    if o.get("sobe") is not None and o["sobe"] < SOBE_MIN:
        return False

    if STRIKTNO_NAMESTEN and o.get("namesten") is False:
        return False

    sprat = o.get("sprat")
    if sprat is not None:
        if sprat > SPRAT_MAX:
            return False
        if LIFT_OBAVEZAN and sprat >= 2 and o.get("lift") is False:
            return False

    return True


def oznake(o: dict) -> str:
    flags = []
    if o.get("cena") is not None and o["cena"] <= CENA_IDEALNO:
        flags.append("⭐ IDEALNO")
    if o.get("terasa") is True:
        flags.append("🌿 terasa")
    if o.get("lift") is True:
        flags.append("🛗 lift")
    if o.get("namesten") is None:
        flags.append("⚠️ nameštenost nepoznata")
    return "  ".join(flags)


# ─────────────────────────── Telegram ─────────────────────────────────
def telegram(o: dict, dry: bool = False) -> None:
    sobe   = f"{o['sobe']:g} soban".replace(".5", ".5") if o.get("sobe") else "?"
    sprat  = f"{o['sprat']}. sprat" if o.get("sprat") is not None else "sprat ?"
    flags  = oznake(o)
    poruka = (
        f"🏠 <b>{html.escape(str(o.get('naslov','Stan')))}</b>\n"
        f"💶 <b>{o.get('cena','?')} €</b>\n"
        f"📍 {html.escape(str(o.get('lokacija','')))}\n"
        f"🛏 {sobe}  ·  🏢 {sprat}\n"
        + (f"{flags}\n" if flags else "")
        + f"🔗 {o.get('url','')}"
    )

    if dry or not (TG_TOKEN and TG_CHAT):
        print("─" * 50)
        print(poruka.replace("<b>", "").replace("</b>", ""))
        if not (TG_TOKEN and TG_CHAT) and not dry:
            print("  (nije poslato: nedostaje TELEGRAM_BOT_TOKEN/CHAT_ID)")
        return

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT, "text": poruka,
                  "parse_mode": "HTML", "disable_web_page_preview": "false"},
            timeout=20,
        )
        if not r.ok:
            print(f"  Telegram greška: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"  Telegram izuzetak: {e}")


# ─────────────────────────── pomoćne za parsiranje ────────────────────
def _broj(txt: str):
    """Izvuče prvi broj iz teksta tipa '420 €' ili '1.350' -> int."""
    if not txt:
        return None
    cif = "".join(c for c in txt.replace(".", "").replace(",", ".") if c.isdigit() or c == ".")
    try:
        return int(float(cif))
    except Exception:
        return None

def _sobe_iz_teksta(t: str):
    t = (t or "").lower()
    if "garsonjer" in t: return 1.0
    if "jednoiposoban" in t or "1.5" in t: return 1.5
    if "jednosoban" in t: return 1.0
    if "dvoiposoban" in t or "2.5" in t: return 2.5
    if "dvosoban" in t: return 2.0
    if "troiposoban" in t: return 3.5
    if "trosoban" in t: return 3.0
    return None


# ─────────────────────────── SCRAPERI ─────────────────────────────────
# NAPOMENA: selektori su best-effort i mogu da zahtevaju doradu posle prvog
# pokretanja ("kalibracija"). Svaki scraper hvata greške i prijavljuje koliko
# je oglasa našao, da se vidi šta radi.

def scrape_4zida() -> list:
    url = (f"https://www.4zida.rs/izdavanje-stanova/novi-beograd"
           f"?cena_d=0&cena_g={CENA_MAX}&valuta=eur")
    oglasi = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        # 1) najpre probaj strukturirane JSON-LD podatke (najstabilnije)
        for tag in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(tag.string or "{}")
            except Exception:
                continue
            items = data if isinstance(data, list) else [data]
            for it in items:
                if not isinstance(it, dict):
                    continue
                if it.get("@type") in ("Product", "Offer", "Apartment", "RealEstateListing"):
                    oglasi.append({
                        "id": "4zida-" + str(it.get("url") or it.get("name") or len(oglasi)),
                        "naslov": it.get("name"),
                        "cena": _broj(str(it.get("offers", {}).get("price", "")) if isinstance(it.get("offers"), dict) else ""),
                        "lokacija": it.get("name", ""),
                        "url": it.get("url"),
                    })
        # 2) fallback: kartice iz HTML-a (selektor proveriti na kalibraciji)
        if not oglasi:
            for card in soup.select("a[href*='/izdavanje-stanova/']"):
                naslov = card.get_text(" ", strip=True)
                href = card.get("href", "")
                if not href or len(naslov) < 8:
                    continue
                oglasi.append({
                    "id": "4zida-" + href,
                    "naslov": naslov,
                    "cena": _broj(naslov),
                    "lokacija": naslov,
                    "sobe": _sobe_iz_teksta(naslov),
                    "url": "https://www.4zida.rs" + href if href.startswith("/") else href,
                })
    except Exception as e:
        print(f"  [4zida] greška: {e}")
    print(f"  [4zida] nađeno: {len(oglasi)}")
    return oglasi


def scrape_nekretnine() -> list:
    url = (f"https://www.nekretnine.rs/stambeni-objekti/stanovi/"
           f"izdavanje-prodaja/izdavanje/grad/beograd/opstina/novi-beograd/"
           f"cena/0_{CENA_MAX}/lista/po-stranici/20/")
    oglasi = []
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        for card in soup.select(".offer, .advert-list .row, article"):
            link = card.select_one("a[href*='/stambeni-objekti/']")
            if not link:
                continue
            naslov = (card.select_one("h2, .offer-title, .text-truncate") or link).get_text(" ", strip=True)
            cena_t = (card.select_one(".offer-price, .price, [class*='price']") or card).get_text(" ", strip=True)
            href = link.get("href", "")
            oglasi.append({
                "id": "nekr-" + href,
                "naslov": naslov,
                "cena": _broj(cena_t),
                "lokacija": naslov,
                "sobe": _sobe_iz_teksta(naslov),
                "url": href if href.startswith("http") else "https://www.nekretnine.rs" + href,
            })
    except Exception as e:
        print(f"  [nekretnine] greška: {e}")
    print(f"  [nekretnine] nađeno: {len(oglasi)}")
    return oglasi


SCRAPERI = {"4zida": scrape_4zida, "nekretnine": scrape_nekretnine}


# ─────────────────────────── lažni podaci za --test ───────────────────
def mock_oglasi() -> list:
    return [
        {"id": "test-1", "naslov": "Dvosoban, Blok 21, kompletno namešten",
         "cena": 390, "lokacija": "Novi Beograd, Blok 21", "sobe": 2.0,
         "sprat": 2, "lift": True, "namesten": True, "terasa": True,
         "url": "https://primer.rs/1"},                                   # treba da PROĐE (idealno)
        {"id": "test-2", "naslov": "Jednoiposoban kod Merkatora",
         "cena": 440, "lokacija": "Stari Merkator, Novi Beograd", "sobe": 1.5,
         "sprat": 3, "lift": True, "namesten": True, "terasa": False,
         "url": "https://primer.rs/2"},                                   # treba da PROĐE
        {"id": "test-3", "naslov": "Garsonjera Fontana",
         "cena": 350, "lokacija": "Fontana", "sobe": 1.0,
         "sprat": 1, "lift": False, "namesten": True,
         "url": "https://primer.rs/3"},                                   # PAD: nema odvojenu spavaću
        {"id": "test-4", "naslov": "Dvosoban Blok 30, nenamešten",
         "cena": 400, "lokacija": "Blok 30", "sobe": 2.0,
         "sprat": 2, "lift": True, "namesten": False,
         "url": "https://primer.rs/4"},                                   # PAD: nije namešten
        {"id": "test-5", "naslov": "Lep dvosoban, Voždovac",
         "cena": 380, "lokacija": "Voždovac", "sobe": 2.0,
         "sprat": 2, "lift": True, "namesten": True,
         "url": "https://primer.rs/5"},                                   # PAD: pogrešna lokacija
        {"id": "test-6", "naslov": "Dvosoban Blok 22, 5. sprat",
         "cena": 420, "lokacija": "Blok 22", "sobe": 2.0,
         "sprat": 5, "lift": True, "namesten": True,
         "url": "https://primer.rs/6"},                                   # PAD: sprat previsok
        {"id": "test-7", "naslov": "Skup dvosoban Blok 19",
         "cena": 520, "lokacija": "Blok 19", "sobe": 2.0,
         "sprat": 1, "namesten": True,
         "url": "https://primer.rs/7"},                                   # PAD: cena
    ]


# ─────────────────────────── glavni tok ───────────────────────────────
def main():
    dry  = "--dry-run" in sys.argv
    test = "--test" in sys.argv

    seen = load_seen() if not test else set()
    print(f"Već viđeno: {len(seen)} oglasa")

    if test:
        svi = mock_oglasi()
        print(f"TEST režim — {len(svi)} lažnih oglasa")
    else:
        svi = []
        for naziv in SAJTOVI:
            fn = SCRAPERI.get(naziv)
            if fn:
                svi += fn()
                time.sleep(2)   # pristojnost prema sajtu

    novi, poslato = [], 0
    for o in svi:
        if o["id"] in seen:
            continue
        if prolazi_filter(o):
            telegram(o, dry=dry or test)
            poslato += 1
        novi.append(o["id"])

    if not test:
        seen.update(novi)
        save_seen(seen)

    print(f"\nNovo provereno: {len(novi)} | poslato obaveštenja: {poslato}")


if __name__ == "__main__":
    main()
