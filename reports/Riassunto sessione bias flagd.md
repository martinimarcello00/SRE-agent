# Riassunto sessione: bias flagd su Astronomy Shop (01-02/10/2026)

## Problema e decisione
- Il bias: su Astronomy Shop (OpenTelemetry demo 3.1.0) tutti i guasti sono iniettati tramite flag di flagd. L'agente poteva arrivare a flagd (nodo del grafo, kubectl sulla ConfigMap `flagd-config`, tracce, log) e leggere direttamente il flag attivo.
- **D1 (decisa):** l'agente deve essere cieco rispetto all'iniezione. La risposta corretta è il microservizio guasto, come il `faulty_service` di AIOpsLab (per esempio recommendationCacheFailure → recommendation).
- Il piano completo (5 fasi, decisioni D1-D10) è in `reports/Bias flagd nel grafo Astronomy Shop.md`. Le note di ricerca sono in `research_notes/Bias flagd nel grafo Astronomy Shop/`.
- Prova che la scorciatoia è reale: nel run AIOpsLab `noop_detection_astronomy_shop` del 01/08 l'agente GPT ha eseguito `kubectl logs flagd --all-containers`. File: `AIOpsLab/aiopslab/data/results/7bf64fbe…json`.

## Regole di lavoro (anche in memoria)
- Mai commit, branch o bump del submodule senza chiedere. Prima si spiegano le modifiche.
- Nei commit nessuna attribuzione AI. `reports/` e `research_notes/` non vanno mai committati.
- Non lavorare in `SRE-agent/MCP-server`. Le modifiche al server MCP si fanno nel clone `/home/tecnico/tesi-marcello/k8s-observability-mcp` (branch WIP), senza committare.

## Stato
**Fatto e pushato:**
- `k8s-observability-mcp` `fe10a92` (WIP): flagd tolto dal grafo (nodo e 12 archi), regola dei candidati scritta nell'intestazione del datagraph e nel README. **Il puntatore del submodule `MCP-server` non è aggiornato**: lo aggiorna l'utente più avanti.
- `SRE-agent` `26b00e4` (WIP):
  - `app_summary` senza flagd nei 12 JSON;
  - `sre-agent/tools/hide_flagd.py`, un interceptor `tool_interceptors` di langchain-mcp-adapters sul server `kubernetes`: le chiamate che nominano flagd ricevono `not_found`; dalle liste escono gli elementi che si chiamano flagd; dagli altri output escono le righe che lo nominano;
  - l'interceptor è registrato in `tools/mcp_tools.py`;
  - `mcp-server-kubernetes@4.1.7` fissato in `config/settings.py`.

**Fatto ma NON committato (in `k8s-observability-mcp`), Fase 3:**
- `api/base_k8s_client.py`:
  - `MECHANISM = flagd|flagservice|feature[ _]?flag`;
  - `FLAG_ANNOUNCEMENT` e `hide_flag_announcement`, cioè D5 opzione (a): si riscrive la frase che annuncia il flag e si tiene il sintomo;
  - `get_services_list` e `get_pods_list` escludono flagd.
- `api/jaeger_api.py`: `strip_flagd(trace)` sostituisce `is_stream_trace`. Toglie span di flagd e chiamate verso flagd, attributi ed eventi `feature_flag*`, e riscrive i messaggi d'errore. È applicata in `get_jaeger_traces` e `get_trace`.
- `api/log_api.py`: riscrive gli annunci dei flag e toglie le righe che nominano flagd.
- `api/k8s_api.py`: `get_problematic_pods` salta flagd.
- Test con dati reali:
  - `tests/test_jaeger_api.py`, `tests/test_log_api.py` e `tests/astronomy_shop_traces.json` (6 tracce Jaeger reali, 135 KB);
  - si eseguono dalla root del repo con `PYTHONPATH=. <venv sre-agent>/bin/python tests/test_*.py`;
  - passano, e falliscono se si disattiva il filtro.
- L'utente ha modificato alcuni commenti in `base_k8s_client.py`.

