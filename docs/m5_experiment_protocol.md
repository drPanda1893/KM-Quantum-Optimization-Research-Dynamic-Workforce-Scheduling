# M5 — IBM-Hardwarevergleich eingefrorener QAOA-Schaltungen

## Forschungsfrage und Stand dieses Protokolls

M5 vergleicht für **dieselbe vorab festgelegte Schaltung** vier Ebenen:
die ursprüngliche ideale M4-Verteilung, die ideal simulierte native
Hardwaredarstellung, eine approximative gerätebezogene Noise-Vorhersage und
später tatsächlich erhobene IBM-QPU-Counts. Primär interessiert die Abweichung
realer Registerverteilungen und unveränderter Planungskennzahlen von diesen
Vorhersagen.

Dieses Dokument legt den Versuch vor Ergebnissichtung fest. Es enthält keine
Hardwaremessungen, keinen abgeschlossenen Hardwarebericht und keine Freigabe.
Ein lokaler Simulator, ein synthetisches Target, ein FakeBackend und ein
lesender Providerabruf liefern keinen M5-Hardware-PASS. Ohne reale Daten ist
der höchste lokale Status `OFFLINE_READY`; ein konkret geprüfter, weiterhin
unbestätigter Hardwareplan kann `READY_FOR_APPROVAL` erreichen.

Es gibt kein neues Training, keine Hardwareoptimierung, keine neuen
Restartseeds, keine Auswahl guter Winkel, kein Penalty- oder epsilon-Tuning,
keine Decoderreparatur, keine Postselection und keine Error Mitigation.
M5 ist ein kleiner Transfer- und Validierungsversuch. Quantum Advantage,
Beschleunigung, Kundenrepräsentativität oder ein neuer allgemeiner
Wirksamkeitsnachweis für Warm-Start sind keine Schlussfolgerungen dieses Plans.

## Eingefrorene Referenzen und historische Integrität

Ausgangspunkt ist der veröffentlichte M4-Stand
`4ca11bf8f709aef9f43df4d3f2a0867189b54a7c` auf
`feat/m4-noise-warm-start`. Der M5-Branch heißt
`feat/m5-ibm-hardware-validation`. Der zuvor nur lokal genannte Kurzcommit
`440c7c9` ist kein Ersatz für die aufgelöste veröffentlichte SHA.

M4s Angaben zu 240 idealen Trainingsläufen, 1.200 Noise-Auswertungen,
1.094 Tests und 2.107 historisch geschützten Dateien sind historische
Referenzangaben. Neue M5-Testzahlen und Nachprüfungen werden separat
ausgewiesen. M4 verwendete synthetische Fehlerprofile ohne konkrete
Hardwaretopologie und dokumentierte keinen breiten Warm-Start-Vorteil.

M1-Modell, Validator und Objective, M2-Variablenmapping, QUBO/Ising einschließlich
Offset und vollständige Ground-Sets bleiben die maßgeblichen Definitionen.
M3/M4-Protokolle, Acceptance und bestehende Rohartefakte werden weder geändert
noch durch neue Resultate überschrieben. Vor und nach M5 werden Inhaltshashes
geprüft. Git-bedingte CRLF/LF-Unterschiede in Texten werden zusätzlich anhand
ausdrücklich ausgewiesener LF-Hashes diagnostiziert; Originalbytehashes bleiben
sichtbar. QPY, Archive und andere Binärdaten werden niemals normalisiert.
Abweichende Checksummen werden nicht neu eingefroren, um ein Gate zu bestehen.

Die Rekonstruktion liest vorhandene Dateien unter `results/m4/training/`,
`results/m4/preparations/` und `results/m4/controls/`. Tatsächliche
Parameterobjekte werden anhand der gespeicherten `parameter_order`,
`final_parameters` und `parameter_bindings` gebunden. Vorhandenes `relaxation.y`
und `epsilon=0.25` werden unverändert verwendet. Kein Optimizer und kein
Relaxationssolver wird aufgerufen. Die idealen Endverteilungen werden gegen
die gespeicherten `ideal_probabilities` beziehungsweise Kontrollverteilungen
reproduziert, einschließlich Energie und aller vier Wahrscheinlichkeitsmetriken.

