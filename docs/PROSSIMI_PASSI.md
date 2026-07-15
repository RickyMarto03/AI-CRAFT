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

**Prossimo blocco consigliato: rifiniture operative + qualita' produzione.**
I primi 5 punti richiesti dall'utente sono implementati (caption originale, produzione reale
protetta, sync settimanale, policy per categoria, stati errore reference). Ora i prossimi passi
piu' sensati sono:
- fare un micro job reale Higgsfield controllato da UI con 1 solo contenuto pronto, cosi' si
  valida il nuovo pulsante "Produci davvero" senza usare un piano grande;
- migliorare la schermata "Oggi" con agenda del giorno, stato produzione e prossime azioni;
- aggiungere controlli di dettaglio in Libreria (filtri per categoria/stato, retry singolo,
  apertura cartella locale);
- affinare analisi video talking con piu' frame e timestamp Whisper (vedi sezione sotto).

## Intenzioni discusse in chat, non ancora implementate

- **Precisione dell'analisi video talking (discusso 15/07/2026, subito dopo l'implementazione
  del punto 1).** L'utente ha fatto notare, giustamente, che `frame_picker.sample_frames`
  campiona solo 5 frame equispaziati su un video fino a 15s — troppo rado, produce un'analisi
  approssimativa dei movimenti. Due leve concrete discusse ma non implementate:
  1. Alzare `engine.ANALYSIS_FRAME_COUNT` (oggi 5) a qualcosa tipo 1 frame/secondo — costa solo
     piu' chiamate Read di Claude (incluse nell'abbonamento), non crediti Higgsfield.
  2. Aggiungere timestamp per segmento alla trascrizione Whisper (oggi `ReferenceItem.transcript`
     e' solo testo piatto — `faster-whisper` supporta nativamente i timestamp per segmento, non
     ancora usati) cosi' Claude puo' sapere A CHE SECONDO viene detta ogni frase e correlarla
     al frame giusto, invece di indovinare l'allineamento.
  Nessuna delle due e' stata implementata in questa sessione per limiti di token residui.

## Checklist "cosa manca per essere operativo al 100%" (stato 15/07/2026 sera)

1. [x] Analisi video per i talking/caption (dialogo verbatim, movimenti dai frame, audio) — vedi
       doc §12.15. Aperto solo l'affinamento sopra (frame density/timestamp).
2. [x] Caption originale: `downloader.download_reference` salva `original_caption` su
       `ReferenceItem`; lo stadio caption/hashtag ora la adatta invece di inventare da zero
       quando e' disponibile.
3. [ ] Verifiche con job Higgsfield REALI mai fatte finora (nessuna per limiti di budget/cautela
       dell'utente su spese non pianificate): `video_references` su seedance_2_0 (toggle
       `settings.SEEDANCE_USE_VIDEO_REFERENCE`, default OFF), `generate_audio` end-to-end,
       `image_reference` come URL remoto per `kling3_0_motion_control`, costo reale di
       `kling3_0_motion_control` (oggi solo il dato ~16cr/10s fornito a voce dall'utente).
4. [ ] Fedelta posa/outfit alla foto originale nei caroselli — gia' segnata nella sezione "Da
       migliorare" dell'app, qualita' buona ma migliorabile.
5. [ ] Stato dedicato su `ContentPiece` per i rifiuti di contenuto di Claude (oggi genericamente
       "error") — proposto, mai confermato in scope dall'utente.
6. [x] UI produzione reale + Libreria: bottone "Produci davvero" con conferma esplicita e
       guardia budget; "Aggiorna libreria" usa la policy per categoria.
7. [ ] **Rifiniture operative UI — vedi "Task su cui lavorare adesso" sopra. PROSSIMO TASK.**

## Log sessioni (piu' recente in cima — AGGIUNGERE una voce nuova, non sovrascrivere le altre)

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
