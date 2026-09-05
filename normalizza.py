#!/usr/bin/env python3
"""
Regole di normalizzazione per lo staging dell'import Fantacalcio.

Ogni funzione qui produce SOLO valori normalizzati: il valore raw va sempre
conservato affiancato a parte (in player_source_records.raw_data), mai
sovrascritto. Nessuna funzione qui dentro fa merge/matching fuzzy automatico:
i casi ambigui vengono segnalati (validation_errors) e finiscono in
match_review.csv per decisione manuale, come da regola del progetto.
"""
import csv
import re
import sys
import unicodedata
from collections import Counter

# ------------------------------------------------------------------
# Mapping ruoli: CSV fantacalcio-online usa sigle italiane a 1 lettera.
# GK/DEF/MID/FWD è il vocabolario canonico interno (compatibile con altre
# fonti internazionali future). Nessuna compressione automatica di ruoli
# "alternativi" (es. le 3 colonne ruolo dell'Excel restano raw, non entrano
# in questa mappa).
# ------------------------------------------------------------------
ROLE_MAP_CSV = {
    'P': 'GK',
    'D': 'DEF',
    'C': 'MID',
    'A': 'FWD',
}

# Suffisso stagione che compare appiccicato ad alcuni nomi nel CSV
# (es. "NKUNKUChristopher2025/2026", "PATRIC-2025/2026").
_SEASON_SUFFIX_RE = re.compile(r"-?\d{4}/\d{4}$")

# Etichetta UI "Nuovo" (nuovo arrivo) appiccicata in coda al nome senza
# separatore (es. "BRAGANCADaniel SantosNuovo"): non fa parte del nome.
_NUOVO_SUFFIX_RE = re.compile(r"Nuovo$")

# Split cognome/nome per stringhe "COGNOMEValidNome" (nessuno spazio tra i due).
# Cerca la PRIMA transizione lettera-maiuscola -> lettera-minuscola: il cognome
# è tutto in maiuscolo (spazi, apostrofi, trattini ammessi), il nome inizia
# con maiuscola seguita da minuscola. Se non c'è nessuna transizione valida
# (es. lettera accentata minuscola dentro il cognome, dato corrotto) il match
# fallisce e il caso va a revisione manuale invece di essere indovinato.
_SPLIT_RE = re.compile(r"^([A-ZÀ-ÖØ-Þ'’\- ]+?)([A-ZÀ-ÖØ-Þ][a-zà-öø-ÿ].*)$")

# Lettere ammesse per un cognome scritto tutto in maiuscolo (nessun nome
# separato fornito dalla fonte, es. "PATRIC"). Include l'apostrofo curvo
# (’, U+2019) oltre a quello dritto: variante di encoding vista in alcuni
# cognomi (es. "N’DRI"), non un'ambiguità di dato.
_ALL_CAPS_RE = re.compile(r"^[A-ZÀ-ÖØ-Þ'’\- ]+$")