## Vorab ausgewählte bekannte Diagnosesuite

| Referenz | Logische M2-Qubits | Vorab benannter Diagnosezweck |
| --- | ---: | --- |
| `A_single_job` | 2 | einfache Zuordnung |
| `E_overlap` | 4 | überlappende Aufträge und Konflikte |
| `H_workload` | 5 | Kapazität und Workload-Hilfsbits |
| `K_bounded_slack` | 7 | bounded Slack und bekannter Problemfall |

Alle vier Fälle waren bereits bekannt. Die Auswahl ist eine strukturorientierte
Hardware-Diagnosesuite, kein unbekannter Hold-out, keine nachträglich verblindete
Studie und keine repräsentative Stichprobe von Kundenproblemen. K ist bewusst
wegen seiner bekannten Schwäche enthalten. Schlechte gespeicherte Parameter
werden ebenso übertragen wie gute.

Je Fall werden Standard-QAOA und Warm-Start-QAOA bei `p=1` und `p=2` ausschließlich
mit dem gespeicherten M4-Restartseed `0` verwendet: **16 QAOA-Schaltungen**.
Je Fall kommen ein uniformer Start und der eingefrorene Warm-Produktstart
ohne QAOA-Schichten hinzu: **8 physisch zu messende Kontrollen**. Diese Starts
sind klassisch samplebar; ein Hardware-Warm-Produktstart ist kein zusätzlicher
quantenalgorithmischer Nutzen gegenüber Bernoulli-Sampling.

## Messblöcke, Reihenfolge und festes Budget

| Job | PUBs | Shots je PUB | Shots insgesamt | reservierte QPU-Obergrenze |
| --- | ---: | ---: | ---: | ---: |
| Smoke | 4 | 128 | 512 | 40 s |
| Hauptblock 1 | 24 | 1.024 | 24.576 | 40 s |
| Hauptblock 2 | 24 | 1.024 | 24.576 | 40 s |
| Gesamt | **52** | — | **49.664** | **120 s** |

Die Hauptmessung umfasst 48 PUBs und 49.152 Shots. Beide Blöcke verwenden
dieselben 24 Schaltungen. Sie sind keine unabhängigen neuen Planungsprobleme
und keine belastbare Langzeitstabilitäts- oder Driftstudie.

Die Hauptreihenfolge wird vor Einreichung mit `pub_order_seed=20261012` festgelegt.
Standard/Warm-Paare desselben Falls und derselben Tiefe bleiben benachbart;
der zweite Block kehrt die vorab festgelegte Reihenfolge kontrolliert um.
Die persistenten PUB-IDs und ihre Positionen stehen im Plan. Diese Anordnung
soll zeitliche Verzerrungen reduzieren, garantiert aber keine bestimmte
Providerverarbeitung oder Driftfreiheit. Tatsächliche zeitliche Beziehungen
werden nur bei verfügbaren Providerzeitstempeln berichtet.

Der Smoke-Job präpariert im sieben Bit breiten relevanten Register die M2-Muster
`0000000`, `1111111`, `1001010` und `0110001`. Die letzten beiden sind verschieden
und asymmetrisch. Die Kontrolle dient Mapping und Auslesediagnose; sie ist keine
vollständige Readout-Kalibration oder Tomographie. Schlechte Häufigkeiten allein
beweisen keinen Mappingfehler. Ein nachweisbarer Daten- oder Vertragsfehler
blockiert Hauptjobs; geringe Lösungsqualität tut dies nicht.

Es gelten höchstens drei Providerjobs, 40 angeforderte QPU-Sekunden je Job,
120 Sekunden insgesamt und **0 EUR ausschließlich auf einem verifiziert
kostenlosen Open Plan**. Die Zeitlimits sind Schutzgrenzen, keine Vorhersage,
dass die Messung innerhalb dieser Grenzen gelingt. Kontoquote, dokumentierte
Schätzung und Spielraum müssen vor Einreichung zusammenpassen. Keine zusätzlichen
Kalibrationsjobs, Wiederholungen, Ersatzmessungen oder automatischen Retrys sind
genehmigt. Erforderliche Planänderungen erfolgen vor Einreichung mit neuer
Planversion und erneuter menschlicher Freigabe.