## Verifica live (cluster kind 01/10, poi eliminato)
- **Tool custom:** nessuna menzione di flagd o dei flag in nessun output. Gli errori reali si conservano (product-catalog, checkout, cart, ad in CrashLoop nel triage), la latenza non cambia.
- **Metriche:** 29 pod su 29 restituiscono tutte le metriche, istantanee e range.
  - `container_cpu_cfs_*` e `container_spec_cpu_quota` sono `None` perché Astronomy Shop non imposta limiti CPU: è atteso.
  - Il tool legge la prima serie, cioè il container applicativo; per i pod multi-container (grafana) vede solo il primo container.
  - Prometheus su `:32000` è un port-forward aperto dalla CLI di AIOpsLab: muore con la CLI.
- **Interceptor kubectl:** funziona. Residuo: le variabili `FLAGD_HOST`/`FLAGD_PORT` nelle liste YAML dei deployment e gli eventi dell'init container `wait-for-flagd`. Creano una pista falsa: l'agente vede un host che "non esiste". **Proposta (A), in attesa di conferma:** nascondere anche questi in `hide_flagd.py` (~10 righe più un test).

## Fase 5: iniezione AIOpsLab sulla demo 3.1.0 (dettagli in memoria `astronomy_shop_injection_findings`)
- **`productCatalogFailure`:** non produce guasti. Confidenza alta: la regola di targeting restituisce "off" in entrambi i rami (116/116 valutazioni), 0 errori in 15 minuti, mentre con il ramo corretto in "on" compaiono 50 errori in 3 minuti.
- **`cartFailure`:**
  - **Misurato:** "on" non è una variante valida, e flagd rifiuta l'intera configurazione (`flag_not_found` per tutti i flag).
  - **Dedotto:** che cart non fallisca. È stato osservato iniettando 5 flag insieme, non lo scenario da solo.
  - **Verificato:** con "100%" il guasto funziona (`EmptyCart` "Can't access cart storage").
- **Ritardo:** gli SDK hanno continuato a usare i valori in cache per più di 10 minuti (ad, recommendation, checkout, fraud-detection). Osservato in condizioni non pulite.
- **checkout** non scrive su stdout, quindi la riga su `kafkaQueueProblems` non arriva a `get_logs`.
- **Proposta, in attesa:** misure pulite, uno scenario alla volta con il flusso AIOpsLab standard: cart, product-catalog e adFailure come controllo.

## Prossimi passi
1. Conferma della proposta (A) per `FLAGD_HOST`.
2. Misure pulite della Fase 5, poi decisione D9: correggere l'injector di AIOpsLab ("100%" per cart, regola di targeting per product-catalog, riavvio dei servizi che leggono il flag) oppure escludere gli scenari.
3. Review dell'utente, poi commit e push della Fase 3 in `k8s-observability-mcp`. Poi l'utente aggiorna il puntatore `MCP-server`.
4. Verifica del planner (Fase 1.5): dopo l'aggiornamento del puntatore, flagd non deve comparire nel prompt del planner.
5. Fase 4:
   - `RCA_gt` senza nomi di flag, con flag e injector spostati in un campo di provenienza;
   - il prompt del giudice deve trattare il meccanismo d'iniezione come una risposta sbagliata;
   - localizzazione con match esatto sui nomi normalizzati e `accepted_targets` per scenario;
   - decisioni D6-D8.

## Come riavviare il cluster
- **Registry:** `sh registry/setup-registry.sh`. Il container `kind-registry` è già attivo.
- **Setup:** script in `research_notes/Bias flagd nel grafo Astronomy Shop/scripts/` (`start_cluster.py <problem_id>`). Usa `setup_cluster_and_aiopslab` con AIOpsLab in `/home/tecnico/workspace_aiops/AIOpsLab`. Va lanciato tenendo viva la CLI, perché è lei a tenere aperto il port-forward di Prometheus.
- **Jaeger:** `kubectl port-forward -n astronomy-shop <pod jaeger> 16687:16686`, URL `http://localhost:16687/jaeger/ui`.
- **Prometheus**, se la CLI muore: `kubectl port-forward svc/prometheus-server 32000:80 -n observe`.
- **Verifiche:**
  - `verify_live.py` si lancia da `k8s-observability-mcp` con `TARGET_NAMESPACE`, `JAEGER_URL` e `PROMETHEUS_SERVER_URL`. Neo4j non è attivo: lo script assegna un valore fittizio a `tools.datagraph`.
  - `verify_kubectl.py` si lancia da `SRE-agent/sre-agent` con `PYTHONPATH=.`.
