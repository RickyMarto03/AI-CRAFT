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

**Punto 6 della checklist sotto: rifiniture UI dell'app desktop.** Deciso con l'utente il
15/07/2026, subito dopo aver completato il punto 1 (analisi video talking). Nello specifico:
- Collegare l'azione di produzione REALE (oggi in UI c'e' solo l'anteprima senza costi,
  `production_preview` — la produzione vera va lanciata da CLI, `aicraft/cli.py produce`) con
  un flusso di conferma esplicita in UI, dato che spende crediti veri.
- Schermata "Oggi" piu' ricca: agenda del giorno (cosa e' pianificato/in produzione oggi),
  non solo i chip riassuntivi attuali.
- Tab "Libreria" con un'azione di sync reale dallo sheet (oggi mostra solo le statistiche,
  il sync va lanciato da CLI).

File coinvolti: `aicraft/desktop/web/{index.html,app.js,style.css}`, eventualmente nuovi
endpoint in `aicraft/desktop/api.py` (pattern `@_endpoint` gia' consolidato, vedi i metodi
esistenti per lo stile).

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
2. [ ] Cattura caption originale dal download (instagrapi la espone in `media_info`, non ancora
       salvata su `ReferenceItem`).
3. [ ] Verifiche con job Higgsfield REALI mai fatte finora (nessuna per limiti di budget/cautela
       dell'utente su spese non pianificate): `video_references` su seedance_2_0 (toggle
       `settings.SEEDANCE_USE_VIDEO_REFERENCE`, default OFF), `generate_audio` end-to-end,
       `image_reference` come URL remoto per `kling3_0_motion_control`, costo reale di
       `kling3_0_motion_control` (oggi solo il dato ~16cr/10s fornito a voce dall'utente).
4. [ ] Fedelta posa/outfit alla foto originale nei caroselli — gia' segnata nella sezione "Da
       migliorare" dell'app, qualita' buona ma migliorabile.
5. [ ] Stato dedicato su `ContentPiece` per i rifiuti di contenuto di Claude (oggi genericamente
       "error") — proposto, mai confermato in scope dall'utente.
6. [ ] **Rifiniture UI — vedi "Task su cui lavorare adesso" sopra. PROSSIMO TASK.**

## Log sessioni (piu' recente in cima — AGGIUNGERE una voce nuova, non sovrascrivere le altre)

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
