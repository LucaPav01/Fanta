# Confronto Fonti: CSV Estratto vs EXCEL Origine

## Riepilogo Esecutivo
- **CSV**: 716 giocatori, 10 colonne, dati di quotazione (prezzi per 4 formati lega)
- **EXCEL**: 565 giocatori, 11 colonne, dati anagrafi e metriche performance
- **Conclusione**: Due dataset **completamente diversi** — non sono versioni dello stesso dato

---

## 1. Struttura Colonne

### CSV (10 colonne)
1. **Ruolo** — A/C/D/P (sigla)
2. **Squadra** — Nome squadra (es. Roma, Inter)
3. **Nome** — Cognome+Nome concatenato senza spazi (es. `MALENDonyell`)
4. **Kap.** — Capitalizzazione? (numero intero)
5-8. **Prezzi per 4 formati** — `8sq/350`, `10sq/350`, `8sq/500`, `10sq/500` (decimali con punto)
9. **M.V.** — Valore medio (decimale con virgola, es. `6,72`)
10. **Pres.** — Presenze (numero intero)

### EXCEL (11 colonne)
1. **Nome** — Cognome spazio Nome (es. `MALEN Donyell`)
2. **RAT** — Rating? (numero)
3. **POT** — Potential? (numero)
4. **IS %** — Indice Statistico percentuale
5. **ETA'** — Età giocatore
6. **Ruolo standard** — Descrizione completa ruolo
7. **Ruolo trequartista** — Variante ruolo trequartista
8. **Ruolo Fantacalcio.it** — Ruolo secondo standard Fantacalcio.it
9. **Posizione** — Duplicato semantico di Ruolo?
10. **Squadra** — Nome squadra
11. **Kapitals** — Capitalizzazione (stesso concetto di `Kap.` nel CSV?)

---

## 2. Volume Dati

| Fonte | Giocatori | Note |
|-------|-----------|------|
| **CSV** | 716 | Completo (tutti i giocatori in asta) |
| **EXCEL** | 565 | Incompleto, 151 giocatori mancanti |

**Ipotesi**: EXCEL è un export vecchio o da una sezione parziale della pagina.

---

## 3. Scope Semantico Diverso

### CSV = **Dati di quotazione/asta**
- Prezzi per diversi formati lega (8sq a 350k, 8sq a 500k, etc.)
- Presenze (partite giocate)
- Valore medio (rating di performance)
- **Use case**: decidere quanto offrire all'asta per un giocatore

### EXCEL = **Dati anagrafi giocatore + metriche**
- Rating, Potential, Indice Statistico (metriche di performance brute)
- Età (dato anagrafico)
- 3 varianti di ruolo (semantica poco chiara — perché 3 colonne separate?)
- **Use case**: analizzare il profilo del giocatore, non prendere decisioni di asta

---

## 4. Anomalie Rilevate

### A) Nomi: Formato incompatibile
```
CSV:   MALENDonyell        (COGNOME+Nome senza spazi, difficile da parsare)
EXCEL: MALEN Donyell       (COGNOME spazio Nome, standard)
```
**Rischio**: Join tra fonti richiede normalizzazione nomi (fuzzy matching o spazio intelligente).

### B) Ruoli: Semantica incerta
```
CSV:   A, C, D, P              (4 valori unici, chiaro ma poco descrittivo)
EXCEL: 3 colonne separate      (Ruolo std, trq, Fantacalcio.it — poco chiara la differenza)
```
**Rischio**: Non è chiaro quale sia la colonna "canonica" per il ruolo.

### C) Decimali: Formato inconsistente 🚨
```
CSV prezzi:  121.73  (punto, US format)
CSV M.V.:    6,72    (virgola, IT format)
```
**Rischio critico**: Un foglio Excel/Sheets italiano interpreterà correttamente solo la virgola, non il punto. I prezzi potrebbero essere mal interpretati come testo o numeri errati.

### D) Colonne non correlabili
```
CSV ha:     Prezzi asta (4 colonne)
EXCEL non ha: Prezzi asta

EXCEL ha:    RAT, POT, IS%, ETA', 3x Ruolo
CSV non ha:  Nessuno di questi
```
**Conclusione**: Non sono gli stessi dati in due formati diversi.

---

## 5. Origine e Integrità

| Aspetto | Dettaglio |
|---------|-----------|
| **CSV** | Estratto da HTML di [fantacalcio-online.com](https://www.fantacalcio-online.com/it/asta-fantacalcio-stima-prezzi) (pagina prezzi/asta) — **Recente** |
| **EXCEL** | Scaricato manualmente dallo stesso sito — **Data sconosciuta** |
| **Ipotesi** | EXCEL è un export della sezione "Giocatori" (anagrafi), non della sezione "Asta" (prezzi) |

---

## 6. Raccomandazioni

### ✅ Usa il CSV per:
- Costruire il database di asta (prezzi, presenze)
- Decidere strategie di offerta per singoli giocatori
- Confronti tra formati lega

### ⚠️ Non usare l'EXCEL per:
- Prezzi (non contiene dati di asta)
- Decisioni di quotazione

### 🔧 Se vuoi unificare i due dataset:

1. **Normalizzare nomi**
   - CSV: split ultimo spazio per separare cognome da nome
   - Usare cognome come chiave di join (Fuzzy match nel caso di omonimi)

2. **Fissare decimal separator**
   - CSV: convertire tutti i `6,72` in `6.72` prima di importare in Sheets
   - Verifica se questo vale anche per altre colonne (Kap., ecc.)

3. **Decidere ruolo canonica**
   - CSV ha 1 colonna (semplice, chiara)
   - EXCEL ha 3 colonne (ridondante, confusa)
   - **Suggerimento**: usare CSV come fonte primary, aggiungere EXCEL come metadata secondario se serve

4. **Non fare join 1:1**
   - Verificare prima quali giocatori sono presenti in entrambi (716 vs 565)
   - 151 giocatori mancano in EXCEL

---

## 7. Anomalie Specifiche da Controllare

| Riga CSV | Nome | Kap | Nota |
|----------|------|-----|------|
| 1 | MALENDonyell | 61 | Nessun valore Pres., M.V. presente |
| 5 | RAMOSGoncalo Matias | 47 | M.V. vuoto |
| 7 | KOLO MUANIRandal | 43 | M.V. e Pres. vuoti (giocatore new?) |
| 36 | MORA CARVALHORodrigo | 26 | M.V. vuoto |
| 45 | NKUNKUChristopher2025/2026 | 23 | Nome contiene anno (data import?) |

**Pattern**: Giocatori nuovi o con carriera recente hanno dati M.V./Pres. mancanti.

---

## 8. Checklist Data Cleaning

- [ ] Normalizzare nomi CSV (split cognome/nome)
- [ ] Convertire decimali virgola → punto (M.V. e altre colonne float)
- [ ] Verificare completezza: 716 righe in CSV, nessun dato mancante se non previsto
- [ ] Decidere strategia join: CSV primary, EXCEL secondary (se serve)
- [ ] Mapping ruoli: decidere quale colonna è "canonica" (CSV vs EXCEL)
- [ ] Controllare giocatori "Estero" e "2025/2026" — anomalia di parsing?

---

**Generato**: Analisi strutturale, nessun dato modificato.
