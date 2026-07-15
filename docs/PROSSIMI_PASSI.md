# Prossimi passi — stato e handoff tra sessioni

Questo file si aggiorna **a ogni sessione di lavoro**, tipicamente poco prima di un commit
di fine sessione (es. quando i token stanno per esaurirsi). Contiene: (1) su cosa lavorare
subito dopo, (2) un mini report di cosa e' stato fatto nell'ultima sessione, (3) le cose
discusse in chat con l'utente che si vogliono implementare a breve ma non sono ancora in
codice. Chi riprende il lavoro (Claude, Codex, o chiunque altro, anche l'utente stesso) deve
leggere QUESTO file per primo, poi `docs/ai-craft-architecture.md` per il contesto tecnico
completo, poi la sezione "Da migliorare" dentro l'app (tabella `ImprovementNote`) per i
problemi di qualita' segnalati durante generazioni reali.

---

## REGOLA FISSA — non rimuovere questa sezione

**Ogni agente che lavora su questo progetto (Claude, Codex, o altro) DEVE aggiornare questo
file quando i token/il budget della sessione stanno per esaurirsi, PRIMA dell'ultimo commit
della sessione.** Aggiornamento = aggiungere una nuova voce in cima a "Log sessioni" sotto
(senza cancellare le voci precedenti) + tenere allineate "Task su cui lavorare adesso",
"Intenzioni discusse in chat, non ancora implementate" e la checklist piu' sotto allo stato
reale. Questa regola e la sua descrizione non vanno mai rimosse o riscritte in modo piu'
debole, anche quando si aggiorna il resto del file — e' l'unico modo per cui, quando un altro
agente (umano o AI) riprende il progetto, puo' vedere cosa e' stato fatto nel frattempo senza
dover rileggere un'intera chat che non ha mai visto.

---

## Task su cui lavorare adesso

**Tutti i 7 punti della vecchia checklist "100% operativo" sono FATTI** (vedi checklist sotto,
tutti [x]). L'utente ha chiesto esplicitamente il prossimo blocco di lavoro (15/07/2026 sera):
arricchire l'app con piu' osservabilita' e funzionalita', a partire da:
1. **Tracking a checkpoint per la produzione** (PRIORITARIO, richiesto esplicitamente): oggi
   `ContentPiece.status` cambia stadio per stadio ma non lascia una traccia storica con
   timestamp — non si vede quanto ci ha messo ogni stadio o dove si e' bloccato un pezzo. Serve
   una tabella di log eventi (piece_id, stadio, timestamp, esito/durata) scritta ad ogni
   transizione in `engine.process_content_piece`, + vista timeline per pezzo in "Produzione".
2. Dopo il tracking, arricchire (in quest'ordine di priorita' scelto dall'utente): **Produzione**
   (timeline/retry singolo pezzo/dettaglio errori — naturale seguito del punto 1), **Piano**
   (storico versioni, duplicazione settimana precedente, vista mensile), **Creator/Libreria**
   (statistiche per categoria/performance nel tempo), **Costi** (storico movimenti nel tempo,
   grafico spesa per tipo, proiezione mensile).

## Intenzioni discusse in chat, non ancora implementate

- **Riconciliazione job dopo un errore `--wait`** (scoperta 15/07/2026 nel primo test reale, vedi
  doc §17 e backlog app): un 503/timeout durante `--wait` puo' nascondere un job in realta'
  riuscito e gia' addebitato. Serve controllare `higgsfield generate list` per un job dello stesso
  tipo appena creato prima di arrendersi. Non implementato, solo annotato.

## Checklist "cosa manca per essere operativo al 100%" (TUTTA FATTA, stato 15/07/2026 sera)

1. [x] Analisi video per i talking/caption (dialogo verbatim, movimenti dai frame, audio,
       densita' frame dinamica, timestamp Whisper per segmento) — vedi doc §12.15 e §15.1.
2. [x] Caption originale: `downloader.download_reference` salva `original_caption` su
       `ReferenceItem`; lo stadio caption/hashtag ora la adatta invece di inventare da zero
       quando e' disponibile.
3. [x] **Primo test reale end-to-end fatto** (15/07/2026): 1 talking + 1 balletti, entrambi
       `delivered`. Costi REALI ora verificati: `kling3_0_motion_control` 18cr/10.6s (non 16 come
       stimato a voce), `seedance_2_0` 36cr/8.1s. Vedi doc §17 e la cartella
       `~/Desktop/REVISIONE_TEST_talking_balletti_15-07-2026/` per prompt/output completi.
       `video_references`/`image_reference` remoto restano da testare (toggle ancora OFF).
4. [ ] Fedelta posa/outfit alla foto originale nei caroselli — gia' segnata nella sezione "Da
       migliorare" dell'app, qualita' buona ma migliorabile. Da rivalutare anche sui 2 nuovi
       output del punto 3.
5. [x] Stato dedicato `ContentPiece.status = "content_refused"` per i rifiuti di contenuto di
       Claude, rilevati euristicamente (`_looks_like_refusal`). Vedi doc §16.
6. [x] UI produzione reale + Libreria: bottone "Produci davvero" con conferma esplicita e
       guardia budget; "Aggiorna libreria" usa la policy per categoria.
7. [x] Rifiniture operative UI: agenda del giorno in "Oggi" (§15.2), filtri/retry
       singolo/apertura cartella in Libreria (§15.3).

**Gap reale trovato e corretto durante il punto 3** (non era nella checklist originale): gli asset
generati non venivano mai scaricati in locale, solo l'URL Higgsfield restava in `generated_assets`
— QA/delivery non avrebbero mai funzionato su un asset vero. Fix: `higgsfield_client.download_result`
+ `engine._localize_asset`. Vedi doc §16.

## Log sessioni (piu' recente in cima — AGGIUNGERE una voce nuova, non sovrascrivere le altre)

### 15/07/2026 sera, parte 2 (sessione Claude — punti 3+5 checklist, primo test reale)

- **Gap reale trovato PRIMA del test**: `generated_assets` teneva solo l'URL Higgsfield remoto,
  mai scaricato — QA/delivery non avrebbero mai funzionato su un asset vero (mai emerso perche'
  nessun asset reale era mai arrivato al QA finora). Fix: `higgsfield_client.download_result` +
  `engine._localize_asset`, chiamati dopo ogni generate_image/generate_video/generate_motion_control.
- **Stato dedicato `content_refused`**: `ClaudeContentRefusedError` + `_looks_like_refusal`
  (euristica su frasi di rifiuto), applicato a tutte le funzioni che chiamano Claude per contenuto
  strutturato. `process_content_piece` lo cattura e marca lo stato invece di "error" generico.
- **Primo test reale end-to-end**: 1 video talking (8.1s) + 1 video balletti (10.6s), i piu'
  recenti idonei nello sheet (il talking piu' recente in assoluto, 22.4s, scartato in automatico
  per soglia 15s). Entrambi `delivered`. Trovato e recuperato un caso reale di job Higgsfield
  riuscito ma segnalato come errore dal nostro CLI (503 transitorio su `--wait` per seedance_2_0) —
  recuperato a mano, costo reale registrato. Costi VERIFICATI: kling3_0_motion_control 18cr/10.6s
  (corretto da 16 stimato), seedance_2_0 36cr/8.1s. Cartella di review completa (prompt/output/
  reference) in `~/Desktop/REVISIONE_TEST_talking_balletti_15-07-2026/`. Nuova voce di backlog per
  il gap di riconciliazione `--wait`. 168 test verdi. Vedi doc §16, §17.
- Prossimo (richiesto dall'utente subito dopo): tracking a checkpoint per la produzione (in corso).

### 15/07/2026 (sessione Claude — review del lavoro Codex + rifiniture richieste)

- **Review completa** del commit Codex (`c9aa930`, 1745 righe): letto ogni diff dei file critici
  a mano (non solo il changelog), verificata idempotenza dell'allocator, correttezza della
  migrazione DB additiva, sicurezza dello scheduler (plistlib, no shell string), confermato con
  l'utente che il passaggio Google Sheet da read-only a read-write era intenzionale.
- **Bug corretto**: `reference_sync.run_once()` non ritentava gli stati granulari
  (`download_error`/`unavailable`/`private`/`transcription_error`), solo `run_policy_once()` lo
  faceva — item bloccati per sempre con `references sync` normale. Unificato in
  `RETRYABLE_STATUSES`, una sola costante di modulo. Vedi doc §15 (introduzione).
- **Densita' frame + timestamp Whisper** per l'analisi video talking (era rimasto aperto dalla
  sessione precedente): frame dinamici (~1/secondo, minimo 5) invece di 5 fissi;
  `transcriber.transcribe()` non scarta piu' i segmenti Whisper (nuova colonna
  `ReferenceItem.transcript_segments`); `write_talking_video_prompt` correla dialogo e frame per
  timestamp esatto quando disponibili. Vedi doc §15.1.
- **Vista "Oggi" con agenda del giorno**: nuovo endpoint `today_agenda`, sezione UI con i
  contenuti pianificati per oggi (stato, reference assegnata o no) e avvisi contestuali. Vedi doc
  §15.2.
- **Libreria**: filtri per stato/categoria (`list_references`), retry singolo per reference
  fallita (`retry_reference`, non tocca lo Sheet), apertura cartella locale in Finder
  (`open_reference_folder`, percorso verificato dentro `MEDIA_DIR` per sicurezza). Vedi doc §15.3.
- 158 test verdi. Commit + push su `origin/main`.

### 15/07/2026 (sessione Codex — primi 5 punti operativi)

- Implementato uso della caption originale IG: `claude_creative.adapt_original_caption_and_hashtags`
  adatta `ReferenceItem.original_caption` mantenendo tono/intenzione, con fallback al vecchio
  `write_caption_and_hashtags` solo se la caption sorgente manca.
- Collegata la produzione reale alla UI desktop: endpoint `production_run` con conferma obbligatoria
  `PRODUCI`, auto-assign delle reference prima del check, guardia su budget/ready_count, bottone
  "Produci davvero" nella tab Produzione. CLI aggiornata con `produce --plan` e riepilogo
  consegnati/falliti.
- Aggiunto sync per categoria: `AICRAFT_REFERENCE_SYNC_POLICY`, `parse_sync_policy`,
  `run_policy_once`, endpoint `references_sync_policy`, UI "Aggiorna libreria" collegata alla
  policy. Il vecchio `references sync --limit/--tab/--category` resta disponibile per run manuali.
- Aggiunta automazione settimanale locale: modulo `aicraft/scheduler.py`, CLI
  `scheduler plist` e `scheduler install-weekly-sync`, LaunchAgent macOS che esegue
  `python -m aicraft.cli references sync-policy` e logga in `data/logs`.
- Migliorati stati errore reference: `download_error`, `unavailable`, `private`,
  `transcription_error` invece di solo `error`; la UI conta tutti questi nello stato "Errore".
- Aggiornati test mirati per caption originale, produzione reale protetta, sync policy/errori,
  scheduler. Suite completa: 145 test verdi. Nessun credito Higgsfield usato in questa sessione.

### 15/07/2026 (sessione Codex)

- Aggiunta visibilita' reale alla tab Libreria: conteggi per stato/settimana/categoria, ultimi
  scaricati, reference fuori retention, finestra di pesca e retention. Aggiunto pulsante
  "Aggiorna libreria" che chiama il sync backend.
- Collegata l'approvazione piano all'assegnazione automatica reference: dopo il budget check,
  `approve_plan` API/CLI prova ad assegnare reference e segnala quante mancano. UI mostra un
  messaggio "aggiorna Libreria" quando la coda locale non basta.
- Aggiunto limite batch al sync: `AICRAFT_REFERENCE_SYNC_MAX_ITEMS` (default 25), CLI
  `references sync --limit N`, `--all`, `--tab`, `--category`. Serve a evitare sync enormi
  tipo primo run da 1179 reference.
- Test reale controllato eseguito con rete: Google Sheet letto con permesso edit (1179 reference
  entro retention), download/mark caroselli verificato (3 ready, background letto via metadata
  Sheet = `red=1, green≈0.949, blue≈0.647`), 2 caroselli non disponibili marcati `error`.
  Micro-sync video `VIRAL GENERAL/TALKING --limit 2`: 2 video scaricati in
  `data/media/2026-W26/VIRAL_GENERAL/TALKING/...`, WAV estratti, Whisper eseguito, transcript
  salvato, caption originale salvata, `DONE RICKY` verificato leggendo le celle Sheet (`TRUE`,
  `TRUE`). Nessun credito Higgsfield usato.
- Test flusso Piano reale locale: `plan assign-refs 1` ha assegnato 2 reference TALKING pronte
  a 3 content piece esistenti, lasciandone 1 mancante come previsto. 135 test verdi.

- Implementata la **libreria locale a coda rotante** discussa con l'utente: il sync salva
  settimana/posizione sheet/categoria su `ReferenceItem`, scarica i media in
  `data/media/YYYY-Www/TAB/CATEGORIA/shortcode/`, cattura `original_caption`, e pulisce i
  reference IG oltre `AICRAFT_REFERENCE_RETENTION_DAYS` (default 45) senza toccare gli asset
  generati/consegnati.
- Google Sheet ora usa scope editor (`spreadsheets`) e, dopo download riuscito, può marcare
  lo sheet: video -> colonna `DONE RICKY`; caroselli -> background colorato sulla cella link.
  Il DB resta la fonte vera dello stato operativo.
- Aggiunto `aicraft/reference_sync/allocator.py`: pesca reference `ready` dal DB locale nelle
  ultime `AICRAFT_REFERENCE_SELECTION_WEEKS` settimane disponibili (default 2), ordinando dal
  più vecchio al più nuovo dentro la finestra, escludendo reference già assegnate. Mappature:
  `video_talking` -> `TALKING`, `video_balletti` -> `BALLETTI/LIPSYNC`, `video_caption` ->
  `CAPTION`, `carosello` -> `BOOBS/BOOTY/GENERAL`.
- Aggiunta assegnazione reference da CLI (`plan assign-refs <plan_id>`) e API/UI desktop
  (`assign_plan_references`, pulsante "Assegna reference" nel Piano). Il Production Engine
  prima prova ad assegnare reference ai piani approvati e poi produce solo pezzi con
  `reference_id` valorizzato.
- Migrazione additiva leggera in `db/base.py` per aggiungere le nuove colonne al DB locale
  esistente senza Alembic. 132 test verdi.

### 15/07/2026 (sessione Claude)

- Aggiunta sezione "Da migliorare" (backlog) nell'app desktop: modello `ImprovementNote`,
  modulo `aicraft/backlog.py`, 3 endpoint API, tab UI dedicato con filtro stato. Popolata con
  le voci reali emerse dal test live su 3 caroselli.
- Riscritta l'analisi video per i talking/caption: `claude_creative.write_talking_video_prompt`
  (sostituisce il vecchio placeholder `write_regen_prompt`), dialogo verbatim dalla trascrizione
  Whisper, `generate_audio=true`, `aspect_ratio`/`resolution` fissi (9:16/720p), `duration` reale
  del video originale. Toggle opt-in per `video_references` (mai testato a pagamento, default OFF).
- Repo git inizializzato per la prima volta, primo commit fatto, push su
  `https://github.com/RickyMarto03/AI-CRAFT.git` (branch `main`) verificato allineato.
- 125 test verdi. Doc `docs/ai-craft-architecture.md` aggiornata (§12.15, §12.13, §13).
- Creato questo file (`PROSSIMI_PASSI.md`) come handoff permanente tra sessioni/agenti.
