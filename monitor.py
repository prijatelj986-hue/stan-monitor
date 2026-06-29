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
import re
import requests
from bs4 import BeautifulSoup

# ════════════════════════════════════════════════════════════════════
#  KONFIGURACIJA  —  menjaj SAMO ovaj blok
# ════════════════════════════════════════════════════════════════════

CENA_MAX     = 500      # gornja granica (EUR). Sve iznad se odbacuje.
CENA_IDEALNO = 400      # ako je <= ovoga, oglas dobija oznaku ⭐ IDEALNO

# Oglas mora da sadrži BAR JEDNU od ovih reči u lokaciji/naslovu.
# Reon: ceo Novi Beograd + Zemun.
LOKACIJE = [
    "novi beograd", "zemun",
]

# ...ALI ako sadrži bilo koju od ovih reči, ipak se odbacuje (izuzeci iz reona).
ISKLJUCI = [
    "ledin",   # hvata Ledine / Ledina
    "altin",   # hvata Altina / Altine
]

SOBE_MIN          = 1.5    # 1.5 = jednoiposoban naviše -> garantuje odvojenu spavaću
STRIKTNO_NAMESTEN = True   # True = preskoči oglase koji NISU namešteni
                           # (oglasi bez podatka o nameštenosti i dalje prolaze, ali sa upozorenjem)
SPRAT_MAX         = 3      # najviši dozvoljen sprat
LIFT_OBAVEZAN     = True   # True = traži lift (prizemlje i 1. sprat prolaze i bez lifta, zbog kolica)

# terasa i parking se NE filtriraju — samo se prikažu kao oznaka u poruci,
# jer "slobodna zona" parkinga skoro nikad nije polje na oglasu (zavisi od ulice).

SAJTOVI = ["4zida", "halo"]   # nekretnine.rs (Cloudflare) zasad isključen

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


# regex za lokacije: granice reči da "blok 6" NE pogodi "blok 67a"
_LOK_RE = re.compile("|".join(r"\b" + re.escape(k) + r"\b" for k in LOKACIJE), re.I)
_ISKLJUCI_RE = re.compile("|".join(re.escape(k) for k in ISKLJUCI), re.I) if ISKLJUCI else None


# ─────────────────────────── filter ───────────────────────────────────
def prolazi_filter(o: dict) -> bool:
    """Vraća True ako oglas zadovoljava kriterijume."""
    if o.get("cena") is None or o["cena"] > CENA_MAX:
        return False

    tekst = f"{o.get('lokacija','')} {o.get('naslov','')}"
    if not _LOK_RE.search(tekst):
        return False
    if _ISKLJUCI_RE and _ISKLJUCI_RE.search(tekst):   # izuzeci (Ledine, Altine)
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
    if isinstance(o.get("cena"), (int, float)) and o["cena"] <= CENA_IDEALNO:
        flags.append("⭐ IDEALNO")
    if o.get("terasa") is True:
        flags.append("🌿 terasa")
    if o.get("lift") is True:
        flags.append("🛗 lift")
    if o.get("namesten") is None:
        flags.append("⚠️ nameštenost nepoznata")
    return "  ".join(flags)


# ─────────────────────────── Telegram ─────────────────────────────────
def telegram_tekst(tekst: str) -> None:
    """Pošalje običnu tekstualnu poruku (za probnu poruku i sl.)."""
    if not (TG_TOKEN and TG_CHAT):
        print(tekst)
        print("  (nije poslato: nedostaje TELEGRAM_BOT_TOKEN/CHAT_ID)")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": TG_CHAT, "text": tekst, "parse_mode": "HTML"},
            timeout=20,
        )
        if not r.ok:
            print(f"  Telegram greška: {r.status_code} {r.text[:200]}")
        else:
            print("  Telegram: poruka poslata OK")
    except Exception as e:
        print(f"  Telegram izuzetak: {e}")


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

