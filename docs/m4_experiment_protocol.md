# M4 — kontrollierte Noise-Analyse und Warm-Start-QAOA

## Eingefrorener Ausgangspunkt und Fragen

M4 baut auf dem vollständigen M3-Archivcommit
`98bbe8bc4106f79372d71560eb9a324edb6fe15e` auf. Der gemeldete Abschlusscommit
`f2a26ed75fe0c44120df45a6e46c2f0cc47b0fa8` und der tatsächliche M3-Trainingscommit
`1f0cdd9f2dd4331a6b637ed24d7fb9d783460add` bleiben getrennt nachvollziehbar.
Vor Änderungen wurden 2.107 historische Dateien mit SHA-256 gesichert, darunter
alle M1/M2/M3-Referenzen und die 1.980 M3-Rohdateien. Nur README, CLI-Anbindung,
pyproject und Lockfile werden für die Integration ergänzt. Historische Berichte
und PASS-Artefakte werden nicht überschrieben.

A untersucht synthetische Kanalfehler bei festen Standard-QAOA-Parametern.
B vergleicht Standard und Warm-Start bei identischem idealem Winkel-Suchbudget.
C überträgt genau diese ideal trainierten Warm-Start-Parameter unter Noise.
Die Standardparameter für A stammen aus B; kein zusätzlicher oder anhand guter
M3-Ergebnisse gewählter Seed wird verwendet. Es gibt **kein Training unter Noise
oder mit shot-basierten Kosten**, keine QPU, Credentials, Runtime oder Cloud-Jobs.

Die vorhandene M3-Semantik wurde unabhängig erneut geprüft. Eine versionierte
M4-Auswertung benennt die anwendungsbezogenen Größen ausdrücklich. Sie ändert
historische Daten nicht und liefert keinen nachträglich umgeschriebenen M3-PASS.

## Vier unterschiedliche Wahrscheinlichkeiten

Primär ist `p_schedule_optimal`: literal decodierte, M1-gültige Planung mit
bewiesen optimalem M1-Objective. Sekundär ist `p_ground_encoding`: vollständige
M2-Minimalbelegung einschließlich aller Hilfsbits. Daneben werden
`p_encoding_feasible` (alle codierten harten Regeln) und
`p_schedule_feasible` (M1-Validator nach literalem Decoding) ausgewiesen.

Für die sicheren lösbaren Referenzen werden die Mengenbeziehungen vollständig
geprüft: ground ⊆ schedule optimal ⊆ schedule feasible und encoding feasible ⊆
schedule feasible. Unterschiedliche vollständige Bitstrings desselben Plans
werden für Planmetriken zusammengezählt. Ungültige Pläne erhalten Objective null;
es gibt keine Reparatur, Hilfsbitoptimierung oder Sample-Selektion im Decoder.

H liefert einen optimalen Plan mit falschem Workload-Slack. M und O zeigen auch
eine nichtminimale Imbalance-Erweiterung trotz null harten Encoding-Penalties.
O ist ausschließlich ein historischer Semantikbeleg außerhalb des Noise-Limits.

## Auswahl vor QAOA-Auswertung

Sechs bekannte Diagnosefälle werden anhand vorher benannter Merkmale verwendet:
A (einfache Zuordnung), E (Überlappung), H (Kapazität), I (mehrere Optima),
K (bounded Slack, bereits bekannte Schwäche), M (aktive Imbalance).
Diese Gruppe ist Entwicklung/explorative Diagnose und kein unberührter Hold-out.
Insbesondere K ist wegen eines bekannten negativen Ergebnisses ausgewählt.

