# Fanta · Asta

App Streamlit privata, ottimizzata per telefono, per consultare i giocatori di Serie A durante l'asta.

## Aggiornare i dati

1. Sostituisci i quattro file sorgente indicati in `app_config.json`, mantenendo gli stessi nomi oppure aggiornando i percorsi nella configurazione. I file Excel sono esclusi da Git e vanno conservati localmente; `fantacalcio_app.db`, invece, deve essere versionato perché è l'artefatto ridotto usato dall'app.
2. Esegui `python3 risolvi_identita.py` per aggiornare alias e casi da verificare.
3. Controlla `match_review.csv`, poi esegui `python3 importa_database.py` dalla radice del repository.
4. Verifica che il report termini senza conteggi inattesi e versiona `fantacalcio_app.db`. Il file contiene solo la vista usata dall'app; i dati raw restano nel database locale ignorato da Git.

Per provare in locale: `streamlit run app.py`.

## Pubblicare privatamente

1. Pubblica le modifiche su un repository GitHub privato.
2. In Streamlit Community Cloud crea un'app indicando repository, branch e `app.py`.
3. In **Sharing**, scegli **Only specific people can view this app** e invita gli indirizzi autorizzati.
4. Da telefono apri il link ricevuto e accedi con Google o con il link monouso inviato via email.

Gli aggiornamenti successivi richiedono solo una nuova importazione e il push del nuovo `fantacalcio_app.db`; Streamlit ridistribuisce automaticamente il commit.