## Backend, physischer Bereich und Compiler

Die lokale Standardvorbereitung verwendet ausschließlich
`m5_offline_synthetic_target`: ein deterministisches **Test-Target mit 53
Gerätedrähten**, gerichteten ECR-Kopplungen und künstlichen Kalibrationswerten.
Der feste Testbereich ist `[1, 4, 9, 13, 22, 31, 42]`. Sein Kalibrationsdatum
`2000-01-01T00:00:00+00:00` ist ein Test-Fixture-Zeitpunkt und keine reale
IBM-Kalibration. Die Fixture kann keine Providerjobs ausführen.

Ein konkreter Hardwareplan benötigt einen explizit gewählten, aktuell
zugänglichen, operationalen echten IBM-Backend und einen verbundenen
sieben Qubit großen Bereich. Auswahlregel, physische IDs und initiales Layout
werden anhand von Topologie und verfügbaren Kalibrationsdaten dokumentiert und
vor Hardwareergebnissen eingefroren. Die vorab festgelegte Bereichsregel
prüft Start-IDs aufsteigend und besucht jeweils per BFS Nachbarn in
aufsteigender ID-Reihenfolge. Der erste BFS-Präfix mit sieben verbundenen
Qubits wird gewählt. Geeignete Verbindungen stammen ausschließlich aus
nativen Zwei-Qubit-Targeteinträgen mit endlichen Gatefehlern und Dauern;
die vollständige spätere Gate-/Readout-/T1-/T2-Abdeckung bleibt ein
zusätzliches blockierendes Gate. Es gibt keine fest codierte reale
Backendempfehlung, keine automatische Paid-Auswahl und keine Mehr-Backend-Kampagne.
Für eine Breite n verwenden Standard/Warm und Kontrollen dasselbe Präfix der
festen sieben physischen IDs. Routing ist auf den eingefrorenen Bereich beschränkt.