Sechs neue Generatorseeds `20261101..20261106` und ihre festen Konfigurationen
liegen in `data/synthetic/m4/generator_configs.json`: einfache Zuordnung,
Worker-Kosten, Konflikte, Kapazität, sparse Skills und Imbalance. Auswahl erfolgt
ausschließlich nach gültigem Schema und höchstens zehn QUBO-Bits. Jede Ablehnung
steht im Selektionsaudit; es gibt kein Resampling. Equivalence-Fehler blockieren
den Vergleich und sind kein Anlass für Ersatzseeds. Die sechs tatsächlich
zulässigen Fälle haben 4/6/4/5/2/10 Bits. Der sparse-Skills-Fall hat keine
Assignment-Bits und bleibt als vorab entstandene triviale Kontrolle sichtbar.

Alle neuen Fälle werden mit unverändertem M2-Encoder, CP-SAT, M1-Exhaustive,
literalem Decoder und vollständiger Ising-Äquivalenz geprüft. Die neue Gruppe
wurde zuvor nicht mit QAOA ausgewertet, ist aber eine sehr kleine synthetische
Evaluation und trägt keine breite Generalisierungsbehauptung.

## Feste primäre Konfiguration

| Einstellung | Wert |
| --- | --- |
| Instanzen / aktive Qubits | höchstens 12 / 10 |
| Tiefen | p=1,2; keine p3-Zusatzsuite |
| Winkel-Restarts | Seeds 0,1,2,3,4 für beide Varianten |
| Winkelinitialisierung | numpy.default_rng(seed).uniform(0,pi,actual_parameter_count) |
| Ideale Kosten | unveränderte M3-StatevectorEstimator-Komponente, precision 0 |
| Optimierer | COBYLA, 300 Kostenaufrufe, tol 1e-6, rhobeg 0.5 |
| Finale Verifikation | ein zusätzlicher, getrennt geloggter Aufruf |
| Quantum-Hamiltonian | unveränderte M2-Koeffizienten, keine Reskalierung |
| Offset | außerhalb der Evolution, in allen Energien enthalten |
| Evolution / Mixerzeichen | exp(-i gamma H), exp(-i beta H_mixer), s=+1 |
| Relaxation | genau eine diagonale konvexe Box-Fortsetzung |
| Box-Optimierer | L-BFGS-B, Start 0.5, analytischer Gradient |
| Box-Budget | 300 kombinierte f/g-Aufrufe und höchstens 300 Iterationen |
| Box-Toleranzen | ftol 1e-12, gtol 1e-8 |
| Numerische Vorbereitungsgates | Library CONVERGED plus skaliertes KKT-Residuum und konvexe Tangentengap jeweils ≤1e-6 |
| Warm-Start-Clipping | epsilon=0.25; kein Epsilon-Tuning |
| Klassische Rundung | ungeclipptes y; bei y=0.5 wird 1 gewählt |
| Compiler | rz/sx/x/cx, optimization_level 0, Seed 424242 |
| Hardwaretarget / Layout / Routing | keines |
| Shots pro Messauswertung | 1024, nach exakter Dynamik |
| Sampling-Prüfung | vier zweiseitige Binomialtests, alpha 1e-9, 95%-Intervalle |
| Speicher / Threads | Aer höchstens 512 MiB; zwei Aer-/BLAS-Threads |
| Parallelität | ein Run, eine Aer-Experiment-/Shot-Gruppe gleichzeitig |
| Wall-Limit | 7.200 Sekunden pro Aufruf; Kontrolle zwischen jedem Experiment |

Die technische Vorprüfung fand zwei ftol-Terminationen oberhalb der strengeren
gtol-Stationaritätsmarke. Die Solver-Toleranzen und Budgets bleiben unverändert.
Das vor der QAOA-Hauptevaluation festgelegte 1e-6-Gate prüft zusätzlich zum
Library-Flag beide numerischen Optimalitätsdiagnosen. Die strengere 1e-8-Marke
wird separat berichtet. Diese Float-Diagnosen sind keine rigorosen symbolischen
Optimalitätszertifikate und besagen nichts über die Güte einer binären Lösung.
Ein fehlgeschlagener Solver oder überschrittenes Residuum bleibt ein Ausfall;
ein bekanntes Optimum wird niemals als Ersatzvorbereitung verwendet.

