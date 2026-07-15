# AI-craft — Blueprint di Architettura

## 0. Scope

Sistema di produzione contenuti IG (video, caroselli, stories) per un Creator con più Profili, espandibile a più Creator in futuro. Pipeline: reference (da Google Sheet) → download → trascrizione → rigenerazione (Higgsfield) → QA → consegna. Con calendario editoriale, budget/crediti, e gestione multi-profilo fin dall'inizio.

Ispirato agli screenshot condivisi (Piano / Produzione / Creator / Libreria / Costi) ma costruito da zero.

---

## 1. Modello dati (entità core)

```
Creator
  id, nome, created_at

Profile
  id, creator_id (FK), nome (es. "Ruby Wilde"), tipo_contenuto ("solo_talking" | "solo_balletti" | "misto"), attivo

ReferenceItem
  id, source_url (link IG dal Google Sheet), sheet_row_id
  status: "pending" | "downloading" | "downloaded" | "transcribing" | "ready" | "error"
  local_video_path, local_audio_path, frame_paths (json list)
  transcript (text), transcript_status
  content_type_hint ("video" | "carosello")
  imported_at, updated_at

ContentPiece
  id, profile_id (FK), reference_id (FK, nullable se generato senza reference)
  content_type: "video_talking" | "video_balletti" | "video_caption" | "carosello" | "stories"
  plan_week_id (FK), scheduled_day (lun-dom)
  status: pipeline stage corrente (vedi §3)
  generated_assets (json list path)
  caption, hashtags (json list)
  cost_credits_estimated, cost_credits_actual
  created_at, updated_at

PlanWeek
  id, profile_id (FK), week_start, week_end
  status: "bozza" | "approvato"
  version (int, incrementale ad ogni modifica)

CreditLedger
  id, timestamp, delta_credits, motivo, content_piece_id (FK nullable)
  # saldo = somma cumulativa; niente colonna "saldo" salvata, si calcola
```

**Nota SQLite come start**: un file unico, zero setup, sufficiente per volumi da singolo operatore. Migrabile a Postgres se in futuro serve accesso concorrente multi-utente o dashboard web con più sessioni.

---

## 2. Moduli del sistema

| Modulo | Responsabilità |
|---|---|
| **Reference Sync** | Polling Google Sheet → crea/aggiorna `ReferenceItem` → download IG → estrazione audio/frame → trascrizione (Whisper) → marca `ready` |
| **Planning** | Crea/modifica `PlanWeek`, gestisce quote per giorno/tipo, workflow bozza→approvato, versioning |
| **Production Engine** | Per ogni `ContentPiece` approvato, esegue la pipeline a stadi (§3): stadi deterministici via MCP tool, stadi creativi via Claude Code headless |
| **Budget** | Stima costo di un piano prima dell'approvazione, blocca produzione se saldo insufficiente (replica la logica "budget non copre il piano" degli screenshot) |
| **QA** | Controlli automatici post-generazione (durata, risoluzione, file esiste, audio presente) + eventuale review creativa via Claude |
| **Delivery** | Assembla cartella finale / consegna (Dropbox o locale) |
| **Command Center** | Dashboard (fase successiva, non ora) — legge lo stesso DB, nessuna logica duplicata |

---

## 3. Pipeline di produzione (stadi per content_type)

Analoga a "Immagine Soul → Video Kling → QA → Consegna Dropbox" degli screenshot, ma con Higgsfield:

```
video_talking:   reference_ready → image_regen → video_regen → qa → caption_hashtag → delivery
video_balletti:  reference_ready → image_regen → video_regen → qa → caption_hashtag → delivery
carosello:       reference_ready → image_regen (N immagini) → qa → caption_hashtag → delivery
stories:         reference_ready → image_regen → qa → delivery
```

Ogni stadio aggiorna `ContentPiece.status`. Gli stadi **deterministici** (download, QA tecnico, delivery, naming) sono codice puro. Gli stadi **creativi** (scrittura prompt di rigenerazione, caption, hashtag, giudizio QA "ha senso?") passano da `claude -p` con tool MCP.

---

## 4. Moduli tecnici / stack

- **Orchestratore**: Python (allinea bene con Whisper, instaloader/yt-dlp, SQLite)
- **DB**: SQLite (file `aicraft.db`), tramite SQLAlchemy per non doverlo riscrivere se poi si migra
- **MCP server**:
  - `higgsfield_server.py` — image/video regen (già abbozzato)
  - `sheets_server.py` — legge righe nuove dal Google Sheet, aggiorna stato
  - `budget_server.py` — tool per Claude per interrogare saldo/costo prima di procedere
- **Mente creativa**: Claude Code headless (`claude -p`), invocato dal Production Engine per gli stadi creativi, con `--allowedTools` ristretti allo stadio in corso
- **Scheduler**: cron o loop Python con sleep, per il polling periodico del Google Sheet

---

## 5. Ordine di build consigliato

Anche costruendo "il sistema completo", conviene un ordine — non tutto in parallelo:

1. **Schema DB + Reference Sync**: Google Sheet → download → trascrizione → `ready`. Verificabile da solo (equivale alla schermata "Libreria").
2. **Production Engine end-to-end su un solo profilo/tipo**: un `ContentPiece` che attraversa tutti gli stadi fino a `delivery`. Qui si valida l'integrazione Higgsfield + Claude headless.
3. **Budget/CreditLedger**: stima costi, blocco se insufficiente.
4. **Planning/calendario**: bozza→approvato, versioning, quote per giorno.
5. **Multi-profilo**: già supportato dallo schema dal punto 1 — qui si aggiunge solo la UI/logica di selezione profilo attivo.
6. **Command Center** (dashboard): dopo che il motore gira stabile da riga di comando/log.

---

## 6. Cosa NON fare (rischi da evitare)

- Non lasciare che Claude decida naming file, struttura cartelle o schema JSON di output — quello resta fisso nel system prompt (vedi conversazione precedente).
- Non fondere Reference Sync e Production Engine nello stesso processo: se il download fallisce, non deve bloccare la produzione di pezzi già pronti.
- Non calcolare il saldo credito "a occhio" dentro al Production Engine: sempre dal `CreditLedger`, unica fonte di verità, per evitare disallineamenti come quelli mostrati nello screenshot Costi.

---

## 7. Decisioni prese durante l'implementazione dello Step 1 (Reference Sync)

Queste integrano — non sostituiscono — le sezioni precedenti, sulla base di scelte fatte in fase di build:

