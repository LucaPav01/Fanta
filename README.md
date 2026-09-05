# Fanta · Asta

PWA mobile-first per consultare i giocatori di Serie A e gestire lo stato dell'asta, anche senza connessione dopo la prima apertura.

## Aggiornare i dati

1. Sostituisci i quattro file sorgente indicati in `app_config.json`, mantenendo gli stessi nomi oppure aggiornando i percorsi nella configurazione. I file Excel sono esclusi da Git e vanno conservati localmente; `fantacalcio_app.db`, invece, deve essere versionato perché è l'artefatto ridotto usato dall'app.
2. Esegui `python3 risolvi_identita.py` per aggiornare alias e casi da verificare.
3. Controlla `match_review.csv`, poi esegui `python3 importa_database.py` dalla radice del repository.
4. Verifica che il report termini senza conteggi inattesi e versiona `fantacalcio_app.db`. Il file contiene solo la vista usata dall'app; i dati raw restano nel database locale ignorato da Git.

Per aggiornare la PWA dopo l'importazione, esegui anche `python3 export_web_data.py`. Per provarla in locale: `cd web && python3 -m http.server 8000`, quindi apri `http://localhost:8000`.

## Pubblicazione

### Vercel — consigliata

1. Importa `LucaPav01/Fanta` in Vercel senza rendere pubblico il repository.
2. Mantieni il framework su **Other**: `vercel.json` pubblica automaticamente la cartella `web/`.
3. Ogni push attiva un nuovo deploy. Il service worker non viene messo in cache dal CDN, quindi le versioni successive dell’app vengono rilevate correttamente.

### GitHub Pages — alternativa

Il workflow `.github/workflows/deploy-pages.yml` è pronto e pubblica il contenuto di `web/` ad ogni push su `main`. Con il piano GitHub Free il repository deve però essere pubblico. `LucaPav01/Fanta` è privato: non cambiarne la visibilità finché non hai scelto esplicitamente questa strada.

## Verifica su iPhone

1. Apri l’URL distribuito in Safari, scegli **Condividi → Aggiungi a Home**, poi avvialo dall’icona: deve essere a schermo intero.
2. Cerca cinque giocatori casuali e verifica anche quelli che prima non venivano trovati (per esempio `Anguissa`, `Ederson`, `Fayed`, `Juan Jesus`, `Nzola`).
3. Registra dieci acquisti, alternando **Preso da me** e **Preso da altri**; controlla crediti, posti per ruolo e la lista con i già presi nascosti.
4. Chiudi completamente l’app, riaprila dall’icona e verifica che preferiti, rosa e crediti siano rimasti invariati.
5. Dopo una prima apertura online, abilita la modalità aereo, riapri l’app e verifica ricerca, dettagli e operazioni d’asta. Salva anche un backup JSON dalla scheda Asta.