def _cena_eur(text: str):
    """Nađe broj koji stoji uz € ili 'eur' (cena), ne prvi broj u tekstu."""
    m = re.search(r'(\d[\d.\s]{1,7})\s*(?:€|eur)', (text or "").lower())
    return _broj(m.group(1)) if m else None

def _block_text(a) -> str:
    """Tekst najbliže 'kartice' oko linka (cena često stoji van samog linka)."""
    node, best = a, a.get_text(" ", strip=True)
    for _ in range(4):
        node = node.parent
        if node is None:
            break
        t = node.get_text(" ", strip=True)
        if 40 <= len(t) <= 700:
            return t
    return best

def _debug_uzorak(naziv: str, oglasi: list, n: int = 5) -> None:
    """Ispiše prvih par oglasa da se vidi kako izgledaju izvučeni podaci."""
    if not oglasi:
        return
    print(f"  [{naziv}] UZORAK prvih {min(n, len(oglasi))}:")
    for o in oglasi[:n]:
        print(f"    cena={o.get('cena')} sobe={o.get('sobe')} | "
              f"{(o.get('lokacija') or '')[:130]}")

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

def probe(naziv: str, url: str) -> None:
    """Dijagnostika 3: široki, čitljivi isečci oko cena (cela kartica)."""
    print(f"\n===== PROBE3 {naziv} =====")
    print(f"URL: {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        t = r.text
        print(f"status={r.status_code}  '€'={t.count('€')}")
        idxs = [m.start() for m in re.finditer("€", t)]

        def win(i: int) -> str:
            isecak = t[max(0, i - 1400): i + 300]
            # de-escape radi čitljivosti
            isecak = isecak.replace('\\"', '"').replace("\\u002F", "/")
            return re.sub(r"\s+", " ", isecak)

        # uzmi 1., srednju i poslednju cenu — da vidimo i promo i obične kartice
        meta = sorted({0, len(idxs) // 2, len(idxs) - 1})
        for n in meta:
            if 0 <= n < len(idxs):
                print(f"\n--- €#{n+1} ---\n{win(idxs[n])}")
    except Exception as e:
        print(f"PROBE3 greška: {e}")


def probe(naziv: str, url: str) -> None:
    """Osluškuje koje JSON API pozive stranica pravi (tu su pravi oglasi)."""
    print(f"\n===== PROBE-NET {naziv} =====")
    print(f"URL: {url}")
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"Playwright nedostupan: {e}")
        return
    hits = []
    try:
        with sync_playwright() as p:
            br = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            pg = br.new_page(user_agent=HEADERS["User-Agent"], locale="sr-RS",
                             viewport={"width": 1280, "height": 1600})

            def on_resp(resp):
                try:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct and resp.request.resource_type in ("xhr", "fetch"):
                        hits.append((resp.status, resp.request.method, resp.url[:180]))
                except Exception:
                    pass
            pg.on("response", on_resp)

            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            for sel in ['#onetrust-accept-btn-handler',
                        'button:has-text("Prihvati")', 'button:has-text("Prihvatam")',
                        'button:has-text("Slažem")', 'button:has-text("Accept")',
                        'button:has-text("Prihvati sve")']:
                try:
                    pg.click(sel, timeout=2000)
                    print(f"  kliknuo kolačiće: {sel}")
                    break
                except Exception:
                    pass
            pg.wait_for_timeout(3500)
            for _ in range(5):
                pg.mouse.wheel(0, 5000)
                pg.wait_for_timeout(1500)
            kartica = len(pg.query_selector_all('a[href*="/izdavanje-stanova/"]'))
            print(f"  kartica '/izdavanje-stanova/' u DOM-u: {kartica}")
            br.close()
    except Exception as e:
        print(f"PROBE-NET greška: {e}")
    print(f"  JSON XHR/fetch zahteva: {len(hits)}")
    vidjeni = set()
    for st, met, u in hits:
        if u in vidjeni:
            continue
        vidjeni.add(u)
        print(f"    {met} {st}  {u}")


def fetch_rendered(url: str, ready_js: str = None, min_count: int = 35,
                   scrolls: int = 6) -> str:
    """Otvori stranicu u headless browseru i SAČEKAJ da se učitaju pravi
    rezultati, pa onda čitaj. Fallback na requests ako Playwright zakaže.
    ready_js: JS izraz (arrow fn) koji vraća broj kartica; čeka se da pređe min_count."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"  [render] Playwright nedostupan ({e}) — koristim requests")
        return requests.get(url, headers=HEADERS, timeout=30).text

    if ready_js is None:   # podrazumevano: 4zida (link sa dugim hex ID-em)
        ready_js = (
            "() => Array.from(document.querySelectorAll('a[href*=\"/izdavanje-stanova/\"]'))"
            ".filter(a => /\\/[0-9a-f]{20,}\\/?$/.test(a.getAttribute('href')||'')).length"
        )
    try:
        with sync_playwright() as p:
            br = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            pg = br.new_page(user_agent=HEADERS["User-Agent"], locale="sr-RS",
                             viewport={"width": 1280, "height": 1600})
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(2500)
            for sel in ['#onetrust-accept-btn-handler',
                        'button:has-text("Prihvati sve")', 'button:has-text("Prihvati")',
                        'button:has-text("Prihvatam")', 'button:has-text("Slažem")',
                        'button:has-text("Accept")']:
                try:
                    el = pg.query_selector(sel)
                    if el and el.is_visible():
                        el.click(timeout=2000)
                        break
                except Exception:
                    pass
            try:
                pg.wait_for_function(f"() => ({ready_js})() > {min_count}", timeout=30000)
            except Exception:
                print(f"  [render] nije prešlo {min_count} kartica u roku — čitam šta ima")
            for _ in range(scrolls):
                pg.mouse.wheel(0, 5000)
                pg.wait_for_timeout(1200)
            try:
                print(f"  [render] kartica u DOM-u: {pg.evaluate(ready_js)}")
            except Exception:
                pass
            html = pg.content()
            br.close()
            return html
    except Exception as e:
        print(f"  [render] greška u browseru ({e}) — koristim requests")
        return requests.get(url, headers=HEADERS, timeout=30).text


def scrape_4zida() -> list:
    """Čita HTML kartice oglasa: <a href=.../id> sa ulicom, lokacijom i cenom."""
    # cena je u PUTANJI (/do-X-evra), sortirano po najnovijem; NBG + Zemun
    urls = [
        f"https://www.4zida.rs/izdavanje-stanova/novi-beograd-beograd/do-{CENA_MAX}-evra?sortiranje=najnoviji",
        f"https://www.4zida.rs/izdavanje-stanova/zemun-beograd/do-{CENA_MAX}-evra?sortiranje=najnoviji",
    ]
    oglasi, vidjeni = [], set()
    id_re = re.compile(r"/[0-9a-f]{20,}/?$")     # link oglasa završava dugim ID-em
    for url in urls:
        try:
            html = fetch_rendered(url)
            soup = BeautifulSoup(html, "lxml")
            for a in soup.select('a[href*="/izdavanje-stanova/"]'):
                href = a.get("href", "")
                if not id_re.search(href) or href in vidjeni:
                    continue
                tekst_a = a.get_text(" ", strip=True)
                m = re.search(r"([\d.]+)\s*€", tekst_a)     # cena unutar kartice
                if not m:
                    continue
                vidjeni.add(href)
                lok_p = a.select_one('p[class*="line-clamp"]')   # "Blok 21, Novi Beograd, Beograd"
                ul_p  = a.select_one('p[class*="truncate"]')     # ulica
                lokacija = lok_p.get_text(" ", strip=True) if lok_p else href.replace("-", " ")
                ulica    = ul_p.get_text(" ", strip=True) if ul_p else ""
                oglasi.append({
                    "id": "4zida-" + href,
                    "naslov": (f"{ulica} — {lokacija}".strip(" —"))[:90] or "Stan",
                    "cena": _broj(m.group(1)),
                    "lokacija": lokacija,
                    "sobe": _sobe_iz_teksta(href),
                    "url": ("https://www.4zida.rs" + href) if href.startswith("/") else href,
                })
        except Exception as e:
            print(f"  [4zida] greška ({url[:60]}...): {e}")
        time.sleep(2)
    print(f"  [4zida] nađeno: {len(oglasi)}")
    _debug_uzorak("4zida", oglasi)
    return oglasi


def scrape_nekretnine() -> list:
    url = (f"https://www.nekretnine.rs/stambeni-objekti/stanovi/"
           f"izdavanje-prodaja/izdavanje/grad/beograd/opstina/novi-beograd/"
           f"lista/po-stranici/20/")
    oglasi, vidjeni = [], set()
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        print(f"  [nekretnine] status: {r.status_code}")
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        for a in soup.select("a[href*='/stambeni-objekti/']"):
            href = a.get("href", "")
            if len([p for p in href.split("/") if p]) < 3 or href in vidjeni:
                continue
            vidjeni.add(href)
            blok = _block_text(a)
            oglasi.append({
                "id": "nekr-" + href,
                "naslov": (a.get_text(" ", strip=True)[:80] or "Stan"),
                "cena": _cena_eur(blok),
                "lokacija": blok,
                "sobe": _sobe_iz_teksta(blok),
                "url": href if href.startswith("http") else "https://www.nekretnine.rs" + href,
            })
    except Exception as e:
        print(f"  [nekretnine] greška: {e}")
    print(f"  [nekretnine] nađeno: {len(oglasi)}")
    _debug_uzorak("nekretnine", oglasi)
    return oglasi


SCRAPERI = {"4zida": scrape_4zida, "nekretnine": scrape_nekretnine}


def _halo_sprat(txt: str):
    """'III/7 Spratnost' -> 3 ; 'PR/4' -> 0 ; 'VPR' -> 0 ; '4/7' -> 4."""
    val = re.split(r"spratnost", txt, flags=re.I)[0].split("/")[0].strip().upper()
    mapa = {"SUT": -1, "PSUT": -1, "PR": 0, "VPR": 0, "NPR": 0,
            "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
            "VIII": 8, "IX": 9, "X": 10, "PK": 50, "PTK": 50}
    if val in mapa:
        return mapa[val]
    m = re.match(r"(\d+)", val)
    return int(m.group(1)) if m else None


def scrape_halo() -> list:
    """halooglasi.com — kartice div.product-item sa cenom, lokacijom, sobama, spratom.
    Čita strane 1-2 (sortirano: prvo najnoviji), preskače promovisane bez spama."""
    baza = ("https://www.halooglasi.com/nekretnine/izdavanje-stanova/beograd-novi-beograd"
            f"?cena_d_to={CENA_MAX}&cena_d_unit=4")   # cena_d_unit=4 = EUR
    oglasi, vidjeni = [], set()
    ready = "() => document.querySelectorAll('div.product-item[data-id]').length"
    for strana in (1, 2):
        url = baza if strana == 1 else f"{baza}&page={strana}"
        pre = len(vidjeni)
        try:
            html = fetch_rendered(url, ready_js=ready, min_count=8)
            soup = BeautifulSoup(html, "lxml")
            for card in soup.select("div.product-item[data-id]"):
                did = card.get("data-id")
                if not did or did in vidjeni:
                    continue
                vidjeni.add(did)
                cena_el = card.select_one(".central-feature span[data-value]")
                cena = _broj(cena_el.get("data-value")) if cena_el else None
                a = (card.select_one("h3 a[href]")
                     or card.select_one('a[href*="/nekretnine/izdavanje-stanova/"]'))
                naslov = a.get_text(" ", strip=True) if a else "Stan"
                href = a.get("href", "") if a else ""
                url_o = href if href.startswith("http") else "https://www.halooglasi.com" + href
                mesta = [li.get_text(" ", strip=True) for li in card.select("ul.subtitle-places li")]
                lokacija = ", ".join(m for m in mesta if m)
                sobe = sprat = None
                for li in card.select("ul.product-features li"):
                    txt = li.get_text(" ", strip=True)
                    low = txt.lower()
                    if "broj soba" in low:
                        msoba = re.search(r"([\d.]+)", txt)
                        if msoba:
                            sobe = float(msoba.group(1))
                    elif "spratnost" in low:
                        sprat = _halo_sprat(txt)
                oglasi.append({
                    "id": "halo-" + did,
                    "naslov": naslov[:90],
                    "cena": cena,
                    "lokacija": lokacija,
                    "sobe": sobe,
                    "sprat": sprat,
                    "url": url_o,
                })
        except Exception as e:
            print(f"  [halo] greška (strana {strana}): {e}")
        if len(vidjeni) == pre:    # strana nije dodala ništa novo — nema dalje
            break
        time.sleep(2)
    print(f"  [halo] nađeno: {len(oglasi)}")
    _debug_uzorak("halo", oglasi)
    return oglasi


SCRAPERI = {"4zida": scrape_4zida, "nekretnine": scrape_nekretnine, "halo": scrape_halo}


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
def probe_halo() -> None:
    """Dijagnostika za halooglasi: render + kolačići, izbroji oglase i pokaže karticu."""
    url = "https://www.halooglasi.com/nekretnine/izdavanje-stanova/beograd-novi-beograd"
    print(f"\n===== PROBE HALO =====\nURL: {url}")
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f"Playwright nedostupan: {e}")
        return
    try:
        with sync_playwright() as p:
            br = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            pg = br.new_page(user_agent=HEADERS["User-Agent"], locale="sr-RS",
                             viewport={"width": 1280, "height": 1600})
            pg.goto(url, wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(2500)
            for sel in ['#onetrust-accept-btn-handler', 'button:has-text("Prihvati")',
                        'button:has-text("Prihvatam")', 'button:has-text("Slažem")',
                        'button:has-text("Accept")', 'button:has-text("Prihvati sve")']:
                try:
                    el = pg.query_selector(sel)
                    if el and el.is_visible():
                        el.click(timeout=2000)
                        print(f"  kliknuo kolačiće: {sel}")
                        break
                except Exception:
                    pass
            pg.wait_for_timeout(3000)
            for _ in range(4):
                pg.mouse.wheel(0, 5000)
                pg.wait_for_timeout(1200)
            t = pg.content()
            br.close()
    except Exception as e:
        print(f"PROBE HALO greška: {e}")
        return

    soup = BeautifulSoup(t, "lxml")
    linkovi = soup.select('a[href*="/nekretnine/izdavanje-stanova/"]')
    print(f"  duzina HTML={len(t)}  linkova ka oglasima={len(linkovi)}  '€' u tekstu={t.count('€')}")
    # pokaži širi isečak oko prve i srednje cene da vidim strukturu kartice
    idxs = [m.start() for m in re.finditer("€", t)]
    for n in sorted({0, len(idxs) // 2}):
        if 0 <= n < len(idxs):
            i = idxs[n]
            isecak = re.sub(r"\s+", " ", t[max(0, i - 1500): i + 200])
            print(f"\n--- €#{n+1} ---\n{isecak}")


def main():
    dry  = "--dry-run" in sys.argv
    test = "--test" in sys.argv

    if "--ping" in sys.argv:
        telegram_tekst("✅ <b>PROBNA PORUKA</b>\nstan-monitor radi i Telegram stiže. 🎉")
        print("Poslata probna poruka.")
        return

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
