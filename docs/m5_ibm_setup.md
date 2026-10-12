# M5 — lokale Bereitschaft, IBM-Konfiguration und bewusste Freigabe

## Was die aktuelle Implementierungsrunde erlaubt

Zuerst wird M5 vollständig lokal geprüft. Die Standardvorbereitung benutzt
ein ausdrücklich synthetisches 53-Draht-Test-Target mit dem festen Bereich
`[1, 4, 9, 13, 22, 31, 42]`; sie ist keine IBM-Hardwaremessung. Es werden
16 eingefrorene M4-QAOA-Schaltungen, acht Startkontrollen und vier
Smoke-Präparationen aufgebaut. Weder Winkeltraining noch Relaxation wird
wiederholt. Das wissenschaftliche Protokoll steht in
[m5_experiment_protocol.md](m5_experiment_protocol.md).

Die Konfigurationsflags bleiben `hardware_enabled=false` und
`submission_approved=false`. Diese Anleitung erteilt keine Hardwarefreigabe.
Ein echtes Submit benötigt später Florians Zustimmung zum konkreten gehashten
Plan. Fehlende IBM-Zugangsdaten verhindern weder lokale Tests noch
`OFFLINE_READY`.

## Gepinnter Stack und geprüfte Dokumentationsstände

| Paket | verbindliche Version |
| --- | --- |
| Python | 3.12 |
| Qiskit | 2.5.2 |
| Qiskit Aer | 0.17.2 |
| qiskit-ibm-runtime | 0.47.0 |

Die hier verlinkten offiziellen API-/Guideseiten wurden für diesen
Implementierungsstand am 12. Oktober 2026 geprüft; vor späterer Hardwarefreigabe
werden aktuelle Konto- und Servicebedingungen erneut gelesen.