Die gleich großen variationalen Budgets sind keine Gleichheit der Gesamtkosten.
Die klassische Relaxationsvorbereitung wird einmal pro Instanz tatsächlich
ausgeführt, für einen einzelnen Warm-Restart vollständig mitgerechnet und in
einer zusätzlichen Kampagnenauswertung ausdrücklich über zehn Warm-Restarts
amortisiert. CP-SAT bleibt die bewiesene klassische Referenz; seine gemessene
lokale Solverzeit wird separat berichtet, ohne Quantum-Advantage-Behauptung.

## Pflichtkontrollen und Ablauf

Für jede Instanz: uniforme volle Bitverteilung, deterministische Rundung,
geclippte klassische Produktverteilung ohne QAOA und unabhängiger CP-SAT-Beleg.
Alle Kontrollen behalten ungültige Zustände. Uniform- und Produktverteilung
werden auch klassisch endlich gesampelt. Es gibt keine Quantum-Leistung aus
dieser klassischen Vorarbeit zuzuschreiben.

B trainiert Standard und Warm-Start ideal mit denselben Startwinkeln, Seeds und
Budgets. Alle Traces werden sofort vollständig gespeichert. Optimiererbudget-
Termination ist ein berichteter Run und keine Löschregel. Jeder gebundene
Circuit wird genau einmal kompiliert und sein Gate-Sequence-Hash eingefroren.
Alle fünf Noise-Profile verwenden denselben Circuit und unveränderte Winkel.

| Profil | lambda_1q | lambda_2q | Readout-Flip |
| --- | ---: | ---: | ---: |
| ideal | 0 | 0 | 0 |
| gate_only | 0.0005 | 0.005 | 0 |
| readout_only | 0 | 0 | 0.01 |
| combined_low | 0.0005 | 0.005 | 0.01 |
| combined_high | 0.002 | 0.02 | 0.03 |

Die API-Kanäle sind E(rho)=(1-lambda)rho+lambda I/2^k, keine pauschalen
Pauli-Fehlerwahrscheinlichkeiten oder Gate-Infidelities. Depolarisation trifft
sx/x und cx nach ihrer realen Kompilierung; rz ist ideal. Zustandserzeugung und
Warm-Mixer sind enthalten. Diese synthetischen Raten sind keine IBM-Kalibration.

Aer density_matrix berechnet p_true vor Messung und Readout. Row-stochastische
Matrizen R[true,observed] wirken danach einmal klassisch: p_observed=R^T p_true
pro Bit, Qiskit Bit i mit Gewicht 2^i. Counts werden anschließend unabhängig
aus p_observed gezogen. Ideal, vor Readout, nach Readout und empirisch werden
für jede Wahrscheinlichkeit und Energie getrennt bezeichnet. TV-Distanz
vergleicht die volle ideale und beobachtete Bitverteilung. Exakte Dichtematrix-
Wahrscheinlichkeiten besitzen keinen Shot-Fehler.

## Ressourcen, Runs und Abbruch

Eine 10-Qubit-Dichtematrix hat 1.048.576 complex128-Einträge (16 MiB).
Der Budgetcheck reserviert konservativ acht solcher Matrizen (128 MiB) als
Workspace-Schätzung, überprüft freien RAM und erzwingt die Run-/Thread-Limits.
Das ist keine behauptete Messung der gesamten Prozess-Spitzenlast.

Vor der Pflichtsuite gibt es einen technischen Smoke-Test auf bekannten
Diagnosefällen mit festen Winkeln, einschließlich eines 10-Qubit-Circuits.
Ressourcenänderungen vor der Freeze-Grenze werden dokumentiert; danach keine
erfolgsabhängigen Instanz-, Seed-, Budget- oder Profilwechsel.

