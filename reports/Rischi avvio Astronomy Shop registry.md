# Le cache registry reggono l'avvio, ma restano fragili

**Nessun elemento oggi impedisce all'Astronomy Shop (chart 0.42.1, app 3.1.0) di partire su kind con i tre pull-through cache: le cache di docker.io, ghcr.io e quay.io sono già popolate con tutte le immagini del chart, e containerd 1.7.24 ricade sull'upstream se un mirror non risponde.** I rischi reali sono di tipo operativo e non riguardano la configurazione dei proxy. Il primo è che `registry/setup-registry.sh`, eseguito nello stato attuale, distrugge la cache dei due mirror fermi (`kind-registry-ghcr`, `kind-registry-quay`, circa 3 GB). Il secondo è che due scenari di fault injection (cartFailure, productCatalogFailure) non funzionano sul chart 0.42.1. Il terzo è che il controllo di avvio di AIOpsLab (`wait_for_ready`) dà falsi positivi, perché quasi nessun servizio del demo ha una readiness probe. Per Docker Hub come upstream il rischio è la quota (100 pull anonimi per finestra, condivisa dall'IP), ma con cache calda un deploy genera zero GET upstream, e un cold fill completo ne costa circa 12 (circa 22 con le 5 immagini openebs). Le note si contraddicono su due punti. Per `proxy.ttl`, la fonte più affidabile (codice 2.8.3 più il binario in esecuzione, verificato di nuovo oggi) dice che l'opzione **non esiste** in 2.8.3. Per la finestra del rate limit (1 h o 6 h) la questione resta **non risolta**. Il report separa ciò che è verificato da ciò che è dedotto o non verificato.

Data di riferimento: 2026-10-05. Le note sorgente sono quattro (`astronomy_shop_startup.md`, `docker_hub.md`, `local_environment_audit.md`, `registry_proxy_containerd.md`). Dove le note citano una fonte, l'URL è riportato qui. Le osservazioni "LOCALE" provengono da comandi in sola lettura sulla macchina, riportati nelle note. Non è stato creato alcun cluster.

## Dove le note si contraddicono e quale fonte prevale

### `proxy.ttl` in registry 2.8.3: non esiste

La nota sul proxy legge `configuration.go` al tag v2.8.3: la struct `Proxy` contiene solo `RemoteURL`, `Username`, `Password`. Il TTL è la costante `repositoryTTL = 24*7*time.Hour` ([configuration.go v2.8.3](https://raw.githubusercontent.com/distribution/distribution/v2.8.3/configuration/configuration.go), [proxymanifeststore.go v2.8.3](https://raw.githubusercontent.com/distribution/distribution/v2.8.3/registry/proxy/proxymanifeststore.go)). Le note su Docker Hub e sull'audit locale consigliano invece `ttl: 0` e citano la documentazione di Distribution ([Configuring a registry](https://distribution.github.io/distribution/about/configuration/)). **Prevale la nota sul proxy**, per tre motivi:

1. La documentazione pubblicata descrive la linea di rilascio corrente (v3), non il tag 2.8.3 usato qui.
2. Il codice è letto al tag esatto.
3. Il log locale mostra `ttl=167h59m59s` in tutti e tre i container, cioè il valore cablato.

Oggi ho aggiunto una verifica indipendente sul binario in esecuzione (`docker exec kind-registry sh -c "strings -n 6 /bin/registry | grep -i remoteurl"`). Trova il tag di struct `yaml:"remoteurl"` e nessun tag `yaml:"...ttl..."`. Quindi `ttl:` nel file di configurazione sarebbe ignorato in silenzio.

La nota sul proxy deduce anche che la scadenza del 2026-10-09 è un no-op senza `storage.delete.enabled`, perché i callback falliscono con `ErrUnsupported` e l'entry viene comunque scartata ([scheduler.go v2.8.3](https://raw.githubusercontent.com/distribution/distribution/v2.8.3/registry/proxy/scheduler/scheduler.go), [issue #4249](https://github.com/distribution/distribution/issues/4249)). Questa deduzione è **plausibile ma non osservata**. L'audit e la nota su Docker Hub, invece, trattano la scadenza come "time bomb" distruttiva e questa lettura è **meno affidabile**, perché si basa sulla semantica documentata di v3. Il test decisivo è futuro: dopo il 2026-10-09 ~15:31 UTC bisogna cercare nei log `Scheduler error returned from OnExpire` e rifare `du -sh`.

### Finestra del rate limit Docker Hub: 1 h o 6 h, non risolto

La documentazione ufficiale dice 100 pull anonimi per IPv4 (o IPv6 /64) e 200 per account Personal ogni 6 ore ([Docker Hub pull usage and limits](https://docs.docker.com/docker-hub/usage/pulls/)). Il JWT anonimo di auth.docker.io contiene `pull_limit_interval: 21600` (6 h, nota Docker Hub). Gli header live dicono invece `ratelimit-limit: 100;w=3600` (1 h). Ho ripetuto oggi la HEAD su `ratelimitpreview/test` dall'IP della macchina e ottengo di nuovo `100;w=3600`, `remaining 100`. Un'altra fonte vede lo stesso header con 429 reali ([ducktape #7936](https://github.com/agentydragon/ducktape/issues/7936)).

Nessuna delle due fonti è decisiva. La documentazione è la fonte primaria sulla policy, ma può essere in ritardo sulla realtà. Gli header sono il valore operativo osservato, ma vengono dal repo di test `ratelimitpreview/test` e non da un pull reale. Non c'è modo di sapere quale finestra sia applicata. Qui il dubbio non cambia le decisioni, perché si assume la finestra più stretta (100/h): un cold fill costa 12-22 GET, e servirebbero 4-5 cold fill ravvicinati per esaurire la quota. Un fatto storico è utile come contesto: l'annuncio di febbraio 2025 (10 pull/h anonimi) fu ritirato l'8 aprile 2025 ([Docker blog](https://www.docker.com/blog/revisiting-docker-hub-policies-prioritizing-developer-experience/)). Docker promette 6 mesi di preavviso per ogni nuova applicazione, e non ho trovato fonti primarie 2026 che cambino i limiti.

### Altre discrepanze minori

- **Mirror fermi "BLOCKER" o no.** L'audit locale classifica ghcr/quay fermi come blocker. La nota su containerd, leggendo `resolver.go` v1.7.24, mostra che qualunque errore o status maggiore di 399 sul mirror passa all'host successivo, cioè al `server` upstream ([resolver.go v1.7.24](https://raw.githubusercontent.com/containerd/containerd/v1.7.24/remotes/docker/resolver.go)). Prevale la lettura del codice: l'avvio non si blocca, si perde solo il beneficio della cache. Il comportamento non è stato provato su un cluster. Qui lo classifico "alto", non "bloccante".
- **Numero di immagini quay.io.** Il chart ne usa 2 (prometheus v3.14.0, k8s-sidecar). La cache quay contiene 6 repository, perché AIOpsLab distribuisce anche un proprio chart Prometheus nel namespace `observe`. Le due cifre sono entrambe corrette.
- **Immagini docker.io.** Sono 7 riferimenti ma 6 immagini distinte, perché `busybox` senza tag e `busybox:latest` sono lo stesso repo e lo stesso tag.
- **Catalogo.** `REGISTRY_SETUP.md` dice che `/v2/_catalog` resta vuoto sui proxy. Il registry docker.io oggi elenca 11 repository (LOCALE), quindi la frase è inesatta.
- **Documentazione UPGRADING.** La documentazione del chart è contraddittoria su k6 e Locust per il load generator. Il render 0.42.1 è basato su Locust, quindi vale il render.

## Bloccanti (condizionali): due azioni o scenari che invalidano l'esperimento

### B1. Eseguire `setup-registry.sh` così com'è azzera la cache ghcr/quay

**Evidenza (LOCALE, audit §4 e nota sul proxy §4-5).**
- Lo script, per ogni container non in esecuzione, fa `docker rm -f` e poi `docker run`.
- La cache sta in un volume anonimo montato da `registry:2` su `/var/lib/registry`, quindi sopravvive a stop e start ma non a `rm -f`.
- Oggi `kind-registry-ghcr` (volume 2.7 GB, 323 entry) e `kind-registry-quay` (0.28 GB, 60 entry) sono `Exited (2)`: il container nuovo parte vuoto e i vecchi volumi restano orfani.
- Il codice 2 coincide con uno stop manuale delle 2026-10-02 15:34:31, avvenuto 50 minuti dopo l'avvio e nello stesso secondo per entrambi. La spiegazione (SIGTERM a un PID 1 Go senza handler) è **un'inferenza non riprodotta**. Gli ultimi log sono normali e senza panic.
- Ricontrollato oggi con `docker ps -a`: lo stato è invariato.

**Come rilevarlo:** `docker ps -a --format '{{.Names}} {{.Status}}'`. Se ghcr/quay compaiono come `Exited`, lo script è pericoloso.

**Fix minimo:** `docker start kind-registry-ghcr kind-registry-quay` (non lo script), poi `curl -s -o /dev/null -w '%{http_code}\n' localhost:5001/v2/` sulle porte 5001, 5002 e 5003, che deve restituire 200. Per eliminare il rischio alla radice si può cambiare lo script in modo che usi `docker start` se il container esiste e dichiarare volumi nominati: è una modifica del repo, da decidere.

### B2. Le iniezioni cartFailure e productCatalogFailure non funzionano sul chart 0.42.1

Non riguarda l'avvio dei pod ma la validità degli esperimenti.

**Evidenza.**
- Il render 0.42.1 definisce cartFailure con varianti `10%`, `25%`, `50%`, `75%`, `90%`, `100%` e `off`, senza `on`. La memoria di progetto (LOCALE, `astronomy_shop_injection_findings.md`) riporta che AIOpsLab imposta `on`, valore invalido: flagd rifiuta l'intero file e tutti i flag diventano `flag_not_found`.
- `productCatalogFailure` ha un targeting che risolve sempre `off`, e il render 0.42.1 contiene ancora la regola ([demo #3816](https://github.com/open-telemetry/opentelemetry-demo/issues/3816), segnalato il 2026-08-09, chiuso, release di correzione non individuata).
- Le SDK mantengono i flag in cache per oltre 10 minuti dopo il riavvio di flagd (memoria di progetto; non rimisurato qui).

**Come rilevarlo:** `kubectl -n astronomy-shop get cm flagd-config -o yaml` per vedere varianti e targeting, e `kubectl -n astronomy-shop logs deploy/flagd -c flagd | grep -i "invalid\|error"`.

**Fix minimo.** Per cartFailure, usare una variante valida (per esempio `100%`) nell'injector di AIOpsLab. Per productCatalogFailure, escludere lo scenario o correggere la regola di targeting: non c'è un fix upstream confermato in 0.42.1. Dopo ogni injection bisogna attendere più di 10 minuti prima di valutare l'agente. Questa parte non è stata verificata dal vivo in questa ricerca.

## Alto: degradano o falsano l'esperimento senza fermarlo

### H1. Mirror ghcr/quay fermi: pull silenziosamente diretti dall'upstream

**Evidenza.**
- La funzione `configure_kind_registry` scrive i `hosts.toml` per tutti e tre gli upstream anche se un mirror è fermo, e ritorna sempre `True` (LOCALE, `automate_cluster_creation.py`).
- Secondo la documentazione di containerd, con un host listato "the hosts will be tried first in the order listed. If all host(s) are tried then server will be used as a fallback" ([hosts.md](https://github.com/containerd/containerd/blob/main/docs/hosts.md)).
- Il codice 1.7.24 conferma il fallback su errore di trasporto, 404 e status maggiore di 399.

**Conseguenze.**
- Dei 34 riferimenti del chart, 25 vengono da ghcr.io (23 immagini del demo, flagd, valkey), 2 da quay.io e 7 da docker.io.
- Con ghcr fermo, 25 immagini dal demo vengono scaricate direttamente da ghcr.io, senza cache. ghcr.io non pubblica limiti per pacchetti pubblici (solo una [risposta di community](https://github.com/orgs/community/discussions/49671)), quindi il rischio è soprattutto la lentezza.
- **Non verificato:** il comportamento live, perché non esiste un cluster.
- **Caso peggiore da conoscere (inferenza dal codice, non misurato).** Un mirror in hang, con container su ma bloccato, causa fino a 30 s di dial timeout per richiesta (nessun `dial_timeout` in containerd 1.7.24) o attese senza limite: meglio un mirror spento che sospeso.

**Come rilevarlo.** `docker ps -a`; dopo il primo scenario, `docker logs --since 10m kind-registry-ghcr | grep -c 'ns=ghcr.io'` deve essere maggiore di zero.

**Fix minimo:** B1 (`docker start`), poi la verifica con `curl` sulle porte. Un preflight che rifiuti il lancio se un mirror non è `Running` e non risponde 200 su `/v2/` è l'estensione naturale, da decidere.

### H2. Docker Hub: cold fill, 429 e immagine busybox:latest

**Stato attuale (verificato).**
- La cache docker.io ha tutti e 6 i tag del chart (grafana `13.2.2-distroless`, jaeger `2.20.0`, busybox `latest`, postgres `18.4`, opensearch `3.8.0`, otel-collector-contrib `0.160.0`).
- Oggi una HEAD upstream su ciascun tag restituisce 200 con lo stesso digest della cache (6/6).
- Le HEAD non decrementano `ratelimit-remaining` (rimasto 100): le HEAD sono gratuite.
- Nei log del proxy non compaiono 429.

**Meccanismo del consumo di quota (codice 2.8.3 verificato, nota Docker Hub §2).**
- Il proxy risolve il tag con una HEAD upstream e ricade sulla copia locale se l'upstream fallisce ([proxytagservice.go](https://raw.githubusercontent.com/distribution/distribution/v2.8.3/registry/proxy/proxytagservice.go)).
- Una miss di manifest per digest causa un GET upstream (contato). Con cache calda, quindi, non c'è consumo di quota.
- Senza `proxy.username` e `proxy.password` le richieste sono anonime e contano sulla quota dell'IP.
- Dai log, un cold fill è di 22 GET su 11 immagini (2 per immagine: indice e manifest della piattaforma). Per le 6 immagini del chart sono circa 12, cioè il 12-22 % di una quota da 100.
- **Non verificato:** se Docker conti indice e manifest figlio come 2 pull o come 1, e se i blob siano esenti.
- L'IP `131.175.28.197` sembra istituzionale e condiviso: non verificabile.

**Quando il rischio diventa reale.**
- Cache persa (per esempio B1 applicato anche al container docker.io, o `docker rm`) combinata con più cold fill ravvicinati o con altri utenti dietro lo stesso NAT.
- Su 429 una miss può produrre un 500 dal proxy: la [issue #3270](https://github.com/docker/distribution/issues/3270) è del 2020 e non è verificata su 2.8.3. containerd passa poi al `server` diretto (stesso IP, stessa quota) e il pod finisce in `ImagePullBackOff` con messaggio `toomanyrequests`.
- I sei init container `busybox:latest` hanno `imagePullPolicy` non impostata, che per `latest` significa `Always`: contattano il proxy a ogni avvio di pod (10 HEAD in circa 4 minuti nei log). Con cache calda e proxy attivo sono innocui. Se il proxy venisse riavviato mentre Docker Hub è irraggiungibile, il proxy va in panic all'avvio (`getAuthURLs` fa `http.Get` e fa `panic` sull'errore: [proxyauth.go](https://raw.githubusercontent.com/distribution/distribution/v2.8.3/registry/proxy/proxyauth.go)) e entra in crash loop con `restart=always`.
- opensearch pesa 1136 MB compressi (grafana 460 MB, postgres 162 MB): è il primo candidato a timeout in un pull a freddo.

**Come rilevarlo.**
- `kubectl -n astronomy-shop get pods` per stati `Init:ImagePullBackOff` e `ErrImagePull`.
- `kubectl describe pod` per `toomanyrequests`.
- Per la quota rimanente: HEAD su `ratelimitpreview/test` con token anonimo, come fatto sopra.

**Fix minimo, in ordine di costo.**
1. Non spegnere mai i volumi della cache e riscaldarla prima di una campagna.
2. Pinnare per digest, se il chart lo permette. Non verificato per tutti i sotto-chart; con `imageOverride.tag=<tag>@sha256:...` per i componenti del demo non è testato.
3. Aggiungere al proxy un token Docker Hub read-only (200/6h per account invece di 100 per IP). Le credenziali finiscono nel file di config su disco e i repository privati dell'account verrebbero serviti dal mirror ([Docker mirror docs](https://docs.docker.com/docker-hub/image-library/mirror/)).
4. Aggiungere `mirror.gcr.io` come secondo host. Oggi risponde 200 alle HEAD per tutti e 6 i tag (verificato dalla nota), ma non è provato che serva anche i blob, e Google non garantisce la presenza in cache ([docs Artifact Registry](https://docs.cloud.google.com/artifact-registry/docs/pull-cached-dockerhub-images)).
5. `docker save` e `kind load` come ultima risorsa: problemi noti con le immagini multi-arch ([kind #4224](https://github.com/kubernetes-sigs/kind/issues/4224)).

### H3. `wait_for_ready` dà falsi positivi e `helm install` non viene controllato

**Evidenza (LOCALE, codice di `astronomy_shop.py`, `helm.py`, `kubectl.py`; chart renderizzato).**
- `Helm.install` non cattura stderr e non controlla il codice di ritorno: l'`error` è sempre `None`. Non c'è `--wait`, `--timeout`, `--version` né `helm repo update`.
- `wait_for_ready(max_wait=900)` considera pronto un pod con tutti i container `ready`. Solo otel-collector-agent, grafana, jaeger, prometheus e opensearch hanno probe. Per kafka, frontend, checkout, flagd, postgres, valkey e load-generator "Ready" significa solo "container avviato". Un pod in crash loop può comparire come pronto tra un riavvio e l'altro.
- Un singolo pod `Evicted` o `Failed` non diventa mai pronto e il wait scade a 900 s.
- La modifica a `max_wait=900` è nel working copy non committato di AIOpsLab.
- Non c'è controllo end-to-end: né `frontend-proxy:8080`, né flagd, né il topic Kafka `orders`, né il collector, né i target Prometheus.
- **Non verificato:** se altri punti del codice AIOpsLab (`Orchestrator`, `start_workload`) aggiungano controlli dopo `deploy()`.

**Come rilevarlo.**
- `helm list -n astronomy-shop -o json` deve mostrare `status: deployed` e chart `opentelemetry-demo-0.42.1`.
- Contare 30 pod: 28 Deployment, 1 StatefulSet, 1 DaemonSet (1 pod sul worker).
- Controllare `restartCount == 0` e `lastState.terminated.reason` per `OOMKilled`.
- Fare un GET su `/` e `/api/products` via port-forward di `svc/frontend-proxy:8080`.
- Verificare che Jaeger `/api/services` elenchi i servizi.
- Verificare che la UI Locust (`:8089/stats/requests`) mostri richieste maggiori di zero.
- Attendere qualche minuto di carico prima di far partire l'agente.

**Fix minimo:** eseguire i controlli sopra a mano come smoke test. Rendere l'installazione fallibile in modo esplicito (codice di ritorno) richiede di modificare AIOpsLab, quindi è una decisione dell'utente.

### H4. OOM sui limiti di memoria bassi, soprattutto in run lunghi

**Evidenza.**
- Il render 0.42.1 ha limiti bassi senza requests: checkout 32Mi, product-catalog 32Mi, currency 20Mi, shipping 20Mi, valkey 20Mi, ad 300Mi, fraud-detection 300Mi, kafka 700Mi, Prometheus 400Mi. Il carico di memoria complessivo è circa 9.0 GiB di request effettive e 9.8-10 GiB di limit (calcolo della nota sul render).
- Prometheus a 400Mi finisce in `OOMKilled`/`CrashLoopBackOff` quando il replay del WAL accumulato su emptyDir supera il limite ([demo #3811](https://github.com/open-telemetry/opentelemetry-demo/issues/3811), segnalato per chart 0.41.0; il PR di correzione, che limita il TSDB invece di alzare la memoria, è ancora aperto per 0.42.3: [helm-charts #2422](https://github.com/open-telemetry/opentelemetry-helm-charts/pull/2422)).
- ad e fraud-detection risultavano oltre il limite in [demo #3034](https://github.com/open-telemetry/opentelemetry-demo/issues/3034) (riassunto di un fetcher, risoluzione non verificata) e i valori 0.42.1 sono ancora 300Mi.
- Il changelog 0.42.1 alza checkout e product-catalog da 20Mi a 32Mi, ma [helm-charts #2121](https://github.com/open-telemetry/opentelemetry-helm-charts/issues/2121) e [#2376](https://github.com/open-telemetry/opentelemetry-helm-charts/issues/2376) mostrano OOM a 20Mi.
- Il load generator con Chromium ha limite 1500Mi.

**Mitigazione dal contesto locale.** L'host ha 62 GiB di RAM e 32 CPU, quindi `Pending` per `Insufficient memory` è improbabile: il rischio è OOM dentro il cgroup del singolo pod, non scheduling. La documentazione upstream parla di "6 GB of free RAM", il render dà 9-10 GiB: nessuna spiegazione ufficiale trovata ([docs](https://opentelemetry.io/docs/demo/kubernetes-deployment/)).

**Come rilevarlo.** `kubectl get pods` (colonna RESTARTS), `kubectl describe pod` (`Last State: Terminated Reason: OOMKilled`), `kubectl get events --sort-by=.lastTimestamp`.

**Fix minimo.** Alzare i limiti su Prometheus, ad e fraud-detection: `components.<nome>.resources.limits.memory` è una chiave verificata sul chart, mentre il percorso per Prometheus (`prometheus.server.resources.limits.memory`) non è stato testato. Ogni override modifica il sistema sotto test: va deciso in base alle esigenze della tesi. Non è stata misurata l'occupazione reale a regime su kind.

## Medio: riproducibilità e dipendenze da rete

### M1. Chart non pinnato: 0.42.2 esiste già

`Helm.install` non passa `--version` e `astronomy-shop.json` non ne ha uno. L'indice upstream elenca 0.42.2 (app 3.1.0, 2026-10-02), mentre l'indice locale in cache (del 2026-10-02) risolve 0.42.1 (LOCALE; indice upstream letto dalla nota). La 0.42.2 modifica solo fixture di test ([release 0.42.2](https://github.com/open-telemetry/opentelemetry-helm-charts/releases/tag/opentelemetry-demo-0.42.2)), quindi oggi la differenza è innocua, ma il chart esce circa ogni settimana (0.42.0, 0.42.1, 0.42.2 in tre settimane) e le versioni 0.41 e 0.42 hanno introdotto breaking change. Un `helm repo update` fatto per altro motivo cambierebbe versione e renderebbe fredde le immagini. Va tenuto presente che il chart non supporta l'aggiornamento in-place: serve rimuovere prima la release. **Fix minimo:** non lanciare `helm repo update`, e aggiungere `version: 0.42.1` ai metadati dell'app (la nota dice che `Helm.install` passa `--version` se presente; modifica in AIOpsLab, non committata). Registrare `helm list -o json` per ogni run.

### M2. Tag mobili: busybox:latest e immagine del nodo kind

`busybox:latest` (6 init container) e il tag senza versione (`init-config` di flagd) possono cambiare digest tra esperimenti: oggi cache e upstream coincidono (digest `sha256:fd7dc986...`), ma un nuovo digest produce un nuovo GET contato e un busybox diverso. Anche l'immagine del nodo `jacksonarthurclark/aiopslab-kind-x86:latest` è mobile (LOCALE: `FROM kindest/node:v1.32.1`, containerd 1.7.24, creata il 2025-02-27). Le immagini del demo sono pinnate per tag `3.1.0-*` ma non per digest. **Fix minimo:** pin per digest dove possibile; registrare `kubectl get nodes -o wide` per run.

### M3. Dipendenze non coperte dai mirror

Un'immagine per run non passa da alcun mirror: `registry.cn-wulanchabu.aliyuncs.com/moge1/kube-state-metrics:v2.3.0`, del chart Prometheus di AIOpsLab (namespace `observe`). Il registry risponde 401 con challenge, ma il pull anonimo non è verificato. Inoltre `Orchestrator.init_problem` fa `kubectl apply -f https://openebs.github.io/charts/openebs-operator.yaml` ad ogni run (raggiungibile oggi, HTTP 200) e le immagini `openebs/*` vengono da docker.io (5 immagini, nella cache). **Come rilevarlo:** `kubectl -n observe get pods`. **Fix minimo:** pre-scaricarla con `kind load` oppure accettare il rischio; per OpenEBS, scaricare il manifest una volta.

### M4. Scadenza TTL del 2026-10-09: attesa innocua, da osservare

Le entry dello scheduler scadono il 2026-10-09 tra le 14:44 e le 15:31 UTC (LOCALE, `scheduler-state.json`). Come discusso sopra, senza `storage.delete.enabled` il codice indica che non cancella dati. **Non aggiungere** `ttl:` (ignorato) né `storage.delete.enabled: true`: quest'ultimo attiverebbe i bug sui blob condivisi proprio per il repository `open-telemetry/demo`, che ha 23 tag con layer base condivisi ([#2367](https://github.com/docker/distribution/issues/2367), [PR #4872](https://github.com/distribution/distribution/pull/4872); gli effetti di #4957 su 2.8.3 non sono verificati). Se il container è fermo alla scadenza, le entry scadute vengono processate al successivo avvio. **Verifica:** dopo il 2026-10-09, `docker logs <registry> | grep OnExpire` e `du -sh` invariato.

### M5. Host, rete e residui

- **inotify.** `fs.inotify.max_user_instances = 128` (LOCALE), mentre kind raccomanda 512 ([kind known issues](https://kind.sigs.k8s.io/docs/user/known-issues/)). Root ne usa 21 su 128 senza cluster. Sintomo: "too many open files" in kubelet o containerd. Fix: `sudo sysctl fs.inotify.max_user_instances=512`.
- **vm.max_map_count.** È 1048576, quindi OpenSearch non ha problemi (richiede 262144: [OpenSearch](https://opensearch.org/blog/error-logs/error-log-max-virtual-memory-areas-vm-max_map_count-is-too-low/)). Il chart non usa init sysctl e imposta `bootstrap.memory_lock=true`: il fatto che `discovery.type=single-node` allenti i bootstrap check è **non verificato**.
- **hostPort.** Il DaemonSet del collector usa hostPort 4317, 4318, 6831, 14250, 14268, 9411: una seconda release o un secondo collector sullo stesso nodo li farebbe collidere (`FailedScheduling: didn't have free ports`). Non verificato dal vivo.
- **Residui.** Nessun cluster kind e nessuna release Helm oggi (LOCALE). Se restasse una release o il namespace di una versione precedente (per esempio 0.37.x), l'installazione fallirebbe: controllare `helm list -A` e `kubectl get ns astronomy-shop`. Gli unici residui sono due volumi anonimi neo4j e gli endpoint di rete dei mirror fermi.
- **Politica di restart.** `restart=always` non riavvia un container fermato a mano finché non riparte il daemon Docker ([Docker docs](https://docs.docker.com/engine/containers/start-containers-automatically/)): al prossimo reboot i mirror fermi riprenderebbero da soli. `--restart=unless-stopped` evita la sorpresa.

### M6. Cold start concorrente su una cache fredda

Con cache fredda, ogni blob costa due download upstream (uno per il client, uno per il riempimento della cache) e i richiedenti concorrenti ne aggiungono altri ([proxyblobstore.go](https://raw.githubusercontent.com/distribution/distribution/v2.8.3/registry/proxy/proxyblobstore.go), lettura del codice). Il riempimento del 2026-10-02 (548 risposte, tutte 200) dimostra che 2 nodi in parallelo reggono. Un caso di blob corrotto da 6 GiB su pull concorrenti ([homelab #1282](https://github.com/teststuffstash/homelab/issues/1282)) è una singola segnalazione e riguarda layer molto più grandi dei nostri.

## Basso: da conoscere, nessuna azione immediata

Un primo gruppo riguarda lo scarto di versioni e i file non tracciati.
- **Disallineamento kubectl/nodo.** kubectl è v1.36.2 contro nodi Kubernetes v1.32.1, oltre la politica ±1 (affermazione dalla conoscenza di fondo dell'autore delle note, non recuperata da una fonte). Con gli stessi binari ha funzionato il 2026-10-02. kind è una build `v0.33.0-alpha`: la compatibilità con `kindest/node:v1.32.1` è provata solo empiricamente.
- **File non tracciati.** `registry/registry-config-ghcr.yml` e `registry/registry-config-quay.yml` sono non tracciati, e sono montati per percorso assoluto dai container: un `git clean` li cancellerebbe e i mirror non ripartirebbero. Anche le modifiche a `setup-registry.sh`, `automate_cluster_creation.py`, `REGISTRY_SETUP.md` e a `astronomy_shop.py` di AIOpsLab non sono committate. Se tracciarli o no è una decisione dell'utente: questo report non raccomanda alcuna azione git.

Un secondo gruppo riguarda piattaforme e documentazione.
- **ARM.** `kind-config-arm.yaml` non ha la patch `config_path`, quindi su host ARM i `hosts.toml` sarebbero ignorati. Anche sul lato cache c'è solo il manifest linux/amd64: un nodo arm64 produrrebbe una miss.
- **Documentazione obsoleta.** `REGISTRY_SETUP.md` cita ancora la porta 5000 invece di 5001 e `./registry-data/` invece del volume anonimo.
- **Rumore dal demo.** [Demo #4053](https://github.com/open-telemetry/opentelemetry-demo/issues/4053): IPv6 interrompe frontend-proxy verso load-generator (aperta). Il flag `failedReadinessProbe` esiste nel set di flag. Nessun problema specifico di avvio su kind è emerso dalle ricerche.

## Cosa è verificato e cosa no

| Stato | Elementi |
|---|---|
| Verificato (LOCALE o live, anche oggi) | Cache docker.io calda con 6/6 tag e digest uguali all'upstream; HEAD gratuite e quota 100/100; log senza 429; ghcr/quay `Exited (2)`; proxy 2.8.3 senza `ttl` (codice più `strings` sul binario); 34 immagini del chart da 3 registry; anonymous volume e `docker rm -f` nello script; kind config x86 con `config_path`; host 62 GiB, `max_map_count` 1048576, inotify instances 128; nessun cluster; `Helm.install` senza controllo del codice di ritorno |
| Verificato da codice sorgente letto (non eseguito) | Fallback containerd 1.7.24; sequenza HEAD e GET del proxy; scadenza che fallisce con `ErrUnsupported`; panic all'avvio se l'upstream è irraggiungibile |
| Dedotto | Scadenza del 2026-10-09 innocua; Exited(2) da SIGTERM su PID 1; fallback senza blocco con mirror fermi; OOM mid-run in esperimenti lunghi; hostPort; mirror in hang con attese da 30 s o senza limite |
| Non verificato | Quale finestra del rate limit sia applicata (1 h o 6 h); se indice e figlio contino come 2 pull; 429 gestito su 2.8.3; mirror.gcr.io per i blob; pull anonimo da Aliyun; durata cold e warm dell'avvio con i 900 s; occupazione reale su kind; effetti reali di `docker network connect` su container fermi; pin per digest su tutti i sotto-chart; chiave per override memoria di Prometheus; correzione di #3816 in una release |

## Checklist di pre-flight in ordine

1. Stato dei mirror: `docker ps -a --format '{{.Names}} {{.Status}}'`. Tre registry devono essere `Up`. Se ghcr o quay sono `Exited`, eseguire `docker start kind-registry-ghcr kind-registry-quay` e non lanciare `setup-registry.sh`.
2. Salute dei mirror: `for p in 5001 5002 5003; do curl -s -o /dev/null -w "$p %{http_code}\n" localhost:$p/v2/; done` deve dare 200 per tutte e tre.
3. Cache e quota: `docker exec kind-registry du -sh /var/lib/registry` coerente (circa 1.9 GB); HEAD su `ratelimitpreview/test` con token anonimo e `ratelimit-remaining` non basso.
4. Versioni: non eseguire `helm repo update`; `helm search repo open-telemetry/opentelemetry-demo --versions` deve mostrare 0.42.1 come più recente nell'indice locale (opzione: pinnare `--version 0.42.1`).
5. Residui: `kind get clusters` vuoto, `helm list -A` senza `astronomy-shop`, `kubectl get ns astronomy-shop` assente; porte 8080, 9090, 16686, 16687 e 32000 libere.
6. Host: `sysctl fs.inotify.max_user_instances` (alzare a 512 se compaiono "too many open files"); `sysctl vm.max_map_count` almeno 262144.
7. Per gli scenari con injection: verificare che cartFailure usi una variante valida e che productCatalogFailure non sia tra gli scenari (B2); pianificare attesa maggiore di 10 minuti dopo l'iniezione.
8. Dopo l'avvio del cluster: confermare che i `hosts.toml` siano nei nodi (`docker exec <nodo> cat /etc/containerd/certs.d/ghcr.io/hosts.toml`) e che i log dei mirror mostrino `?ns=ghcr.io`, `?ns=quay.io`, `?ns=docker.io`.
9. Dopo il deploy: `helm list -o json` (`deployed`, chart 0.42.1), 30 pod pronti con `restartCount` 0, nessun `OOMKilled`, GET 200 su `frontend-proxy`, flagd senza errori, Jaeger e Prometheus con dati, Locust con richieste maggiori di zero; qualche minuto di warm-up prima di avviare l'agente.
10. Dopo il 2026-10-09 15:31 UTC: cercare `OnExpire` nei log dei registry e confrontare `du -sh`, per verificare che la scadenza sia davvero un no-op.

## Conclusione

La conclusione più utile è che il rischio principale non sta dove le note iniziali lo cercavano. La cache Docker Hub è calda, la quota non è stata mai vicina all'esaurimento, e la scadenza di 7 giorni, che sembrava una bomba a orologeria, è con ogni probabilità un no-op su 2.8.3. I punti deboli sono tre: un comando (`setup-registry.sh`) che cancella cache in silenzio, un controllo di avvio che non distingue un'app funzionante da un'app avviata, e flag di fault injection che il chart 0.42.1 non accetta o ignora. Per la tesi, la riproducibilità dipende più da questi punti, e dal pin di chart e digest, che dalla capacità dei registry.

Resta una verifica empirica che nessuna nota ha potuto fare, perché non esisteva un cluster. Bisogna misurare i tempi di avvio a freddo e a caldo contro i 900 s di `wait_for_ready`, e osservare il comportamento reale di containerd con un mirror fermo.