Ein Preset-Passmanager verwendet einheitlich `optimization_level=1`,
`seed_transpiler=424242` und `approximation_degree=1.0`. Instruktionen,
Parametergrenzen und gerichtete Kopplungen werden gegen das tatsächliche
Backend-`Target` geprüft; ECR oder CZ werden nicht als CX angenommen. Nicht
verwendete Control-flow-Features eines Targets verhindern keine statische
Kompilierung. M5 selbst verwendet keine dynamischen Schaltungen, manuellen
Pulskalibrationen oder neuen Gattervarianten. Die native Darstellung ist eine
neue M5-Darstellung und ersetzt kein historisches M4-Artefakt.
IBM beschreibt Target und Kopplungen als Grundlage der ISA-Prüfung und
Preset-Passmanager als lokalen Transpilationsweg.
[IBM: Transpilation](https://quantum.cloud.ibm.com/docs/en/guides/transpile)

Logische und native Schaltungen werden als QPY archiviert, mit Originalbyte-SHA,
QPY-/Qiskit-Version, geordneten Instruktionen und Parametern, Gatehistogramm,
Tiefe, Initial-/Finalmapping und Target-Snapshot. QPY ist kein versionsunabhängiges
oder kanonisch bytegleich serialisierendes Format. Resume prüft vorhandene
Originalbytes und lädt die ursprüngliche Datei; äquivalente Neuserialisierung
rechtfertigt keinen neuen Hash. Struktur, Parameter, Metadaten, Layout und
geladenes Messverhalten werden geprüft.
[IBM: QPY speichern und laden](https://quantum.cloud.ibm.com/docs/en/guides/save-circuits)

## Eine explizite Mappingkette und bounded Simulation

Die einzige Abbildungskette lautet:

`M2-Variable -> logisches Qubit -> physisches Qubit nach Routing -> klassisches m2-Bit -> M2-Bitvektor`.

Ein terminales klassisches Register `m2` hat genau ein Bit je M2-Variable.
Die Messungen werden vor Transpilation logisch eindeutig angebracht. Tatsächliche
native Messinstruktionen und die finale Layoutpermutation müssen übereinstimmen.
Das Rückmapping verwendet tatsächliche klassische Bitpositionen. M2 speichert
`x[0]...x[n-1]`; in Qiskit-Basisindices besitzt Variable i das Gewicht `2**i`.
Qiskits dargestellte Strings ordnen die klassischen Positionen anders an;
deshalb wird kein beliebiger String pauschal umgedreht.
[IBM: Bitordering](https://quantum.cloud.ibm.com/docs/en/guides/bit-ordering)

Rohcounts in Providerdarstellung und korrekt nach M2 decodierte Counts werden
getrennt erhalten. Tests erzwingen nichtidentische physische und klassische
Layouts, asymmetrische Muster, CX-Richtung, SWAP-/Routing-Permutationen,
unbenutzte Gerätedrähte und nichttriviale physische IDs. `measure_all()` nach
Ausweitung auf die Chipbreite ist ausgeschlossen.

Eine reine Umindizierung erstellt eine Simulationskopie **aller** tatsächlich
beteiligten Routing-, Gate- und Messdrähte. Unbenutzte 53-/128+-Gerätedrähte
lösen keine vollständige Chipsimulation aus. Aktive Breite einschließlich
Routingqubits ist höchstens zehn, vorzugsweise sieben; RAM höchstens 512 MiB
und Simulationsparallelität höchstens zwei Threads. Nichttriviale Rand- oder
Routingqubits werden nicht gelöscht. Ihre Marginalisierung muss dieselbe
gemessene Registerverteilung ergeben. Die Provider-Schaltung bleibt unverändert.

## Verbindliches lokales Äquivalenzgate

Für jede der 24 Hauptschaltungen werden Original-M4, logische Rekonstruktion,
native ideale Simulation und QPY-Roundtrip verglichen. Vorab festgelegte
absolute Toleranzen sind `1e-10` für vollständige Registerwahrscheinlichkeiten
und die vier Wahrscheinlichkeitsmetriken sowie `1e-8` für die vollständige
QUBO-Energie einschließlich Offset; kein relatives Ergebnis-Tuning.

Fehlende Parität blockiert Hardwareplanung und Einreichung. Sie ist ein
Darstellungs-, Mapping- oder Softwareproblem und darf nicht als Noise erklärt
werden. Dieses Gate verlangt ideale Kompilierungsäquivalenz. Es verlangt
keine Gleichheit späterer empirischer Hardwarecounts mit exakten Verteilungen.

## Snapshot, gerätebezogene Vorhersage und Readout genau einmal

Vor Hardwaremessungen werden Target, erlaubte Instruktionen und Richtungen,
relevante Gate-Dauern/-Fehler, Readout, T1/T2 und verfügbare Frequenzen sowie
Abruf- und verfügbare Kalibrationszeitstempel gespeichert. Fehlende Daten
bleiben explizit fehlend. Öffentliche Snapshots exportieren keine Credentials
oder vollständigen Kontokonfigurationen. Synthetische, Fake- und Aer-Daten
tragen einen Offline-Artefakttyp und werden niemals als echte Kalibration
oder Hardwaremessung ausgegeben.

Die Implementation nutzt Aers `NoiseModel.from_backend` und remappt die
qubitspezifischen, gerichteten Kanalattachments und Readout-Matrizen in exakt
dieselbe kompakte physische Darstellung. Die Abdeckung wird je tatsächlich
ausgeführtem Gatevorkommen geprüft. Fehlende benötigte Kalibrationswerte
blockieren den konkreten gerätebezogenen Vergleich; es gibt keinen stillen
Fallback auf perfekte Hardware oder ein beliebiges FakeBackend. Auf
Backend-Fehler wird kein zusätzliches pauschales M4-Depolarisationsprofil addiert.
Die Aer-API beschreibt dieses Modell ausdrücklich als approximativ.
[Aer: NoiseModel](https://qiskit.github.io/qiskit-aer/stubs/qiskit_aer.noise.NoiseModel.html),
[Aer 0.17.2: exakter API-Quellstand](https://raw.githubusercontent.com/Qiskit/qiskit-aer/0.17.2/qiskit_aer/noise/noise_model.py)

Dichtematrix vor Messung, Born-Wahrscheinlichkeiten vor Readout und beobachtete
Verteilung nach Readout sind getrennte Ebenen. Analytischer Readout wird genau
einmal angewandt; ein Aer-Samplingpfad mit bereits angewandtem Readout erhält
keinen zweiten Durchlauf. Das primäre Hardwarehistogramm bleibt unkorrigiert.

T1/T2-Kanäle während Gates modellieren keine vollständigen Idle-Zeiten,
Crosstalk, Drift oder alle Gerätewirkungen. Der aktuelle statische Pfad
modelliert keine gesonderten Idle-/Delay-Effekte und lehnt explizite Delays ab,
wenn deren Kanalabdeckung nicht nachgewiesen ist. Symmetrisch aus mittlerem
Readoutfehler abgeleitete Matrizen werden als solche gekennzeichnet.
Vorhersagen werden vor den zugehörigen Messresultaten gehasht und eingefroren.
Spätere passendere Kalibrationsmodelle wären getrennte explorative Analysen.

## Providervertrag, Freigabe und sichere Fortsetzung

Der geprüfte Stack ist Qiskit `2.5.2`, Aer `0.17.2` und
`qiskit-ibm-runtime==0.47.0`. M5 verwendet `SamplerV2(mode=backend)` im Job-Modus,
keine Session, Batch, V1-Primitiven oder zusätzliche Estimatorjobs. Der diagonal
in Z messbare Hamiltonian wird aus den terminalen Counts ausgewertet. Aktuelle
Tutorials für Executor ab Runtime 0.50.0 sind keine API-Vorgabe für diesen Pin.
[IBM: SamplerV2 0.47](https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/0.47/sampler-v2)

Angefordert werden explizit `max_execution_time=40`,
`dynamical_decoupling.enable=false`, `twirling.enable_gates=false`,
`twirling.enable_measure=false`, `execution.init_qubits=true` und
`execution.meas_type="classified"`. Rep-Delay und Backenddefault werden
dokumentiert; ein veränderter Delay wird nicht zur Ergebnisverbesserung gesucht.
Es gibt kein erfundenes Sampler-`resilience_level` und keinen Hardware-Samplerseed.
Angeforderte Optionen und tatsächlich bestätigte Providermetadaten bleiben
getrennt. Im lokalen Testmodus der gepinnten serverseitigen Primitiven können
Optionen ignoriert werden; Mock-Erfolg beweist keine Wirkung auf dem Dienst.
[IBM: SamplerOptions 0.47](https://quantum.cloud.ibm.com/docs/en/api/qiskit-ibm-runtime/0.47/options-sampler-options),
[IBM: lokaler Testmodus](https://quantum.cloud.ibm.com/docs/en/guides/local-testing-mode)

`max_execution_time` betrifft QPU-Nutzung, nicht Queue oder Client-Wandzeit.
Eine Obergrenze allein garantiert keine perfekten Eurokosten. Plan, kostenlose
Instanz, aktuelle Nutzungsdaten, operationaler Backend, Shots-/PUB-/Joblimits,
Schätzungen und vollständige Reservierung werden vor Einreichung geprüft.
Jobbezogene Usage-Schätzungen werden nicht aus erfundenem Overhead ergänzt:
ein explizites `--usage-estimates`-Artefakt kann lokal extern überprüfte
Schätzungen mit `seconds` und nachvollziehbarer `source` bereitstellen.
Unbekannte Quoten, Kosten, Schätzungen oder interne kostenwirksame Expansion blockieren die
Einreichung als `OWNER_CONFIGURATION_REQUIRED` beziehungsweise `BLOCKED`.
[IBM: maximale Ausführungszeit](https://quantum.cloud.ibm.com/docs/en/guides/max-execution-time),
[IBM: Nutzung und Schätzung](https://quantum.cloud.ibm.com/docs/en/guides/estimate-job-run-time)

Die Freigabe bindet Manifest-/QPY-Hashes, pseudonymes Instanzmerkmal, Backend,
Layout, Ablaufdatum, alle PUBs/Shots, drei Jobs, Budget und Runtimeoptionen.
Der Entwurf bleibt `approved=false`, `hardware_enabled=false` und
`submission_approved=false`. Nur Florian kann den konkreten Plan ausdrücklich
freigeben. Änderungen an gebundenen Inhalten machen diese Freigabe ungültig.
Eine spätere Quota-/Operationalitätsprüfung darf enger blockieren und niemals
still erweitern. Fehlende Zugangsdaten blockieren nur den Online-Schritt.

Vor jedem Submit wird der volle genehmigte Betrag atomar im dateibasierten
Ledger reserviert; dann wird genau der nächste Job eingereicht und seine
Provider-ID sofort persistiert. SDK-POST-Retrys werden für den geprüften
Transport deaktiviert. Bei möglicherweise angenommenem Request ohne sichere
Antwort gilt `SUBMISSION_UNKNOWN`: volle Reservierung bleibt erhalten, keine
automatische Doppeleinreichung. Lokale IDs/Tags und lesender Providerabgleich
helfen bei manueller Klärung. Ein laufender, fehlgeschlagener oder unklarer
Vorgänger erlaubt keinen automatischen nächsten Job. `collect` liest bekannte
IDs einmal pro Aufruf; Resume reicht nichts erneut ein.

## Metriken und vorab festgelegte Statistik

Alle vier Wahrscheinlichkeiten behalten ihre M4-Semantik:

| Metrik | Ereignis |
| --- | --- |
| `p_schedule_optimal` | literal decodierter, M1-gültiger optimaler Plan |
| `p_ground_encoding` | vollständiges M2-Ground-Encoding einschließlich Hilfsbits |
| `p_schedule_feasible` | M1-gültiger Plan nach literalem Decoding |
| `p_encoding_feasible` | vollständiges zulässiges Encoding |

Zusätzlich werden vollständige QUBO-Energie mit Offset, beste gültige
Sample-Planung und Objective-Gap, komplette rohe Histogramme und Anzahl
verschiedener decodierter Pläne berichtet. Ungültige Plan-Objectives bleiben
`null`; ihre vollständige QUBO-Energie ist trotzdem berechenbar. Hilfsbit-
Unterschiede bleiben sichtbar, auch wenn mehrere Bitstrings denselben Plan
decodieren. Kontrollen verwenden denselben M1/M2-Evaluator.

Hauptblöcke werden zunächst getrennt ausgewertet. Deskriptives Pooling erhält
beide Einzelresultate und dokumentiert mögliche zeitliche Abhängigkeit.
Wahrscheinlichkeitsdifferenzen werden absolut in Prozentpunkten gegen ideale
und modellierte Vorhersage angegeben. Total-Variation-Distanz betrifft
Registerhistogramme und ist keine Quantenzustandsfidelity.

Binäre Ereignisse erhalten vorab festgelegte zweiseitige exakte
Binomialintervalle mit 95 % Konfidenz. Ihre Interpretation setzt geeignete
binomiale, insbesondere unabhängige und gleichverteilte Shot-Annahmen voraus;
Providerdrift oder zeitliche Abhängigkeit können diese einschränken. Null Treffer
bedeutet nicht Wahrscheinlichkeit null. Der endliche Shot-Effekt für TV wird
mit 1.000 lokalen Multinomialziehungen je eingefrorener Vorhersage und passender
Shotzahl beschrieben, mit `monte_carlo_seed=515151` und 2,5/50/97,5-%-Quantilen.
Diese Referenzbereiche bestätigen nicht die Wahrheit des Noise-Modells.

Trainingsseed identifiziert feste Winkel, Transpilerseed den Compilerlauf und
Reihenfolge-/Monte-Carloseeds lokale Reproduzierbarkeit. Sie steuern keine
Hardwarezufälligkeit. Reale Counts müssen bei Wiederholung nicht bytegleich
sein. Weder ideale Hardwareübereinstimmung noch gute QAOA-Leistung sind ein
technisches PASS-Kriterium; valide schlechte Hardwareergebnisse bleiben erhalten.
Das historische M3/M4-Sampler-Korrektheitsgate wird nicht auf die Forderung
übertragen, eine echte QPU müsse die ideale Verteilung einhalten.

## Zustände, Artefakte und Acceptance

Die normale Kette lautet `DRAFT -> OFFLINE_READY -> PROVIDER_PREFLIGHT_READY ->
READY_FOR_APPROVAL -> APPROVED -> SUBMITTED -> COLLECTED -> ANALYZED`.
`BLOCKED`, `PARTIAL`, `FAILED`, `CANCELLED` und `SUBMISSION_UNKNOWN` erhalten
ihre eigene Diagnose. Ausstehende Messungen werden als `PENDING_HARDWARE`
beziehungsweise `PARTIAL` ausgewiesen. `OWNER_CONFIGURATION_REQUIRED` benennt
fehlende konkrete Providerkonfiguration, nicht das Scheitern der Offlinearbeit.

Die lokale Fixture-Vorbereitung liegt in `results/m5/`; ein konkret lesend
vorbereiteter Hardwarestand verwendet `results/m5-provider/`. Beide bleiben
getrennt. Die Auswahl steht in `data/synthetic/m5/selection_manifest.json`;
Schaltungen, eingefrorene Vorhersagen, Jobs, Rohdaten und Zusammenfassungen
liegen in den entsprechenden M5-Unterverzeichnissen. Persönliche Instanz-
und Freigabedetails liegen ausschließlich unter dem ignorierten `.m5-local/`.
Kein riesiger Zustandsvektor oder duplizierter Gesamtexport wird zur
vorgegebenen wissenschaftlichen Evidenz erklärt.

Acceptance berichtet getrennt Implementation, historische Integrität,
Kompilierungsäquivalenz, Bitmapping, Noise-Modell, Providerkonfiguration,
Freigabe, geplante/eingereichte/abgeschlossene/fehlende Jobs und PUBs,
`hardware_execution_status`, `hardware_used`, Rohdatenvollständigkeit,
Analysestatus, beobachtete Übereinstimmung sowie Kosten-/Nutzungsnachweise und
Grenzen. Gesamt-M5-PASS erfordert gültige menschliche Freigabe, vollständige
reale Pflichtmessungen, intakte Rohdaten, bestandene technische Gates und
fertige Analyse. Gute Verteilungen sind dafür nicht erforderlich; fehlende
Messungen sind kein erfolgreiches Hardwareexperiment.

## Lokale Bedienung und Prüfpflicht

Die Befehle verwenden den bestehenden Einstieg `km-quantum m5`:
`prepare`, `verify-local`, `provider-preflight`, `plan`, `submit-next`,
`collect`, `analyze` und `acceptance`. Der vollständige lokale und spätere
Providerablauf steht in [m5_ibm_setup.md](m5_ibm_setup.md). `prepare` und
Standardtests sind offline. Providerzugriff ist auf explizite Subcommands
begrenzt; `submit-next` ohne passende menschliche Freigabe wird verweigert.

Vor Verbrauch von Hardwarezeit sind historische Integrität, eingefrorene
Parameterrekonstruktion, native ideale Parität, physisches/klassisches Mapping,
QPY-Verhalten, aktive Breite/RAM, Noise-Remapping und Readout genau einmal
Pflicht. Hinzu kommen Tests für gepinnte API und Optionen, PUB-Identitäten,
Counts mit vollständiger Shot-Summe, Secret-Redaktion, Paid-Fallback-Verweigerung,
Planmutation, Budgetreservation, Timeout nach möglicher Annahme, Resume ohne
Resubmit sowie fehlende oder gescheiterte Jobs und Status ohne reale Daten.

Regressionen, Ruff, Format, mypy und Build werden tatsächlich ausgeführt und
mit neuen M5-Ergebnissen dokumentiert. Tests und CI reichen niemals echte Jobs
ein, auch bei lokal vorhandenen Credentials. Hardwareintegration bleibt
explizit deaktiviert und benötigt separate Freigabe. Kein automatischer
Push, Merge oder Produktionszugriff gehört zu dieser Implementierungsrunde.