Geplant: 12 Vorbereitungen, 36 Verteilungskontrollen, 12 CP-SAT-Referenzen,
240 ideale Trainingsläufe und 1.200 Kanal-/Readout-/Sampling-Auswertungen.
Das Manifest enumeriert jede Pflichtidentität. Sample-Seed-Regeln sind vollständig
aus Instanzindex, Variante, Tiefe, Restart und Profilindex bestimmt.

Hashabweichungen, Equivalence-/Compiler-/Mixer-/Zero-Noise-/Readout-Gatefehler
blockieren Leistungsexperimente. Ressourcen- oder Wall-Limits stoppen sichtbar.
Fehler und übersprungene Pflichtläufe bleiben im Ausführungsledger erhalten.
Resume verwendet ausschließlich passende Referenz-/Konfigurations-/Compiler-
und Code-/Dependency-Identitäten und verifiziert gespeicherte Winkel, Traces,
vollständige Verteilungen, Metriken und Counts. Keine stille Ersetzung von Seeds.

## Statistik, Ergebnisse und Grenzen

Zuerst werden fünf Restarts innerhalb jeder Instanz zusammengefasst; anschließend
werden Instanzen gleich gewichtet. Bekannte Diagnose und neue Evaluation bleiben
getrennt. Median, Sample-Streuung, gepaarte Restart-Unterschiede und Unterschiede
der Instanzmediane werden mit klarer Aggregationseinheit angegeben. Primäre
Änderungen stehen in absoluten Prozentpunkten; relative Änderung bei Nullbasis
ist null. Shots und Restarts sind keine neuen unabhängigen Kundenprobleme.

Explizite Zusatznutzenmaße: P_schedule_optimal(Warm-QAOA) minus Produktstart ohne
QAOA sowie P_schedule_optimal(Standard-QAOA) minus uniform. Conditional-Objective
steht immer zusammen mit Schedule-Feasibility; beste gültige Sample-Planung und
Gap sind null, wenn kein gültiger Sample-Plan auftritt. Encoding-/Plan-Degeneracy
und Einzelfälle einschließlich K werden vollständig gezeigt.

Es wird kein monoton schlechterer Noise-Trend oder Warm-Start-Sieg vorausgesetzt.
Negative und neutrale Befunde bleiben sichtbar. Kleine synthetische Fälle, eine
einzige Box-Relaxation, exponentielle Simulation und künstliche Kanäle begrenzen
die Aussage. Thermal Relaxation, Idle-Zeiten, Drift, Crosstalk, Hardwaretopologie,
Mitigation und noisy Training sind nicht modelliert. M5 wird ausschließlich als
späterer kontrollierter Vergleich eingefrorener Instanzen/Circuits/Winkel
beschrieben; es wird weder implementiert noch ausgeführt.

## Primärquellen und Stack

Qiskit 2.5.2 bleibt unverändert. Aer 0.17.2 und threadpoolctl 3.7.0 sind stabil
installierbare, gepinnte Ergänzungen; die Aer-Webdokumentation trägt teilweise
noch Versionslabel 0.17.1. Maßgeblich sind installierte APIs und eigene Tests.

- [Egger, Mareček, Woerner](https://arxiv.org/abs/2009.10095)
- [IBM Warm-Start](https://quantum.cloud.ibm.com/docs/en/tutorials/warm-start-qaoa)
- [AerSimulator](https://qiskit.github.io/qiskit-aer/stubs/qiskit_aer.AerSimulator.html)
- [NoiseModel](https://qiskit.github.io/qiskit-aer/stubs/qiskit_aer.noise.NoiseModel.html)
- [Depolarisationskanal](https://qiskit.github.io/qiskit-aer/stubs/qiskit_aer.noise.depolarizing_error.html)
- [ReadoutError](https://qiskit.github.io/qiskit-aer/stubs/qiskit_aer.noise.ReadoutError.html)
- [Aer-Paket](https://pypi.org/project/qiskit-aer/0.17.2/)
- [Threadpool-Steuerung](https://pypi.org/project/threadpoolctl/)