- **Google Sheet: accesso strettamente read-only.** Mai scrivere sullo sheet (niente aggiornamento di stato lì). Lo stato vive solo in `ReferenceItem.status`. Le colonne "DONE <nome persona>" nello sheet sono checkbox di un altro team, non fanno parte del nostro modello dati e vanno ignorate dal parser.
- **Trascrizione: Whisper locale, non Claude.** I modelli Claude (Sonnet 5 / Opus 4.8, verificato su docs ufficiali luglio 2026) supportano testo e immagini ma non hanno input audio nativo. Whisper (via `faster-whisper`) genera il transcript grezzo in `ReferenceItem.transcript`; Claude headless resta riservato agli stadi creativi a valle (prompt di rigenerazione, caption, hashtag) usando quel transcript come input testuale.
- **Higgsfield: CLI, non MCP.** Il Production Engine è orchestrato in modo deterministico da Python, non è una sessione conversazionale con un agente. L'MCP di Higgsfield è pensato per un agente che sceglie autonomamente modello/parametri in linguaggio naturale dentro una chat — userlo così romperebbe la separazione creativo/deterministico imposta da `CLAUDE.md` e renderebbe più fragile il tracciamento costi via `CreditLedger`. Il wrapper Python (`aicraft/production/higgsfield_client.py`) chiama il CLI ufficiale via subprocess — il binario si chiama **`higgsfield`** (non `hf`: nome corretto dopo aver letto il README ufficiale del repo, `npm install -g @higgsfield/cli` poi `higgsfield auth login`), non un collegamento MCP nella sessione Claude.
- **Download IG: Instaloader, non yt-dlp.** Serve gestire sia `video` (reel) sia `carosello` (post multi-immagine "sidecar"); Instaloader li gestisce entrambi nativamente con metadati strutturati, a differenza di yt-dlp che è video-centrico. **Autenticazione via cookie del browser locale** (Chrome di default, `browser_cookie3`), non username/password in codice: si riusa la sessione già loggata a mano su instagram.com, che evita i checkpoint/verifiche 2FA che il login diretto spesso innesca. Sessione salvata su disco dopo il primo import, rate-limiting conservativo tra un download e l'altro.
- **Google Sheet: serve un service account vero, i cookie non bastano.** Verificato provando l'export CSV pubblico dello sheet senza credenziali: risponde `401`, è privato. A differenza di Instaloader (che riusa cookie di sessione per "impersonare" il browser sullo stesso protocollo), l'API Google Sheets richiede token OAuth/service-account: non è aggirabile con un cookie copiato in modo pulito. Setup: service account su Google Cloud con scope `spreadsheets.readonly`, sheet condiviso in sola lettura con la sua email.
- **Campo aggiuntivo `ReferenceItem.source_category` (non nel blueprint originale §1).** Lo sheet marca ogni link con una categoria/tag di contenuto (es. `BOOBS`/`BOOTY`/`GENERAL` nel tab CAROSELLI, `OTHER CONTENTS`/`BALLETTI/LIPSYNC`/`TALKING` nel tab VIRAL GENERAL). Non c'era una colonna per questo nello schema originale: per non perdere informazione utile a valle (Planning, assegnazione a Profile) è stata aggiunta `source_category` (testo libero) insieme a `source_tab` (nome del tab di provenienza). Estensione additiva, non rompe nulla di esistente — segnalata qui perché non esplicitamente concordata prima del build.
- **Parsing dello sheet è label-driven, non a colonne fisse.** I due tab noti (`CAROSELLI`, `VIRAL GENERAL`) hanno layout diversi tra loro (banner di settimana su riga propria vs data annegata in una cella di intestazione categoria). Il parser (`aicraft/reference_sync/sheets_reader.py`) riconosce righe di intestazione categoria/data per contenuto della cella, non per lettera di colonna — tollera piccoli spostamenti di colonna senza rompersi, com'è stato indicato essere plausibile ("non dovrebbe variare molto, se lo farà avviso").
- **Download IG: RISOLTO con instagrapi (14/07/2026), dopo un blocco temporaneo.** _Contesto:_ Instagram aveva inasprito il blocco anti-scraping sulle **query GraphQL del sito web** che usano Instaloader/yt-dlp/gallery-dl. Testato su 15 link reali dello sheet (inclusi 5 confermati live): Instaloader 0/15, yt-dlp 2/15 ("Instagram sent an empty media response"), gallery-dl 0/15 (redirect a login). Non era un bug nostro (documentato upstream, es. [instaloader/instaloader#2682](https://github.com/instaloader/instaloader/issues/2682)). _Soluzione:_ **instagrapi**, che colpisce l'**API "mobile"** (quella dell'app) invece del GraphQL web — endpoint diversi, non bloccati. Verificato: 5/5 su link reali (dove gli altri 3 facevano 0-2/15), con download reali sia di reel/video sia di caroselli multi-immagine sia di caroselli misti video+foto. `aicraft/reference_sync/downloader.py` riscritto su instagrapi, stessa interfaccia (`download_reference` → `DownloadResult`), quindi il resto della pipeline non cambia. Autenticazione invariata: cookie `sessionid` dal browser (`login_by_sessionid`), nessuna password in codice. **Playwright (browser reale) resta l'alternativa di riserva** se un domani anche l'API mobile venisse bloccata — non serve ora perché instagrapi funziona pulito.
- **Le date delle settimane nello sheet mischiano italiano e inglese.** Scoperto testando contro lo sheet reale: la prima settimana di `VIRAL GENERAL` è `"15-21 GIUGNO"`, settimane successive usano l'inglese (`"20-26th JULY"`); `CAROSELLI` usa `"18-24 may"`. Il parser riconosce entrambe le lingue (nome per esteso o abbreviato) tramite una tabella di lookup in `sheets_reader.py`, non `strptime` legato a una locale. Verificato: tutte le 1018 reference dello sheet reale (499 CAROSELLI + 519 VIRAL GENERAL) si parsano con settimana assegnata correttamente, zero orfane.

---

## 8. Step 2 (Production Engine) — stato dell'implementazione

Modulo `aicraft/production/`. Aggiornamento del 14/07/2026: le due integrazioni esterne (Higgsfield, Claude headless) sono state **verificate per davvero** contro servizi reali, non solo scritte da documentazione. Dettagli sotto.

**Verificato con test reali (non mock):**
- `naming.py` — convenzione di cartelle/file FISSA (regola di progetto, non improvvisata a runtime):
  ```
  data/delivery/{profile-slug}/{content_type-slug}/{week_start}_{scheduled_day}_{content_piece_id}/
      asset_01.<ext>, asset_02.<ext>, ...
      caption.txt
      meta.json   # {content_piece_id, content_type, hashtags, cost_credits_actual, reference_id}
  ```
- `qa.py` — controlli tecnici via `ffprobe` (file esiste, durata minima, traccia audio presente, risoluzione minima). Testato con video/immagini veri generati al volo con `ffmpeg`.
- `engine.py` / `delivery.py` — orchestrazione stadi, `CreditLedger`, gestione errori per-pezzo, assemblaggio cartella finale. Testato end-to-end con `higgsfield_client`/`claude_creative` mockati per lo *stage flow*; le due integrazioni sotto sono verificate separatamente contro i servizi veri.
- **`higgsfield_client.py` — verificato contro l'account reale** (`trinityaigencyllc@gmail.com`, piano Ultra, 651 crediti, già autenticato in locale da uso precedente, nessun nuovo login servito). Corretti diversi dettagli sbagliati nella prima stesura (basata solo su doc pubbliche):
  - il binario si chiama `higgsfield`, non `hf` (già corretto in §7);
  - `text2image_soul_v2` non accetta `--soul-id`: i parametri veri sono `prompt` (obbligatorio), `aspect_ratio`, `quality`, `image_references` (via `--image-references`/`--image`) — verificabili con `higgsfield model get <job_type>`;
  - `kling3_0` usa `--start-image`/`--end-image`, non `--image`, per l'immagine sorgente;
  - `generate create --wait --json` risponde con una **lista** di job (anche per una singola generazione), non un oggetto singolo — `_run_json` ora normalizza; campi reali: `id`, `status`, `result_url`, `params`, nessun campo di costo;
  - il costo NON è nella risposta di `generate create`/`get`: va richiesto a parte con `generate cost <job_type> --prompt ...` PRIMA di lanciare il job (`estimate_cost` in `higgsfield_client.py`, usato da `engine.py` per popolare `CreditLedger`).
  - Generazione reale di test eseguita: un'immagine Soul V2 (0.12 crediti) tramite sia il CLI grezzo sia il wrapper Python — entrambi funzionanti. Il ramo video (`kling3_0`) è verificato solo per parametri accettati e costo stimato (10 crediti/5s) via `model get`/`generate cost`, non con una generazione reale (costo/tempo non giustificati, lo schema di risposta è già confermato dal ramo immagine).
- **`claude_creative.py` — verificato per davvero.** Il binario `claude` non era installato come CLI standalone su questa macchina (l'utente lo usa via estensione VS Code); installato con `npm install -g @anthropic-ai/claude-code` su prefix utente (`~/.npm-global`, niente sudo — il prefix npm di default puntava a una cartella di root non scrivibile). **Serve aggiungere `~/.npm-global/bin` al PATH della shell dell'utente** (es. in `~/.zshrc`) perché il Production Engine possa invocare `claude` da script eseguiti in un terminale normale. Verificato: `claude -p "..." --output-format json` risponde `{"result": "...", ...}` esattamente come atteso; `write_caption_and_hashtags()` testato per davvero con un transcript finto, output valido nello schema fisso `{"caption": "...", "hashtags": [...]}`.
- Schema fisso per lo stadio `caption_hashtag`: `{"caption": "...", "hashtags": ["#tag1", "#tag2"]}` — imposto via prompt, il parsing lo valida e solleva errore esplicito se Claude risponde in un formato diverso.

**Semplificazioni consapevoli, non richieste esplicitamente:**
- `carosello` genera **un'immagine per chiamata** (`GenerationOp.count=1` in `pipeline_spec.py`), non ancora le N immagini multiple previste da §3 ("image_regen (N immagini)"). Non c'era ancora un numero N concordato né dati reali di reference (frame_paths) su cui basare la scelta. Il meccanismo per N>1 c'è già (engine cicla su `count`, budget moltiplica per `count`): quando N sarà noto, si cambia SOLO il `count` in `pipeline_spec.py` e sia produzione sia stima si adeguano insieme.
- `ContentPiece.content_type == "video_caption"` (presente nell'enum di §1 ma senza una riga propria in §3) è trattato con la stessa pipeline di `video_talking` per default, in attesa di una pipeline dedicata se serve differenziarla.
- Il Production Engine dipende dallo stadio `reference_ready`, che a sua volta dipende dal download IG — attualmente bloccato (vedi nota sopra). Il motore è stato quindi validato con dati di test, non con un giro reale end-to-end su una reference vera scaricata dallo sheet.

---

## 9. Step 3 (Budget/CreditLedger) + Step 4 (Planning) — stato dell'implementazione

Costruiti insieme perché strettamente accoppiati: l'approvazione di un piano è il punto in cui il budget blocca la produzione se il saldo non copre. Moduli `aicraft/budget/` e `aicraft/planning/`. Tutto verificato con test reali (37 test totali verdi) senza dipendere da credenziali (la stima costi ha `cost_fn` iniettabile); in più la stima è stata verificata anche contro l'API Higgsfield reale.

**Budget (`aicraft/budget/`):**
- `ledger.py` — **unica fonte di verità per il saldo** (regola ferma CLAUDE.md). `current_balance()` = somma cumulativa dei `delta_credits`, nessuna colonna "saldo" salvata. Ogni scrittura sul ledger passa da qui (`record_consumption` con delta negativo, `record_topup` con delta positivo): l'engine ora scrive i consumi tramite `budget.ledger`, non più creando `CreditLedger` a mano.
- `estimate.py` — stima il costo in crediti di un ContentPiece / PlanWeek **prima** di produrre. Il costo Higgsfield dipende da modello+parametri, non dal prompt: usa un prompt segnaposto e mette in cache il costo per ogni `(job_type, params)` distinto (stimare un piano di N pezzi identici fa 1 sola coppia di chiamate, non N). `cost_fn` iniettabile (default = Higgsfield reale). Verificato contro l'API reale: `video_talking`→10.12, `carosello`/`stories`→0.12 crediti, coerenti coi costi CLI misurati.
- `pipeline_spec.py` (in `production/`) — **fonte unica** di quali generazioni (modello+params+count) compone ogni `content_type`. Sia l'engine (che genera) sia il budget (che stima) leggono da qui, così costo stimato e spesa reale non divergono — esattamente il disallineamento che il blueprint vieta per i crediti (screenshot Costi).

**Planning (`aicraft/planning/`):**
- `plan.py` — `create_plan_week` (nasce `bozza`, `version=1`), `add_content_piece`/`remove_content_piece`/`reschedule_content_piece` (con quote e versioning), `approve_plan`.
- **Versioning**: ogni modifica al contenuto del piano fa `version += 1`.
- **Decisione presa in build**: una modifica a un piano già `approvato` lo **riporta a `bozza`**. Motivo: un piano approvato è stato coperto a budget in quel momento; se poi guadagna pezzi silenziosamente, la copertura non è più garantita. Richiedere una nuova approvazione forza un nuovo controllo di budget. Non era esplicitamente richiesto — segnalato qui.
- `quota.py` — quote per giorno e per tipo/settimana. **I limiti non erano nel blueprint** ("gestisce quote per giorno/tipo" senza numeri): `QuotaPolicy` è quindi parametrica, default permissivo (nessun limite) finché l'utente non fornisce i numeri reali.

**Integrazione Budget↔Planning (il cuore dei due step):**
- `approve_plan()` stima il costo del piano (`budget.estimate`), lo confronta col saldo (`budget.ledger.current_balance`) e **blocca con `BudgetInsufficientError` se saldo < stima** — replica "budget non copre il piano". La stima viene salvata su `ContentPiece.cost_credits_estimated`. Il piano resta `bozza` se l'approvazione fallisce.

**Integrazione Planning↔Production:**
- `engine.run_once()` ora produce **solo i ContentPiece di piani `approvato`** (join su `PlanWeek`): un piano in bozza, o un pezzo senza piano, non entra in produzione. Testato.

**Nota su saldo interno vs saldo Higgsfield reale:** il `CreditLedger` è il registro *interno* (unica fonte di verità per la nostra contabilità, come da regola ferma). Il saldo reale su Higgsfield si allinea con `budget/sync.py` — vedi §10.

---

## 10. Step 5 (Multi-profilo) + Step 6 (Command Center) + chiusura punto aperto saldo

Ultimo blocco costruito in un colpo: tutto ciò che restava fattibile senza il download IG. Tutto verificato — 51 test verdi + smoke test CLI reale end-to-end contro Higgsfield.

**Step 5 — Multi-profilo (`aicraft/profiles/manager.py`):**
- Lo schema supportava già il multi-profilo (Creator 1‑N Profile) dal punto 1; qui si aggiunge la logica di gestione (CRUD creator/profili, abilita/disabilita) e di **selezione del profilo attivo**.
- Distinzione tenuta esplicita: `Profile.attivo` (bool) = profilo *abilitato*; "profilo attivo selezionato" = quale profilo è quello corrente per i comandi che non lo indicano, memorizzato in `AppState` (uno solo alla volta). Sono cose diverse — un profilo può essere selezionato anche se disabilitato, e viceversa.
- **Nuova tabella `AppState`** (key/value) — non nel blueprint originale §1: aggiunta per memorizzare stato livello-operatore (per ora solo `active_profile_id`). Estensione additiva, segnalata.

**Chiusura punto aperto Step 3 — sync saldo (`aicraft/budget/sync.py`):**
- Nella sessione precedente avevo lasciato fuori la sincronizzazione col saldo reale Higgsfield per non allargare lo scope. Ora fatta: `sync_from_higgsfield()` legge il saldo reale (`higgsfield account status`) e, se diverge dall'interno, registra **una voce di rettifica** sul ledger (non sovrascrive: la storia dei movimenti resta tracciata). Verificato contro l'account reale (tirato 651.01 crediti).
- Aggiunto `higgsfield_client.account_status()` (schema `{credits, email, subscription_plan_type}` verificato).

**Step 6 — Command Center, base (`aicraft/reporting.py`):**
- Il blueprint vuole la dashboard *dopo* che il motore gira stabile da CLI/log, e "legge lo stesso DB, nessuna logica duplicata". Questo modulo è quella base: `overview()` aggrega lo stato (saldo via `budget.ledger`, profili, reference/piani/content per stato) in sola lettura, senza logica di dominio nuova. `format_overview()` lo rende testo leggibile.
- **Una eventuale UI web resta l'unico pezzo deferito** (come da blueprint): consumerà queste stesse funzioni di reporting.

**CLI operativa unificata (`aicraft/cli.py`):**
- Entrypoint unico che orchestra tutti i moduli, senza logica di dominio propria: `status`, `profiles list/add-creator/add/use`, `budget balance/topup/sync`, `plan create/add/show/approve`, `references sync`, `produce`.
- È l'interfaccia da "riga di comando/log" che il blueprint indica come precondizione alla dashboard. Smoke test reale eseguito: creazione creator/profilo → `budget sync` (651.01 dal reale) → piano con 2 pezzi → `approve` (stima reale 10.24 crediti, budget check superato) → `status`. Tutto funzionante end-to-end.

**Stato complessivo del progetto dopo questo blocco:** Step 1‑6 implementati e verificati end-to-end contro servizi reali: sheet reader (1018 reference), download IG via instagrapi (5/5, video+caroselli reali), trascrizione Whisper (verificata su video reale, gestione video muti), Higgsfield (generazione+costo+saldo), Claude headless (caption/hashtag), budget, planning, multi-profilo, reporting, CLI. **Nessun blocco esterno residuo.**

---

## 11. App desktop (PyWebView) — la "faccia" del sistema

Modulo `aicraft/desktop/`. Scelta tecnica concordata con l'utente: **PyWebView** (finestra nativa macOS via backend Cocoa, UI in HTML/CSS/JS, backend Python chiamato direttamente — nessun processo separato né IPC come servirebbe con Electron). Ispirata negli screenshot condivisi dall'utente ("Centro di Comando") ma non copiata; palette dark con **verde=positivo, rosso=negativo, blu=accento secondario** (preferenza dell'utente).

**Architettura:**
- `desktop/api.py` — **bridge Python↔JS**. Classe `Api` con metodi invocabili da JS via `window.pywebview.api.<metodo>()`. Nessuna logica di dominio nuova: orchestra soltanto i moduli esistenti (reporting, profiles, budget, planning, reference_sync). Ogni metodo apre/chiude la propria sessione DB, committa, e cattura le eccezioni ritornando `{ok: False, error}` — così il frontend riceve sempre una risposta pulita (mai un'eccezione JS). `BudgetInsufficientError` è mappato in `{ok:False, kind:"budget", needed, available}` per il messaggio "budget non copre il piano".
- `desktop/app.py` — launcher della finestra (`python -m aicraft.desktop.app`).
- `desktop/web/` — frontend statico (nessuna dipendenza esterna, nessuna richiesta di rete dal browser): `index.html` (shell + sidebar), `style.css` (tema), `app.js` (SPA vanilla JS con router, viste, delegazione eventi).

**Schermate (mappano 1:1 sui moduli backend):** Oggi (dashboard, `reporting.overview`), Piano (calendario editoriale con stepper +/- per tipo×giorno, versione, Bozza→Approva → `planning`), Produzione (anteprima **senza costi** dei pezzi pronti + stima → `production_preview`, dry-run: non genera nulla, non spende), Creator (profili + creazione + profilo attivo → `profiles`), Libreria (stato reference → `reference_sync`), Costi (saldo, copertura piano, ricarica, sync Higgsfield → `budget`), Sistema (overview grezza).

**Sicurezza crediti:** l'app NON espone la produzione reale (che spenderebbe crediti Higgsfield) — solo l'anteprima dry-run, come il "Avvia una prova senza costi" degli screenshot. La generazione vera resta un'azione esplicita separata (engine), da collegare con una conferma guardata quando si vorrà.

**Nuova funzione backend `planning.set_cell_count`** — logica dietro gli stepper del calendario: porta il numero di pezzi (tipo, giorno) a un target aggiungendo/rimuovendo, senza toccare pezzi già in produzione. Additiva, testata.

**Verifica:** 67 test verdi (inclusi 12 sull'API bridge, senza GUI né rete). Smoke test end-to-end reale attraverso l'API: creazione profilo → `budget_sync` (651 CR reali da Higgsfield) → piano con 4 contenuti → stima costo reale (10.48 CR) → approvazione → 4 pezzi pronti. La finestra GUI va lanciata dall'utente (`python -m aicraft.desktop.app`, o doppio click su `avvia.command` in cima al progetto); non è avviabile in ambiente headless.

**Layout deliberatamente diverso dagli screenshot di ispirazione dell'utente**, non solo ricolorato — l'utente ha chiesto esplicitamente di non sembrare copiata:
- **Barra di navigazione orizzontale in alto invece della sidebar verticale**: cambia la struttura visibile a colpo d'occhio, non solo lo stile. Profilo attivo e saldo sempre visibili in cima, non nascosti dentro le singole pagine.
- **Calendario del Piano ribaltato**: invece della griglia tipo-riga/giorno-colonna (quella dei riferimenti), una fila orizzontale di **card per giorno**, ognuna con dentro i tipi di contenuto e i relativi +/-. Stesso dato (`grid[content_type][giorno]`), organizzazione opposta — il giorno è l'unità primaria, non il tipo.
- Il "rail" laterale con le statistiche è diventato una striscia di chip orizzontali in cima alla pagina (`chip-strip`), niente colonna fissa a destra.
- Tile/card con **bordo di accento a sinistra** invece di sfondo tinto pieno (più sobrio); numeri in **monospace** (font `--mono`) per un feel da "readout", meno dashboard SaaS generica.
- Palette confermata dall'utente: verde=positivo, rosso=negativo, **blu come accento secondario** (selettore profilo, badge informativi, bottoni "blue").

**Eliminazione profilo** (`profiles.manager.delete_profile`): aggiunta su richiesta dell'utente dopo aver creato profili di test da rimuovere. Per sicurezza rifiuta se il profilo ha piani/contenuti collegati, a meno di `force=True` — in quel caso cancella a cascata piani e content piece del profilo (le voci di `CreditLedger` collegate si scollegano, `content_piece_id` è nullable: la storia dei movimenti resta). Esposta sia in CLI (`aicraft.cli profiles delete <id> [--force]`) sia nell'app desktop (pulsante "Elimina" nella schermata Creator, con conferma).

**`avvia.command`** in cima al repo: script doppio-click per macOS che crea l'ambiente virtuale al primo avvio (se manca), lo attiva, lancia l'app, e tiene aperto il terminale a fine esecuzione così un eventuale errore resta leggibile invece di sparire con la finestra.

---

## 12. Workflow di generazione reale (Ruby2) — in costruzione

Fin qui il Production Engine usava una pipeline **generica/segnaposto** (prompt scritti da Claude da zero, modelli Higgsfield scelti a caso da chi scrive questo codice). Questa sezione documenta il workflow **vero**, definito dall'utente, e lo stato reale dell'implementazione — non tutto è ancora cablato nell'engine, vedi "Cosa manca" in fondo.

### 12.1 Il workflow (definito dall'utente, verbatim dove serve precisione)

**Caroselli**: dal link, si selezionano al massimo 3 foto — tutte se il carosello ne ha ≤3, altrimenti quella su cui atterra il link (`img_index`, 1-based su IG) + precedente e successiva, o due precedenti/due successive se si è a un bordo del carosello. Per ogni foto selezionata, Claude scrive un prompt di ricostruzione **ultra-dettagliato (2200-2400 caratteri)**: outfit, posa, background — mantenendo outfit/background coerenti tra le foto dello stesso carosello. Il prompt include SEMPRE le caratteristiche fisiche di Ruby2 + "very big natural breast, slim waist, no tattoos, no overlay text, no watermark". Generazione con `text2image_soul_v2` + `custom_reference_id` = Ruby2 — **solo testo**, nessuna immagine passata al modello (niente `image_references`).

**Balletti**: dal video originale si estrae il primo frame in cui è visibile la ragazza (vedi §12.3 — non necessariamente t=0, non necessariamente figura intera). Da quel frame, stessa procedura foto sopra per ottenere la "foto Ruby2". Poi video originale + foto Ruby2 → workflow `kling3_0_motion_control` (`video_references` + `image_references`), durata = durata del video originale, 9:16 720p.

**Talking**: stessa foto Ruby2 dal primo frame utile. Poi analisi precisa del video originale (dialogo, movimenti, background, outfit, tempo) → prompt dettagliato per `seedance_2_0`, con la foto Ruby2 come `start_image`. Implementato, vedi §12.15.

**Caption (content_type "video_caption")**: non ancora definito, deferito dall'utente.

**Caption/hashtag testo**: NON generata da zero da Claude — si copia/adatta la caption del video/post originale. Serve quindi catturare la caption sorgente durante il download (instagrapi la espone in `media_info`), non ancora fatto — vedi "Cosa manca".

**Soul per creator, non per profilo**: tutti i Profile di una stessa Creator condividono lo stesso Soul. Oggi esiste solo la creator "Ruby" con Soul "Ruby2".

### 12.2 Scoperte tecniche reali (verificate contro l'account Higgsfield, nessuna generazione a pagamento oltre quelle già note)

- **Soul esistenti sull'account**: `Ruby2` (id `0698f81f-1d26-47bb-b31b-9391aeadb144`, quello in uso), `Ruby`, `Sol2`, `Sol` — tutti tipo `soul_2`, stato `completed`. Lista/dettaglio via `higgsfield soul-id list|get`.
- **`text2image_soul_v2` non ha un parametro `--soul-id`** (correzione a quanto scritto in §8): il parametro giusto per il personaggio è **`custom_reference_id`**.
- **`seedance_2_0` è il modello giusto per i talking video**, non `kling3_0` che avevo messo di default in `pipeline_spec.py` — costo reale **22.5 crediti** per 5s (contro i 10 di Kling), va corretto `pipeline_spec.py`. `seedance_2_0` supporta `generate_audio` (voce) e `video_references` (fino a 3), oltre a `start_image`/`image_references`.
- **`kling3_0_motion_control` — incongruenza RISOLTA (15/07/2026), con una generazione reale di test.** Non è un modello "semplice" (non compare in `higgsfield model list`) né va creato via `higgsfield generate workflow <nome>` (che supporta solo `draw_to_video`/`reframe`/`voice_change`/`dubbing`): si invoca come qualunque altro job_type, con **`higgsfield generate create kling3_0_motion_control --image-references <foto> --video-references <video> --background_source input_video --mode std --wait`**. Confermato con un job reale (id `8ddb6b61-...`):
  - **la durata si auto-deriva dal video passato** (non è un parametro impostabile — `higgsfield model get` non la elenca perché non è un input, e infatti provare a passarla con `--duration` dà "Unknown params: duration"; la stima costi (`generate cost`) invece la richiede e fallisce con "Field required" — è un bug del solo endpoint di stima, non della generazione vera, verificato tentando piu' combinazioni di flag);
  - **9:16 720p sono automatici** (`width: 720, height: 1280` nell'output del job), non servono parametri per impostarli;
  - **la stima costi è rotta per questo job_type specifico** — non è possibile sapere il costo prima di lanciare una generazione reale (per tutti gli altri modelli testati la stima ha sempre funzionato).
  - **Il job di test è stato bloccato dal filtro di moderazione con `status: "nsfw"`** — probabilmente per la foto di riferimento Ruby2 in bikini usata come `image_references`, o per il contenuto del video sorgente, o per la combinazione. **Zero crediti addebitati** per un job bloccato in moderazione (saldo verificato invariato prima/dopo: 651.01). Questo è un rischio concreto e potenzialmente sistemico per lo stadio balletti, dato che gran parte del contenuto Ruby2 è in bikini/rivelatore per specifica dell'utente — **da investigare ulteriormente** (provare con foto di riferimento meno esplicite per isolare la causa, o accettare che una parte dei balletti finirà bloccata e vada gestita come caso d'errore nel Production Engine).

### 12.3 `frame_picker.py` — trovare il primo frame utile di un video

Stadio deterministico (no Claude): trova il primo frame in cui è riconoscibile la ragazza, gestendo anche inquadrature parziali o "di spalle all'inizio" (richiesto esplicitamente dall'utente).

**Storia del tuning (perché conta)**: il primo tentativo usava i classici Haar Cascade di OpenCV (frontale + profilo). Testato su contenuto IG reale: un frame con la ragazza ripresa **di spalle** veniva segnalato come "volto frontale" — falso positivo su texture di capelli. Ho provato a correggere con una verifica incrociata (rilevatore di occhi nel riquadro del volto), ma tarare la sensibilità per escludere quel falso positivo faceva perdere anche volti veri: i due errori non si bilanciavano con nessuna soglia provata.

Sostituito con un **rilevatore DNN** (SSD ResNet10 su Caffe, pesi standard di OpenCV, ~10MB, locale): sugli stessi due casi reali classifica correttamente entrambi. Ma anche qui, testando su una finestra più ampia dello stesso video "di spalle", è emerso un secondo problema: un **singolo frame isolato** con motion-blur (capelli in movimento) generava un falso positivo con confidenza altissima (0.955) — mentre il volto vero, quando appariva, restava rilevato su **più frame consecutivi di fila** (5+ campionamenti sopra soglia). Fix: si richiedono **2 rilevamenti consecutivi** prima di accettare un volto come valido, uno isolato non basta.

Livelli finali, in ordine: (1) volto — DNN, richiede 2 hit consecutivi; (2) persona generica — HOG people detector di OpenCV, copre il caso genuinamente di spalle per tutta la finestra di scan; (3) fallback fisso (primo frame) se niente trovato. **Importante**: si scandisce SEMPRE l'intera finestra (default 6s, configurabile) cercando un volto anche se una "persona" viene trovata prima — un volto trovato dopo vale sempre più di una persona trovata subito, altrimenti un hit "person" precoce bloccherebbe la ricerca di un volto vero un istante più avanti.

I pesi del modello DNN e (residualmente, non più usati da questo modulo) alcuni Haar cascade sono scaricati dal repo ufficiale OpenCV e versionati in `aicraft/production/dnn_models/`: **opencv-python 5.x non include più i Haar Cascade nel pacchetto** (verificato 15/07/2026) — da qui la necessità di versionarli invece di fare affidamento sul path interno del pacchetto installato. Pinnato `opencv-python==4.13.0.92` (la 5.0 installata di default mancava perfino di `cv2.CascadeClassifier`).

Test in `tests/test_frame_picker.py`: la logica di scelta (priorità, conferma a 2 hit, fallback) è testata mockando i rilevatori — l'accuratezza reale del modello è stata validata a mano contro contenuto IG reale durante lo sviluppo (non riproducibile in CI senza asset con volti reali).

### 12.4 `carousel_selection.py` — quali foto ricreare da un carosello

Implementa la regola in §12.1: `parse_img_index` legge il parametro `img_index` (1-based su IG, convertito a 0-based) dall'URL, default alla prima immagine se assente; `select_carousel_indices` calcola la finestra di foto da selezionare, clampata ai bordi del carosello. Testato (12 casi, inclusi i bordi).

### 12.5 `character.py` — definizione fissa del personaggio Ruby2

Un Soul per creator (§12.1), tenuto come **costante di codice** (`CHARACTERS_BY_CREATOR`) e non come colonna nel DB: non abbiamo ancora un sistema di migrazioni (nessun Alembic) e oggi esiste una sola creator reale. Se arriva una seconda creator con un suo Soul, va promosso a colonna vera su `Creator` con una migrazione — segnalato come scelta di scope, non dimenticanza.

`physical_description` **fissata il 15/07/2026** analizzando 4 foto di riferimento fornite dall'utente in `data/character_refs/ruby2/` (coerenti tra loro) — mai generata al volo dentro un prompt, per garantire coerenza tra tutte le generazioni.

### 12.6 Wiring in `pipeline_spec.py`/`engine.py`/`higgsfield_client.py` — FATTO (15/07/2026)

- **`pipeline_spec.py` corretto**: `video_talking`/`video_caption` usano `seedance_2_0` (non più `kling3_0`); `video_balletti` usa `kling3_0_motion_control` con `manual_cost_estimate=16.0` — **dato reale fornito dall'utente** (~16 crediti per una clip di ~10s, da uso diretto della piattaforma), non ancora verificato con un job nostro completato con successo (il test reale è stato bloccato da moderazione content prima di generare — il che conferma comunque che il job era arrivato al punto di essere effettivamente generabile via questo workflow, solo bloccato dal filtro finale). Presumibilmente scala con la durata del video originale: per clip molto più lunghe/corte il valore va rivisto.
- **`higgsfield_client.generate_motion_control()`** — nuova funzione dedicata (convenzione di chiamata diversa da `generate_video`: `image_references`/`video_references`, niente prompt/duration). Solleva **`HiggsfieldNSFWBlockedError`** (sottoclasse di `HiggsfieldError`) quando il job viene bloccato in moderazione, riconosciuta cercando "nsfw" nel messaggio d'errore del CLI.
- **`engine._stage_video_regen`** ora fa branch su `job_type`: per `kling3_0_motion_control` salta la scrittura del prompt (non serve), passa il video ORIGINALE (`reference.local_video_path`) come `video_reference` e la foto Ruby2 appena generata come `image_reference`. **Punto non verificato**: passa `result.result_url` (URL remoto sulla CDN Higgsfield) come `image_reference` — il CLI documenta "UUID (upload id o job id) o local file path", non un URL esterno generico; da confermare al prossimo giro reale, il fix se non funziona è propagare `result.job_id` invece dell'URL.
- **`engine.process_content_piece`** gestisce `HiggsfieldNSFWBlockedError` con uno stato dedicato **`blocked_nsfw`** (non "error" generico) — esito legittimo e non recuperabile con retry sullo stesso input.
- Test aggiunti (mockati, nessuna spesa): `test_video_balletti_usa_motion_control_con_video_originale`, `test_video_balletti_bloccato_nsfw_marca_stato_dedicato`.

### 12.8 `claude_creative.write_carousel_prompts` — FATTO e verificato con foto reali (15/07/2026)

Design concordato con l'utente: **ibrido**, non un template statico che Claude si limita a copiare, e non lasciato interamente alla sua discrezione.

- **Fisso in codice, mai lasciato a Claude**: `character.physical_description` + `mandatory_additions` + `negative_prompt` (concatenati in `_assemble_full_prompt`, testo verbatim, mai parafrasato).
- **Scritto davvero da Claude**: la descrizione di outfit/posa/background, guardando le foto reali (via `--allowedTools Read`, non una descrizione testuale mia). `_scene_target_range` calcola quanto spazio serve per questa parte in modo che il prompt FINALE (fisso + scena + fisso) rientri nel target 2200-2400 concordato con l'utente.
- **Una sola chiamata con tutte le foto del set** (2-3, gia' selezionate da `carousel_selection.py`): Claude le confronta direttamente e mantiene outfit/background coerenti tra loro, variando la posa dove serve — deciso con l'utente, le pose cambiano quasi sempre da una foto all'altra dello stesso carosello.
- **Retry automatico su lunghezza fuori target** (fino a 2 tentativi extra, poi si arrende e logga un warning invece di fallire): deciso con l'utente.

**Verificato con una chiamata reale** (non solo mockata) su un carosello scaricato per davvero dallo sheet (8 foto, selezionate le 3 giuste da `carousel_selection.py`): tutte e 3 le descrizioni entro il target al **primo tentativo** (2236, 2240, 2205 caratteri), outfit/background coerenti tra le prime due foto (stesso top, stessa gonna, stesso tavolino), la terza riconosciuta correttamente da Claude come uno scatto di dettaglio senza la persona (vetrina di un negozio) e descritta di conseguenza invece di inventare una posa inesistente.

**Decisione (utente, 15/07/2026)**: `carousel_selection.py` seleziona le foto per vicinanza all'`img_index` senza verificare se la persona è effettivamente inquadrata — può capitare (come nel test reale) che una delle 2-3 foto sia un dettaglio/ambientazione senza soggetto. L'utente ha scelto di **generarla comunque così com'è** (nessun filtro aggiuntivo): comportamento già quello attuale, nessuna modifica al codice necessaria.

**Due bug reali trovati e corretti girando il test più volte** (non emersi dai test mockati, solo da chiamate vere):
1. **Claude tentava di usare uno strumento non autorizzato** (verosimilmente bash, per contare i caratteri) e restava bloccato in attesa di un'approvazione che in modalità headless non arriva mai, ritornando testo tipo "The command needs your approval..." invece del JSON. Fix: istruzione esplicita nel prompt di non usare comandi/strumenti per contare, stimare la lunghezza "a mente".
2. **Claude a volte avvolge il JSON in un blocco markdown** (` ```json ... ``` `) nonostante l'istruzione esplicita di non farlo. Fix: `_strip_markdown_fence()` applicato prima di ogni `json.loads`, sia qui sia in `write_caption_and_hashtags`.

Dopo entrambi i fix, verificato su un nuovo giro reale completo: 3/3 descrizioni entro target al primo tentativo (2256, 2255, 2284 caratteri).

### 12.10 `_stage_image_regen` collegato al flusso reale — FATTO (15/07/2026)

`engine._stage_image_regen` non usa più il placeholder generico per l'immagine: ora risolve il personaggio dalla creator del profilo (`character.get_character_for_creator(profile.creator.nome)`, errore esplicito se la creator non ha un Soul configurato), seleziona le foto sorgente con `_select_source_photos`, e chiama `claude_creative.write_carousel_prompts` per ottenere un prompt per foto — poi genera un'immagine Higgsfield per prompt con `custom_reference_id=character.soul_id` (nuovo parametro aggiunto a `higgsfield_client.generate_image`).

`_select_source_photos` fa da ponte tra i due casi del workflow (§12.1), stessa funzione per tutti i content_type:
- **carosello/stories**: foto da `reference.frame_paths` (già scaricate), selezione via `carousel_selection.select_carousel_photos` (fino a 3).
- **video_talking/balletti/caption**: un frame estratto da `reference.local_video_path` via `frame_picker.pick_reference_frame` (salvato accanto al video originale, suffisso `_character_frame.jpg`), poi trattato come una lista di 1 sola foto — stessa identica funzione di prompt-writing, N=1 invece di N=2-3.

Il numero di immagini generate per un carosello ora è **dinamico** (`len(prompts)`, dipende da quante foto seleziona `carousel_selection.py`, fino a 3), non più il `count` fisso di `pipeline_spec.py` — quel campo resta solo come approssimazione conservativa per la stima di budget PRIMA che la reference sia scaricata (quando non si sa ancora quante foto ci saranno), gap già noto e documentato in `pipeline_spec.py`.

Test aggiunti/aggiornati (mockati: `frame_picker`, `claude_creative.write_carousel_prompts`, nessuna chiamata reale nei test): `test_carosello_usa_carousel_selection_e_genera_una_foto_per_prompt` (verifica N generazioni distinte con `custom_reference_id` corretto), più aggiornamento dei test video_talking/balletti esistenti (creator ora si chiama "Ruby", per risolvere il personaggio da `character.py`).

### 12.12 Rifiniture da feedback su generazioni reali — FATTO (15/07/2026)

L'utente ha generato per davvero delle foto con i prompt prodotti dal sistema e ha dato feedback concreto. Quattro correzioni:

**1. Fedeltà all'originale (colori/posa/espressione).** Il prompt in `_generate_scene_descriptions` era troppo generico su questi tre punti. Riscritto in 4 istruzioni numerate esplicite con **budget di caratteri per sezione** (outfit ~35%, posa ~30%, espressione ~20%, background ~15% del target totale): OUTFIT con colori nominati il più precisamente possibile (es. "rosa cipria" non "rosa"), POSA con richiesta esplicita di replicare angolazione testa/busto/bacino, posizione esatta di mani/braccia/gambe, direzione dello sguardo, ESPRESSIONE FACCIALE con dettaglio su occhi/sorriso/sopracciglia, BACKGROUND ridotto a elementi essenziali (non un elenco esaustivo). Istruzione esplicita di scrivere in modo "denso e diretto" (fatti concreti, non prosa atmosferica) — necessaria perché la prima versione con solo le 4 istruzioni dettagliate, senza vincolo di concisione, produceva testo troppo lungo (3082 caratteri contro un target di 2200-2400: più dettaglio richiede più disciplina di scrittura, non solo più istruzioni). **Bug trovato durante i test**: Claude a volte citava scritte/loghi visibili in foto con virgolette doppie letterali, rompendo il JSON della risposta — fix: istruzione esplicita di usare virgolette singole per le citazioni. Verificato su un giro reale completo dopo tutti i fix: 2383 caratteri, entro target al primo tentativo, con dettaglio genuinamente più preciso (es. "occhi chiusi, ciglia abbassate... sorriso ampio a bocca aperta con denti superiori ben visibili" invece di un generico "espressione"). **Non ancora verificato con una nuova generazione Higgsfield reale** (richiederebbe spesa autorizzata a parte) se questo si traduce in un'immagine visivamente più fedele — verificato solo che il testo del prompt sia più specifico.

**2. Aspect ratio per content_type** (non specificato prima, mancava): 1:1 per caroselli/stories (post statici), 9:16 per il frame-foto dei video talking/balletti/caption (verticali come il video di destinazione). `stories` non era stato specificato dall'utente: assunto 9:16 per coerenza con le Instagram Stories reali (schermo intero verticale), segnalato come assunzione. Implementato in `pipeline_spec.py` (`_ASPECT_SQUARE`/`_ASPECT_VERTICAL` nei `params` di ogni `GenerationOp`), fluisce automaticamente a `generate_image` via `**op.params`.

**Bug reale trovato sistemando questo**: `higgsfield_client.estimate_cost()` convertiva ogni underscore nei nomi dei parametri in trattino (`aspect_ratio` → `--aspect-ratio`), ma il CLI vuole il nome del parametro cosi' com'e' per i parametri normali (underscore, verificato con `--custom_reference_id`/`--background_source` nei test reali precedenti — l'unica eccezione sono i flag "media" tipo `--image-references`, gia' gestiti a parte in `generate_image`/`generate_video`/`generate_motion_control`). Bug mai emerso finora perche' gli unici param passati fin qui (`prompt`, `duration`) sono parole singole senza underscore. Corretto e riverificato con una chiamata cost reale gratuita (`aspect_ratio=9:16` e `1:1` entrambi accettati, 0.12 crediti).

**3. Check durata iniziale (video >15s scartato).** Nuova costante `engine.MAX_VIDEO_DURATION_SECONDS = 15.0` e eccezione dedicata `VideoTooLongError`. Il check avviene in `_select_source_photos` PRIMA di estrarre il frame o chiamare Claude — un video troppo lungo non spreca nessuna chiamata. `process_content_piece` lo riconosce e marca un nuovo stato dedicato `too_long` (stesso principio di `blocked_nsfw`: esito legittimo, non un errore tecnico, non recuperabile con un retry). Nuova funzione pubblica `qa.get_duration_seconds()` (riusa `_ffprobe_json` gia' esistente).

**Test aggiunti**: fedeltà al prompt (nessun test automatico dedicato, e' testo libero — verificato a mano), aspect_ratio passato correttamente per content_type (esteso `test_carosello_usa_carousel_selection_e_genera_una_foto_per_prompt` e `test_process_content_piece_video_talking_end_to_end`), `test_video_troppo_lungo_scartato_senza_spendere_nulla` e `test_video_entro_soglia_procede_normalmente`. 106 test verdi.

### 12.13 Cosa manca ancora

- **Cattura della caption originale** durante il download (instagrapi la espone già in `media_info`, non ancora salvata su `ReferenceItem`) — serve perché le caption non si generano da zero, si copiano/adattano dall'originale.
- **Analisi video per i talking** (dialogo + movimenti + background + outfit + tempo) — FATTA, vedi §12.15. Resta aperto solo: timestamp per segmento nella trascrizione Whisper (oggi solo testo piatto, la sincronizzazione dialogo/movimento è dedotta da Claude guardando i frame senza sapere il secondo esatto di ogni frase) e la verifica con una generazione `seedance_2_0` reale (mai fatta finora, solo testo del prompt validato).
- **Verificare `image_reference` come URL remoto per motion control** e il costo reale di `kling3_0_motion_control` (`manual_cost_estimate=16.0` è il dato dell'utente, non un job nostro completato con successo) — entrambi richiedono un job completato con successo, non solo bloccato da moderazione.
- **Verificare `video_references` reale su `seedance_2_0`** (toggle `settings.SEEDANCE_USE_VIDEO_REFERENCE`, default OFF) — mai testato con un job pagato, vedi §12.15.
- Investigare se una foto di riferimento meno esplicita riduce i blocchi NSFW sui balletti.
- **Verificare con una generazione Higgsfield reale** se il prompt rafforzato (punto 1 sopra) migliora davvero la fedeltà visiva colori/posa/espressione — finora verificato solo il testo del prompt, non l'immagine generata.

### 12.14 Test reale su 3 caroselli — verificato (15/07/2026)

Generati per davvero i caroselli di 3 URL IG reali con la pipeline completa (`write_carousel_prompts` + `generate_image`), output caricato in una cartella dedicata del progetto per revisione. Esito: **buoni, utilizzabili per procedere col workflow**. Coerenza di outfit tra le foto dello stesso set ottima. Fedeltà rispetto alla foto originale (posa esatta, colori, espressione) migliorabile ma non bloccante — annotato in backlog (vedi §13) invece che risolto subito, per non fermare l'avanzamento del workflow su un dettaglio di rifinitura.

Nel terzo carosello, Claude ha rifiutato di scrivere i prompt di rigenerazione per alcune foto sorgente per policy di contenuto (persona reale, inquadratura ravvicinata sessualizzata). Non è un bug: è un limite di policy del modello, non risolvibile lato nostro codice. Annotato in backlog; resta aperta la decisione se dare a questo caso uno status dedicato su `ContentPiece` (proposta, non ancora implementata) analogo a `blocked_nsfw`/`too_long`.

**Cosa resta (prossimi passi UI):** collegare l'azione di produzione reale con conferma; schermata "Oggi" più ricca (agenda del giorno); Libreria con azione di sync reale dallo sheet; rifiniture visive ulteriori.

### 12.15 Analisi video per i talking/caption — FATTO (15/07/2026)

Lo stadio `video_regen` per `video_talking`/`video_caption` (entrambi su `seedance_2_0`) usava un prompt generico e cieco (solo la trascrizione come testo, nessuna vision, nessuna struttura) — punto esplicitamente lasciato aperto in §12.13. Riscritto dopo aver chiarito con l'utente il funzionamento reale di `seedance_2_0` (`higgsfield model get seedance_2_0`, lookup gratuito): il modello accetta anche `video_references` (fino a 3, riferimento di movimento) e `generate_audio` (voce, default `true`), oltre a `start_image`/`duration`/`aspect_ratio`/`resolution`.

**Decisioni prese con l'utente:**
1. **`video_references` come feature opt-in**, non ancora verificata con un job reale: nuovo modulo `aicraft/production/settings.py`, flag `SEEDANCE_USE_VIDEO_REFERENCE` salvato su `AppState` (stesso pattern del profilo attivo), default **OFF**. Quando attivo, il video originale viene passato SOLO per movimento/inquadratura/ritmo camera — l'identità/outfit restano vincolati alla foto Ruby2 (`start_image` + `physical_description` iniettata in codice, mai lasciata al video): `write_talking_video_prompt` scrive questo vincolo esplicitamente nella sezione REFERENCE USAGE del prompt quando il flag è attivo. Verrà acceso a mano dall'utente quando pronto a testare — nessuna generazione reale con `video_references` fatta finora.
2. **`generate_audio` acceso, con dialogo scritto per esteso nel prompt**: l'utente ha fornito due prompt reali `seedance_2_0` funzionanti come esempio (struttura REFERENCE USAGE / STYLE / ACTION-PERFORMANCE / CAMERA / PACING / DIALOGUE-AUDIO / CONSTRAINTS, dialogo riportato tra virgolette e collegato a gesti/movimenti specifici). `generate_audio=true` è già il default reale del modello — esplicitato in `pipeline_spec.py` solo per non dipendere da un default upstream che potrebbe cambiare. Nessun rischio di costo nuovo: il prezzo già verificato (22.5cr/5s) era misurato con questo default già attivo.
3. **`duration` = durata REALE del video originale** (non un valore fisso): `pipeline_spec.py` usa `duration=15` (worst case, MAX_VIDEO_DURATION_SECONDS) solo per la STIMA di budget, stesso principio del `count=3` dei caroselli. La generazione reale in `engine._stage_video_regen` sovrascrive `duration` con `qa.get_duration_seconds()` sul video vero.
4. **9:16 e 720p sempre** per i video seedance (dato dall'utente).

**`claude_creative.write_talking_video_prompt`** (sostituisce interamente `write_regen_prompt`, mai usato altrove): guarda `frame_picker.sample_frames()` — nuova funzione che campiona N frame (default 5, `engine.ANALYSIS_FRAME_COUNT`) equispaziati lungo l'INTERO video (a differenza di `pick_reference_frame`, che guarda solo la finestra iniziale per la foto-base) — e scrive un prompt strutturato in inglese seguendo il formato degli esempi reali. Il dialogo è la trascrizione Whisper VERBATIM: Claude può solo ripulire refusi di trascrizione evidenti, non può inventare, riordinare o aggiungere frasi — iniettato come vincolo esplicito nell'istruzione, stesso principio della `physical_description` mai lasciata alla memoria di Claude. Output testo libero (non JSON, a differenza di `write_carousel_prompts`): nessun target di lunghezza imposto (gli esempi reali variano ampiamente), nessun retry automatico — solo validazione di frame/transcript non vuoti e risposta non vuota.

**`higgsfield_client.generate_video`** esteso con `aspect_ratio`/`resolution`/`generate_audio`/`video_references` (prima solo `start_image`/`duration`). Sintassi CLI verificata solo via lookup gratuito (`model get`), NON con una generazione reale — in particolare `video_references` su `generate_video` (a differenza di `generate_motion_control`, dove è già verificato) resta da confermare al primo giro reale con il flag acceso.

**Non fatto in questo giro** (resta in backlog concettuale, non ancora un'`ImprovementNote`): timestamp per segmento nella trascrizione Whisper (oggi solo testo piatto) — Claude deve dedurre la sincronizzazione dialogo/movimento guardando i frame senza sapere A CHE SECONDO viene detta ogni frase, un limite reale di precisione finché non c'è; verifica con una generazione `seedance_2_0` reale (mai fatta in questo giro, solo testo del prompt validato via test mockati) se il dialogo scritto per esteso produce davvero audio/lip-sync corretti.

**Test**: `tests/test_claude_creative.py` (6 nuovi test su `write_talking_video_prompt`: validazione input, assemblaggio col personaggio, fence markdown, risposta vuota, contenuto condizionale REFERENCE USAGE), `tests/test_frame_picker.py` (4 nuovi test su `sample_frames`), `tests/test_engine.py` (E2E talking aggiornato con asserzioni sui parametri seedance passati, nuovo test dedicato al toggle `video_references`). 125 test verdi in tutto il progetto.

## 13. Backlog ("Da migliorare") — FATTO (15/07/2026)

Su richiesta dell'utente: ogni volta che durante il lavoro emerge un limite noto o un miglioramento possibile ma fuori scope del momento, va registrato in una sezione dedicata dell'app invece che solo nei commenti/doc tecnici, così resta consultabile dall'operatore senza dover leggere codice o chat.

**Modello dati**: `ImprovementNote` (`aicraft/db/models.py`) — `category` (testo libero, es. "qualita'", "limite noto"), `title`, `description` opzionale, `status` (`aperto` | `fatto` | `scartato`, default `aperto`), `created_at`. Segue lo stesso pattern di `AppState`: tabella non nel blueprint originale, aggiunta per un bisogno operativo emerso durante l'implementazione.

**Modulo**: `aicraft/backlog.py` — `add_note`, `list_notes` (filtro per `status`, `None` = tutte, ordina per più recenti), `set_status` (valida contro `STATI_VALIDI`, solleva `ValueError` su stato o id non validi).

**API desktop** (`aicraft/desktop/api.py`): `list_backlog(status="aperto")` (accetta anche `"tutti"` come sentinella UI-friendly, mappata a `None` internamente), `add_backlog_note(category, title, description="")`, `set_backlog_status(note_id, status)`. Stesso pattern `@_endpoint` di tutti gli altri metodi.

**UI** (`aicraft/desktop/web/`): nuovo tab "Da migliorare" in `index.html`, vista `VIEWS.backlog` in `app.js` con filtro per stato (aperto/fatto/scartato/tutti), form di aggiunta voce, e azioni per segnare fatto/scartare/riaprire una voce. Nessun CSS nuovo: riusa le classi esistenti (`card`, `badge`, `btn`).

**Voci reali già presenti** (seedate dopo il test reale su 3 caroselli, §12.14): fedeltà posa/outfit alla foto originale da migliorare, rifiuto Claude su contenuto sessualizzato (limite noto), lunghezza prompt occasionalmente fuori target anche dopo retry.

**Test**: `tests/test_backlog.py` (6 test sul modulo backend) + 2 test aggiunti in `tests/test_desktop_api.py` (`test_backlog_add_e_list`, `test_backlog_set_status_e_filtro`). 19 test verdi su questi due file, nessuna regressione sugli altri.