`pyproject.toml` und `uv.lock` sind maßgeblich. Der vorhandene Qiskit-/Aer-Stack
wird nicht unkontrolliert aktualisiert. Die Runtime-Erweiterung wird gegen
den Pin geprüft. `SamplerV2(mode=backend)` ist die 0.47-Schnittstelle für
den Job-Modus; eine offene Session oder Batch ist für M5 ausgeschlossen.
Neuere Tutorialbeispiele für Executor ab 0.50.0 werden nicht in diesen
funktionierenden Stack übernommen.
[IBM: SamplerV2 0.47](https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/0.47/sampler-v2),
[Runtime 0.47.0: Primitive-Implementierung](https://raw.githubusercontent.com/Qiskit/qiskit-ibm-runtime/0.47.0/qiskit_ibm_runtime/base_primitive.py)

Die generischen IBM-Seiten können spätere Beispiele enthalten. Für Optionen
sind die versionierten 0.47-Seiten verbindlich. Die generische Aer-Referenz
wird zusätzlich gegen den exakten 0.17.2-Quellstand und die installierten
Offline-Contracttests geprüft.
[IBM: SamplerOptions 0.47](https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/0.47/options-sampler-options),
[Aer 0.17.2: NoiseModel-Quellstand](https://raw.githubusercontent.com/Qiskit/qiskit-aer/0.17.2/qiskit_aer/noise/noise_model.py)

## M5A — ohne Konto und ohne Netzwerk prüfen

Die folgenden Aufrufe laufen aus dem Repositorywurzelverzeichnis mit einer
bereits eingerichteten, gesperrten Umgebung. `--offline` verhindert einen
Dependency-Netzwerkabruf; fehlende Pakete werden nicht durch eine stille
Aktualisierung ersetzt.

```powershell
uv run --locked --offline km-quantum m5 --help
uv run --locked --offline km-quantum m5 prepare --root . --output results/m5 --config configs/m5_baseline.json
uv run --locked --offline km-quantum m5 verify-local --root . --output results/m5
uv run --locked --offline km-quantum m5 plan --root . --output results/m5
uv run --locked --offline km-quantum m5 acceptance --root . --output results/m5
```

Alternativ kann der bereits installierte Einstieg aus `.venv` benutzt werden;
die ausführbaren Befehle und ihre `--help`-Ausgabe bleiben identisch. Die
Offlinevorbereitung benötigt keine Tokens, Kontodiscovery oder Backendabrufe.
Ihre Artefakte und Vorhersagen sind mit `offline_synthetic` gekennzeichnet.
Der Plan ist ein unbestätigter Vorschlag und kein submitfähiger Hardwareplan.
`analyze` kann ohne echte Rohdaten keinen Hardwarebericht erzeugen.

`verify-local` prüft den eingefrorenen Inhalt, die idealen M4-Verteilungen,
native ideale Parität, Energie einschließlich Offset, vier Metriken und
QPY-Roundtrips. Wahrscheinlichkeits-/Metriktoleranz ist `1e-10`, Energietoleranz
`1e-8`. Ein Simulatorlauf beweist keine Runtime-Serverwirkung und keinen
bereits auf Hardware validierten Pfad.

Resume lädt versiegelte Artefakte und erhält vorhandene QPY-Originalbytes
und SHA. Wiederholte QPY-Serialisierung ist kein kanonischer Bytegleichheitstest;
geladenes Verhalten, Struktur, Parameter und Mapping sind zusätzlich geprüft.
Eine geänderte Rohdatei darf nicht durch neue Checksummen legitimiert werden.
[IBM: QPY](https://quantum.cloud.ibm.com/docs/en/guides/save-circuits)

## M5B — Konto ausschließlich durch den Nutzer einrichten

Florian wählt in IBM Quantum Platform eine vorhandene, ausdrücklich kostenlose
Open-Plan-Instanz und einen tatsächlich zugänglichen operationalen Backend.
Es gibt keinen festen realen Backendnamen in dieser Anleitung. Ein optional
verfügbares Kontingent oder eine Werbeaktion wird nicht vorausgesetzt. Das
aktuelle Konto und seine Restnutzung sind entscheidend. Der M5-Prozess legt
keine Accounts oder kostenpflichtigen Instanzen an, kauft keine Zeit und ändert
kein Billing, keine Zugriffspolitik und keine anderen Projekte.
[IBM: Zugangspläne](https://quantum.cloud.ibm.com/docs/en/guides/plans-overview)

Credentials werden über einen **explizit benannten vorhandenen lokalen
Account-Eintrag** oder sichere lokale Umgebungsvariablen bereitgestellt.
Keine Tokens gehören in Shellbefehle dieses Dokuments, Chat, Commits,
Screenshots, Trace-Ausgaben oder öffentliche Fehlermeldungen. Ein Secret wird
weder interaktiv echoend abgefragt noch durch einen Accountdump kontrolliert.

Für den dokumentierten Dateipfad legt der Nutzer ausschließlich lokal unter
`.m5-local/provider.json` diese Bindungsdaten an:

```json
{
  "instance": "<explizite lokale Open-Plan-Instanz-CRN>",
  "backend": "<expliziter zugänglicher operationaler Backend>",
  "account_name": "<Name des vorhandenen lokalen Account-Eintrags>"
}
```

Die Platzhalter sind aus lokalen Kontoinformationen zu ersetzen und werden
nicht im Chat geteilt. Die Datei wird ignoriert und enthält kein Tokenfeld
oder vollständiges Kontoobjekt. Es wird genau ein Credentials-Ursprung
gewählt. Die zugrunde liegende Umgebungs-Konfiguration kennt
`KM_QUANTUM_IBM_INSTANCE`, `KM_QUANTUM_IBM_BACKEND`,
`KM_QUANTUM_IBM_ACCOUNT_NAME` oder alternativ `KM_QUANTUM_IBM_TOKEN`.
Ein Token wird ausschließlich außerhalb dieses dokumentierten JSON und
außerhalb öffentlicher Logs gesetzt. Der explizite Instanzparameter verhindert
eine stille Auswahl einer anderen Default- oder Paid-Instanz.

Bei reinem Umgebungszugang entfällt `--provider-config` in den späteren
Providerkommandos. Dann müssen Instanz und Backend sowie genau einer der beiden
Credentials-Ursprünge bereits in der lokalen Umgebung verfügbar sein; es wird
kein Token als Kommandoargument übergeben. Die Datei-/Umgebungswahl darf die
Instanzbindung des freigegebenen Plans nicht ändern.

Öffentliche Artefakte enthalten ein SHA-basiertes pseudonymes Instanzmerkmal;
die tatsächliche CRN bleibt lokal. Repositoryschutz ist kein Ersatz für den
sorgsamen Umgang mit lokalen Dateien und Umgebungsvariablen.

## Lesender Preflight und tatsächlicher Hardwareplan

Erst nach der lokalen Konfiguration wird dieses explizite Onlinekommando
aufgerufen. Es liest Provider-/Backendmetadaten und kompiliert lokal für das
tatsächliche Target. Es startet keinen Smoke- oder Hauptjob.

```powershell
uv run --locked --offline km-quantum m5 provider-preflight --root . --output results/m5-provider --provider-config .m5-local/provider.json --usage-estimates .m5-local/usage_estimates.json
uv run --locked --offline km-quantum m5 verify-local --root . --output results/m5-provider
uv run --locked --offline km-quantum m5 plan --root . --output results/m5-provider
uv run --locked --offline km-quantum m5 acceptance --root . --output results/m5-provider
```

Hier bezieht sich `uv --offline` ausschließlich auf Dependencyauflösung.
`provider-preflight` ist bewusst ein Netzwerkkommando. `results/m5-provider`
trennt den konkreten Hardwarestand von der lokalen synthetischen Fixture.
Das Backend und sein zusammenhängender sieben Qubit großer Bereich werden
anhand der vorab festgelegten Bereichsregel ausgewählt: Start-IDs aufsteigend,
BFS mit aufsteigend sortierten Nachbarn, erster BFS-Präfix mit sieben
verbundenen IDs. Geeignete Zwei-Qubit-Targetverbindungen müssen
endliche Dauern und Gatefehler haben; vollständige Gate-/Readout-/T1-/T2-
Abdeckung wird zusätzlich verlangt und kann den konkreten Pfad blockieren.
Reale physische IDs dürfen von den Offline-Fixture-IDs abweichen. Für alle
Schaltungen gleicher Breite gilt dasselbe Initiallayoutpräfix; Routing,
Messmapping, Snapshot und Noise-Modell werden gemeinsam eingefroren.

Der Preflight muss eine passende Instanz, `plan=open`, `pricing_type=free`,
0 EUR, echten operationalen QPU-Backend, aktuelle Nutzung und Restquote,
erlaubte Shots/PUBs/Jobs sowie dokumentierte jobbezogene QPU-Schätzungen
belegen. Vor Submit werden diese Angaben erneut frisch geprüft; eine alte
Erreichbarkeitsprüfung ist keine spätere Budgetgarantie. Die Implementation
fordert derzeit höchstens fünf Minuten alte Preflight- und Nutzungsdaten.

`--usage-estimates` ist ein optionaler expliziter Dateieingang für bereits
extern überprüfte QPU-Schätzungen. Er wird nur angegeben, wenn diese Fakten
tatsächlich lokal vorliegen. Das oben gezeigte Kommando setzt deshalb eine
entsprechende `.m5-local/usage_estimates.json` voraus:

```json
{
  "smoke": {"seconds": null, "source": null},
  "main-block-1": {"seconds": null, "source": null},
  "main-block-2": {"seconds": null, "source": null}
}
```

`null` kennzeichnet **fehlende Fakten und besteht das Preflightgate nicht**.
Nur tatsächlich verfügbare nichtnegative, mit Quelle überprüfte Sekundenwerte
und ihre nachvollziehbare Quellenbeschreibung werden eingesetzt. Ohne diesen
optionalen Eingang bleiben fehlende Schätzungen unbekannt und blockieren
`READY_FOR_APPROVAL`. Es wird kein pauschaler Overhead oder Nutzungsstand
erfunden, nur um ein Gate zu bestehen. Eine Änderung der dokumentierten
Schätzung darf ein Budget enger blockieren und es nicht still erhöhen.

Fehlende oder uneindeutige API-Angaben ergeben `OWNER_CONFIGURATION_REQUIRED`.
Dann bleibt der konkrete Onlinepfad blockiert: keine erfundene Restquote,
kein pauschal angenommenes Providerlimit und kein Paid-Fallback. Der Nutzer
prüft aktuelle Instanz- und Nutzungsinformationen lokal und klärt die fehlenden
Fakten, bevor ein neuer konkreter Plan freigegeben wird. M5A bleibt gültig.
Eine fehlende notwendige Gate-/Readoutkalibration blockiert den gerätebezogenen
Vergleich; sie wird nicht durch einen FakeBackend ersetzt.

## Exakte Laufzeit- und Optionsbindung

Der Vorschlag umfasst Smoke 4×128 Shots und zwei Hauptjobs mit je 24×1.024
Shots: 52 PUBs, 49.664 Shots, drei Jobs. Jeder Job reserviert 40 QPU-Sekunden,
insgesamt 120 Sekunden; Geldbudget ist 0 EUR. Diese Limits sind keine Garantie,
dass ein konkreter Backend den Versuch durchführen kann.

Die gepinnten Sampleroptionen lauten:

```json
{
  "max_execution_time": 40,
  "dynamical_decoupling": {"enable": false},
  "twirling": {"enable_gates": false, "enable_measure": false},
  "execution": {"init_qubits": true, "meas_type": "classified"}
}
```

Normaler Reset/Initialisierung ist damit ausdrücklich vorhanden. Rep-Delay
und Backenddefault werden im Preflight ausgewiesen; ein expliziter Delay
muss unterstützt, im zulässigen Bereich und im genehmigten Plan enthalten
sein. Es gibt kein `resilience_level` aus Estimatorbeispielen, keinen
Hardware-Samplerseed und keine unbekannten still ignorierten Optionen.
Die angeforderte Optionsliste und zurückgegebene Dienstmetadaten sind
unterschiedliche Belege.
[IBM: SamplerExecutionOptionsV2 0.47](https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/0.47/options-sampler-execution-options-v2),
[IBM: TwirlingOptions 0.47](https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/0.47/options-twirling-options)

`max_execution_time=40` begrenzt die angeforderte QPU-Nutzung in Sekunden,
keine Queue- oder Client-Wandzeit. SDK-Lese-/Verbindungszeitlimits verfolgen
einen anderen Zweck. Lokales Reservierungsledger und serverseitige Obergrenze
schaffen keine perfekte technische Eurogarantie. Interne kostenwirksame
Mehrjob-Ausführung oder nicht abgedeckte Expansion verhindert die Einreichung;
Provider-Aufteilung und Overhead müssen in der Schätzung berücksichtigt sein.
Bereits verbrauchte oder in ihrem Ausgang unklare Jobs werden nicht als
kostenloser neuer Versuch behandelt.
[IBM: Laufzeitgrenzen](https://quantum.cloud.ibm.com/docs/en/guides/max-execution-time),
[IBM: Nutzungsdefinition und Schätzungen](https://quantum.cloud.ibm.com/docs/en/guides/estimate-job-run-time)

Lokale Runtime-Fake-/Aer-Tests unter 0.47 können zusätzliche Runtimeoptionen
ignorieren. Ihre erfolgreiche Ausführung belegt deshalb weder wirksame
serverseitige Optionen noch eine vollständig deaktivierte physische
Gerätekontrolle. Offline-API-Contracttests prüfen Konstruktion und Schema;
später verfügbare Providerinformationen müssen separat erhalten bleiben.
[IBM: Grenzen lokalen Testens](https://quantum.cloud.ibm.com/docs/en/guides/local-testing-mode)

## Menschliche Freigabe des konkreten Plans

`plan` schreibt einen unbestätigten Plan und `approval_request.json`.
Florian prüft genau diesen Stand: Manifest und QPY-Rohhashes, Instanzbindung,
Backend, sieben physische IDs, Mapping, Ablaufzeit, PUB-Reihenfolge, Shotzahlen,
drei Jobs, Limits, Schätzung/Spielraum und Optionen. Der Agent kann den Entwurf
nicht selbst bestätigen. Eine Freigabe gilt nicht pauschal für andere Backends,
mehr Shots, zusätzliche Jobs oder später geänderte Schaltungen.

Der Nutzer legt die geprüfte Freigabe ausschließlich lokal als
`.m5-local/approval.json` ab. Eine gültige menschliche Datei bindet den
exakten `plan_hash` und die unveränderten Planfelder und enthält
`approved=true`, `signed_by="Florian"`, einen expliziten UTC-`signed_at` und
eine noch gültige `expires_at`. Der generierte Entwurf enthält diese
Bestätigung nicht. Die konkrete Freigabephase beginnt erst nach erfolgreichem
Hardwarepreflight und `READY_FOR_APPROVAL`; ein Offline-Testplan ist nicht
freigabefähig für die QPU.

## Erst nach Freigabe: genau einen Job einreichen und bestehende IDs abholen

Die nachfolgenden Befehle sind die spätere Bedienung und werden in der
aktuellen Implementierungsrunde nicht auf echte Hardware angewandt:

```powershell
uv run --locked --offline km-quantum m5 submit-next --root . --output results/m5-provider --provider-config .m5-local/provider.json --approval .m5-local/approval.json
uv run --locked --offline km-quantum m5 collect --root . --output results/m5-provider --provider-config .m5-local/provider.json
```

`submit-next` reicht pro explizitem Aufruf ausschließlich den nächsten
genehmigten Job ein. Reihenfolge ist Smoke, Hauptblock 1, Hauptblock 2.
Der Vorgänger muss abgeholt und sein Datenvertrag geprüft sein, bevor der
nächste Job zulässig ist. Schlechte Lösungswahrscheinlichkeiten sind kein
Grund, zusätzliche Messungen zu beginnen. Es wird niemals ein automatischer
Retry oder Ersatzjob angelegt.

Das Ledger reserviert den vollen 40-Sekunden-Betrag atomar vor dem Request,
bindet lokale Job-/PUB-IDs und persistiert die Provider-ID sofort nach
Erhalt. Bei Timeout, Prozessabbruch oder unklarer Annahme bleibt
`SUBMISSION_UNKNOWN` mit voller Reservierung bestehen. Nicht erneut submitten.
Bekannte IDs werden lesend abgeglichen; ohne eindeutig zuordenbare ID bleibt
die Sache zur manuellen Klärung offen. Ein stale Ledgerlock wird ebenfalls
nicht automatisch entfernt und durch eine neue Einreichung ersetzt.

`collect` liest nur bestehende IDs, ruft Ergebnisse nur für abgeschlossene
Jobs ab und betreibt kein endloses Polling. Wiederholtes Abholen oder Resume
ist keine Einreichung. Daten-/PUB-Fehler, negative Counts, unvollständige
Shot-Summen, fehlende Jobs und Providerfehler bleiben sichtbar. Primärdaten
sind rohe nichtnegative Counts mit vollständiger Shot-Summe, ohne
Readoutkorrektur und ohne Aussortieren ungültiger Pläne.

## Analyse, Grenzen und Abschluss

Erst vorhandene echte Ergebnisse erlauben eine Hardwareanalyse:

```powershell
uv run --locked --offline km-quantum m5 analyze --root . --output results/m5-provider
uv run --locked --offline km-quantum m5 acceptance --root . --output results/m5-provider
```

Berichte erhalten Hauptblöcke getrennt, vier unveränderte Planmetriken,
Offsetenergie, beste gültige Sample-Planung und Gap, vollständige Histogramme,
95-%-Intervalle und vorab festgelegte Multinomial-TV-Referenzbereiche mit
1.000 Ziehungen und Seed `515151`. Eine empirische Abweichung ist ein
Messbefund und kein automatischer Softwarefehler. Null Treffer belegen keine
Nullwahrscheinlichkeit. Histogrammdistanz ist keine Zustandsfidelity.

Queue, lokale Rechenzeit und tatsächliche QPU-Nutzung werden getrennt
berichtet. Die Noise-Vorhersage deckt nicht alle Idle-Zeiten, Crosstalk oder
Drift ab. Fehlende Kalibration und symmetrische Readoutapproximationen werden
benannt. Zwei wiederholte Blöcke sind weder unabhängige Problemstichproben
noch ein belastbarer Langzeitnachweis. Die kleine bekannte Suite und ein
einziger Trainingsseed tragen keinen allgemeinen Warm-Start-Vorteil,
Kostenvergleich zur klassischen Optimierung, Beschleunigungsnachweis oder
nachgewiesenen Kundennutzen.

Vor echten Daten bleiben `hardware_used=false`, Hardwarejobs null beziehungsweise
ausstehend und beobachtete Hardwareübereinstimmung unbekannt. Gesamt-M5-PASS
erfordert gültige Freigabe, alle realen Pflichtmessungen, intakte Rohdaten,
technische Gates und fertige Analyse. Ein Test-/Mockbericht darf diesen
Status nie ersetzen. Es erfolgt kein automatischer Push, Merge oder
Produktionszugriff.