def strip_accents(s: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', s) if unicodedata.category(c) != 'Mn')


def make_key(s: str) -> str:
    """Chiave di normalizzazione per matching: NFKC, maiuscolo, senza accenti/punteggiatura/spazi."""
    s = unicodedata.normalize('NFKC', s or '')
    s = strip_accents(s)
    s = s.upper()
    s = re.sub(r'[^A-Z0-9]', '', s)
    return s


def strip_season_suffix(raw_name: str) -> tuple[str, bool]:
    """Rimuove un suffisso stagione tipo '2025/2026' (con eventuale '-' davanti)."""
    cleaned = _SEASON_SUFFIX_RE.sub('', raw_name or '')
    return cleaned, cleaned != raw_name


def strip_nuovo_label(raw_name: str) -> tuple[str, bool]:
    """Rimuove l'etichetta UI 'Nuovo' appiccicata in coda al nome."""
    cleaned = _NUOVO_SUFFIX_RE.sub('', raw_name or '')
    return cleaned, cleaned != raw_name


def strip_disambiguator_initials(cognome: str | None, nome: str | None) -> str | None:
    """
    Alcune righe hanno iniziali di disambiguazione omonimi incollate in coda
    al cognome, senza separatore dal nome che segue (es. "ESPOSITO FPFrancesco
    Pio" -> cognome grezzo "ESPOSITO FP", nome "Francesco Pio"; "ESPOSITO
    SSebastiano" -> cognome grezzo "ESPOSITO S", nome "Sebastiano"). Se
    l'ultimo token del cognome coincide con le iniziali del nome, non è
    cognome ma disambiguatore: va tolto per non nascondere l'omonimia.
    """
    if not cognome or not nome:
        return cognome
    tokens = cognome.split(' ')
    if len(tokens) < 2:
        return cognome
    last = tokens[-1]
    if not (1 <= len(last) <= 4 and last.isalpha() and last == last.upper()):
        return cognome
    initials = ''.join(word[0] for word in nome.split() if word)
    if last == initials.upper():
        return ' '.join(tokens[:-1])
    return cognome


def split_full_name(name_clean: str) -> tuple[str | None, str | None, bool]:
    """
    Divide un nome "COGNOMENome" in (cognome, nome, ambiguous).
    ambiguous=True significa: non split-abile automaticamente, va a revisione manuale.
    Nomi tutto-maiuscolo senza parte minuscola sono un caso valido (nessun nome fornito),
    NON ambiguo.
    """
    name_clean = (name_clean or '').strip()
    if not name_clean:
        return None, None, True

    if _ALL_CAPS_RE.match(name_clean):
        return name_clean, None, False

    m = _SPLIT_RE.match(name_clean)
    if not m:
        return None, None, True

    cognome, nome = m.group(1).strip(), m.group(2).strip()
    if not cognome or not nome:
        return None, None, True
    return cognome, nome, False


def parse_decimal_comma(raw: str) -> float | None:
    """Numeri in formato IT: virgola come separatore decimale (es. M.V. '6,72')."""
    raw = (raw or '').strip()
    if not raw:
        return None
    return float(raw.replace(',', '.'))


def parse_decimal_dot(raw: str) -> float | None:
    """Numeri già in formato con punto (es. prezzi CSV '121.73')."""
    raw = (raw or '').strip()
    if not raw:
        return None
    return float(raw)


def parse_int(raw: str) -> int | None:
    raw = (raw or '').strip()
    if not raw:
        return None
    return int(raw)


def normalize_csv_row(row: dict, row_number: int) -> dict:
    """
    Normalizza una riga del CSV quotazioni (fantacalcio-online).
    Ritorna raw + normalizzato affiancati, mai il raw sovrascritto.
    """
    errors: list[str] = []       # log completo, incluse le note puramente informative
    review_errors: list[str] = []  # solo i casi che richiedono una decisione manuale

    ruolo_raw = (row.get('Ruolo') or '').strip()
    ruolo_normalizzato = ROLE_MAP_CSV.get(ruolo_raw)
    if ruolo_normalizzato is None:
        msg = f"ruolo sconosciuto: {ruolo_raw!r}"
        errors.append(msg)
        review_errors.append(msg)

    squadra_raw = (row.get('Squadra') or '').strip()
    team_key = make_key(squadra_raw)
    if not team_key:
        msg = "squadra mancante"
        errors.append(msg)
        review_errors.append(msg)

    nome_raw = row.get('Nome') or ''
    nome_clean, had_season_suffix = strip_season_suffix(nome_raw)
    if had_season_suffix:
        # rimozione automatica riuscita: solo nota informativa, non richiede revisione
        errors.append("suffisso stagione rimosso dal nome (es. '2025/2026')")

    nome_clean, had_nuovo_label = strip_nuovo_label(nome_clean)
    if had_nuovo_label:
        # rimozione automatica riuscita: solo nota informativa, non richiede revisione
        errors.append("etichetta 'Nuovo' rimossa dal nome")

    cognome, nome, ambiguous = split_full_name(nome_clean)
    if ambiguous:
        msg = "nome non parsabile automaticamente (split cognome/nome ambiguo)"
        errors.append(msg)
        review_errors.append(msg)
    else:
        cognome = strip_disambiguator_initials(cognome, nome)

    name_key = make_key(nome_clean)
    if not name_key:
        msg = "nome mancante"
        errors.append(msg)
        review_errors.append(msg)

    try:
        kap = parse_int(row.get('Kap.'))
    except ValueError:
        kap = None
        msg = f"Kap. non numerico: {row.get('Kap.')!r}"
        errors.append(msg)
        review_errors.append(msg)

    prices = {}
    for field, out_key in (
        ('8 sq. / 350', 'price_8sq_350'),
        ('10 sq. / 350', 'price_10sq_350'),
        ('8 sq. / 500', 'price_8sq_500'),
        ('10 sq. / 500', 'price_10sq_500'),
    ):
        try:
            prices[out_key] = parse_decimal_dot(row.get(field))
        except ValueError:
            prices[out_key] = None
            msg = f"{field} non numerico: {row.get(field)!r}"
            errors.append(msg)
            review_errors.append(msg)

    try:
        mv = parse_decimal_comma(row.get('M.V.'))
    except ValueError:
        mv = None
        msg = f"M.V. non numerico: {row.get('M.V.')!r}"
        errors.append(msg)
        review_errors.append(msg)

    try:
        presenze = parse_int(row.get('Pres.'))
    except ValueError:
        presenze = None
        msg = f"Pres. non numerico: {row.get('Pres.')!r}"
        errors.append(msg)
        review_errors.append(msg)

    return {
        'source_row_number': row_number,
        'raw': dict(row),
        'ruolo_raw': ruolo_raw,
        'ruolo_normalizzato': ruolo_normalizzato,
        'squadra_raw': squadra_raw,
        'team_key': team_key,
        'nome_raw': nome_raw,
        'nome_clean': nome_clean,
        'cognome': cognome,
        'nome': nome,
        'name_key': name_key,
        'name_ambiguous': ambiguous,
        'kap': kap,
        **prices,
        'mv': mv,
        'presenze': presenze,
        'validation_errors': errors,
        'needs_manual_review': bool(review_errors),
    }


def _run_report(csv_path: str) -> None:
    """Esegue la normalizzazione su tutto il CSV e stampa un report di verifica."""
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = [normalize_csv_row(row, i + 1) for i, row in enumerate(reader)]

    total = len(rows)
    ambiguous_rows = [r for r in rows if r['name_ambiguous']]
    season_suffix_rows = [r for r in rows if any('suffisso stagione' in e for e in r['validation_errors'])]
    role_errors = [r for r in rows if r['ruolo_normalizzato'] is None]
    team_keys = Counter(r['team_key'] for r in rows)
    name_key_collisions = Counter(r['name_key'] for r in rows)
    duplicate_name_keys = {k: c for k, c in name_key_collisions.items() if c > 1}

    print(f"Righe totali:              {total}")
    print(f"Ruoli non mappati:         {len(role_errors)}")
    print(f"Nomi con suffisso stagione rimosso: {len(season_suffix_rows)}")
    print(f"Nomi ambigui (split fallito): {len(ambiguous_rows)}")
    print(f"Squadre distinte (team_key): {len(team_keys)}")
    print(f"name_key duplicati (stesso nome, squadre diverse o omonimi): {len(duplicate_name_keys)}")

    if ambiguous_rows:
        print("\n--- Nomi ambigui (esempi, prime 15) ---")
        for r in ambiguous_rows[:15]:
            print(f"  riga {r['source_row_number']:>4}  nome_raw={r['nome_raw']!r}  squadra={r['squadra_raw']!r}")

    if duplicate_name_keys:
        print("\n--- name_key duplicati (prime 15) ---")
        for k, c in list(duplicate_name_keys.items())[:15]:
            print(f"  {k}  ({c} righe)")

    # match_review.csv: solo le righe che richiedono davvero una decisione manuale
    # (non le note puramente informative come il suffisso stagione rimosso)
    review_rows = [r for r in rows if r['needs_manual_review']]
    if review_rows:
        with open('match_review.csv', 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['source_row_number', 'nome_raw', 'squadra_raw', 'ruolo_raw', 'validation_errors'])
            for r in review_rows:
                writer.writerow([
                    r['source_row_number'],
                    r['nome_raw'],
                    r['squadra_raw'],
                    r['ruolo_raw'],
                    '; '.join(r['validation_errors']),
                ])
        print(f"\n{len(review_rows)} righe scritte in match_review.csv per revisione manuale.")


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else 'fantacalcio_prezzi.csv'
    _run_report(path)
