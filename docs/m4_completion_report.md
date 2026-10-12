# M4 — Abschlussbericht: kontrollierte Noise-Analyse und Warm-Start-QAOA

Stand: 12. Oktober 2026. **M4-Gesamtstatus: PASS.** Die vorab eingefrorene Pflichtsuite ist vollständig ausgeführt; technische Gates und unabhängiger Vollaudit sind **PASS**. Die getrennte Gesamt-Abnahme
steht in [m4-acceptance.json](../results/m4/m4-acceptance.json). Schlechte algorithmische Ergebnisse sind kein technischer Abnahmefehler.

Warm-Start zeigt **keine breite Verbesserung auf der neuen Suite**. Der Mittelwert der sechs gleich gewichteten Instanzmedian-Differenzen ist ideal positiv
(+6,673 / +4,579 Prozentpunkte bei p=1/2), vor allem wegen des einfachen neuen Falls. Der Median dieser Instanzdifferenzen beträgt dagegen +0,153 / −0,383 Prozentpunkte;
die entsprechende Zusammenfassung gepaarter Seed-Differenzen −0,471 / +0,043. Diese verschiedenen Aggregationen dürfen nicht als dasselbe Ergebnis gelesen werden.
Die bekannten Fälle sind explorative Diagnose, insbesondere der bereits schwache Fall K.

## 1. Was wurde gebaut und überprüft?

M4 ergänzt ausschließlich eine lokale Forschungs-Pipeline: genau eine klassische Box-Relaxation, Produktstart und dazu passenden Mixer, ideale Standard-/Warm-Optimierung,
deterministische Kompilierung, Kanaltransfer mit Dichtematrizen und getrenntes Readout/ Sampling. **Training unter Noise oder mit shot-basierten Kosten ist ausgeschlossen.**
Standard verwendet den unangetasteten M3-Trainingspfad. Für A werden die unter B trainierten Standardparameter verwendet; C überträgt die ideal trainierten Warm-Winkel.
Es gab keine zusätzliche Suche je Noise-Profil oder Auswahl guter M3-Seeds.

| Pflichtteil | Geplant | Ausgeführt und geprüft | Fehlgeschlagen / übersprungen / fehlend |
| --- | ---: | ---: | --- |
| Klassische Vorbereitungen | 12 | 12 | 0 / 0 / 0 |
| Verteilungskontrollen | 36 | 36 | 0 / 0 / 0 |
| Ideale Trainingsläufe | 240 | 240 | 0 / 0 / 0 |
| Gebundene, kompilierte Schaltungen | 240 | 240 | 0 / 0 / 0 |
| Density-/Readout-/Sampling-Auswertungen | 1.200 | 1.200 | 0 / 0 / 0 |

Der Ausführungsledger enthält 1.200 `COMPLETE`-Identitäten. Die Hauptkampagne dauerte **1.104,264 s (18 min 24 s)**. Es wurden 1.228.800 primäre und 36.864 Kontroll-Shots
gesampelt. Alle 1.200 primären Sampling-Audits und insgesamt 4.944 Binomialprüfungen der vier Metriken einschließlich Kontrollen bestanden. Alle primären Samples enthielten
eine gültige Planung; deren beste Objective-Gap war 1.192-mal null und achtmal eins. Dies ist eine Best-of-1024-Statistik, nicht die Wahrscheinlichkeit eines optimalen Plans.
Die Anzahl unterschiedlicher gesampelter Pläne lag zwischen 1 und 64.

Es gibt 1.968 primäre Rohdateien mit 148.596.979 Bytes, 1.728 JSON-Artefaktseals und 240 Traces. `runs.jsonl` enthält zusätzlich den aggregierten Export (83.107.340 Bytes).
Der Vollaudit rekonstruiert ideale Schaltungen, Kontrollen, Metriken und gesampelte Counts und prüft Ledger, Traces, Seals und Compiler; er optimiert nicht erneut und wiederholt
keine primäre Density-Simulation. Seals sind Integritätsbelege, keine digitalen Signaturen.

### Semantik und historische Referenzen

2.107 historische Dateien blieben lokal **byteweise SHA-256-identisch**. Die M3-Semantik wurde auf 114 Referenzen mit 47.014 Basiszuständen und allen 660 gespeicherten M3-Läufen
erneut geprüft. Es gibt keinen historischen Algorithmus-/Mappingfehler und keine notwendige Neuberechnung einer Baseline. Die zusätzliche [Semantikauswertung](../results/m4/metric_semantics_audit.json)
benennt vorhandene Größen ausdrücklich und aggregiert Hilfsbitdarstellungen desselben Plans; historische Berichte, Rohdaten und M3-PASS bleiben unverändert.

Primär ist `p_schedule_optimal`, sekundär `p_ground_encoding`. Zusätzlich stehen `p_encoding_feasible` und `p_schedule_feasible`. Die vollständigen Zustandsmengen bestätigen
ground ⊆ schedule optimal ⊆ schedule feasible und encoding feasible ⊆ schedule feasible. M3s `optimal_valid_probability` bezeichnet dagegen energie-konsistente optimale Encodings.
In 240 M3-Läufen übersteigt die literal decodierte Optimalitätsmasse die Ground-Masse.

| Nachweislicher Zustand, M2-Reihenfolge | M1-Objective | QUBO-Energie | harte Encoding-Penalty | Bedeutung |
| --- | ---: | ---: | ---: | --- |
| H: `01101` | 7 | 22 | 15 | optimaler Plan, falsches Slack; Punktmasse hat P_schedule_optimal=1 und P_ground=0 |
| M: `0011011010` | 7 | 9 | 0 | null harte Penalties erzwingen keine minimale Imbalance-Bewertung |
| O: `011000001010101` | 0 | 1 | 0 | optimaler Plan und encoding-feasible, trotzdem nonground |

O hat 15 Bits und dient ausschließlich als historischer Semantikbeleg außerhalb des M4-Noise-Limits. Ungültige Pläne erhalten Objective `null`; Decoder und Kontrollen
reparieren nichts und optimieren keine Hilfsbits nachträglich.

### Technische Verifikation und Tooling

Die M4-Gates prüften 2.368 Basiszustände, 24 Zero-Noise-Schaltungen und 24 Warm-/Standard- Grenzfälle. Maximale Abweichungen: ideale Compiler-Amplituden 1,572e−15,
epsilon=0.5-Amplituden 3,925e−16, Zero-Noise-Dichtematrix 3,894e−16. Über die primären Density-Records: maximaler Trace-Fehler 2,731e−14,
Hermitizitätsfehler 4,0e−16, kleinster Eigenwert −1,762e−15; numerisches Rundungsniveau. Die maximale Estimator-/unabhängige Energieabweichung im Training war 4,547e−13.
`energy_residual` bedeutet dagegen Erwartungsenergie minus Grundenergie und ist kein Rechenfehlermaß. Die tatsächlichen technischen Gates dauerten 28,148 s.

| Tatsächlich ausgeführte Prüfung | Ergebnis |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest -q` | 1.094 bestanden, 0 fehlgeschlagen, 235,59 s |
| `.venv/Scripts/ruff.exe check .` | PASS |
| `.venv/Scripts/ruff.exe format --check .` | PASS, 97 bereits formatierte Dateien |
| `.venv/Scripts/mypy.exe src` | PASS, 53 Quelldateien |
| `uv build` | PASS, Wheel und sdist |
| unabhängiger Rohdaten-Vollaudit | PASS, keine fehlenden, unerwarteten oder übersprungenen Identitäten |
| unabhängige Qualitäts-/Noise-Reviews | PASS; 14.400 Qualitätszahlen stimmen, max. Differenz 2,842e−14 |

Getestet wurden insbesondere Gradient/PSD/Binärgleichheit und harte Budgets, Mixer-Matrix gegen unabhängige Matrixexponential, epsilon=0.5-Grenzfall, Bit-/Readout-Orientierung,
Compiler-Parität, Zero Noise, physikalische Density-Matrizen, aktive Noise-Gates, Sampling-Statistik, Manifest/Resume, Ressourcen, Abbrüche und sämtliche CLI-Routen.
Keine Prüfung verlangt einen Warm-Sieg oder monoton schlechtere Noise-Metriken.

## 2. Versuchsdesign und zusätzliche klassische Arbeit

Das unveränderliche [Protokoll](m4_experiment_protocol.md) und [Manifest](../data/synthetic/m4/benchmark_manifest.json) legen p=1/2, Seeds 0..4, COBYLA mit 300 Kostenaufrufen, tol=1e−6 und rhobeg=0.5 fest.
Beide Varianten initialisieren dieselben tatsächlichen Winkelparameter mit `[0,pi)`. 65.032 Optimiereraufrufe und 240 separate finale Verifikationen wurden gespeichert.
56 Läufe erreichten die untere Trust-Region-Grenze (Standard 27, Warm 29); 184 stoppten ausdrücklich wegen `MAXFUN` (Standard 93, Warm 91), bei 40..300 Aufrufen.
Diese Budgettermination bleibt ein gültiger beobachteter Lauf, keine Konvergenzbehauptung.

Bekannt: A (Zuordnung), E (Konflikte), H (Kapazität), I (mehrere Optima), K (bekannte Schwäche), M (Imbalance). Neue Seeds 20261101..20261106 wurden ausschließlich anhand
fester Schema-/Größenkriterien gewählt und mit unverändertem M2-Encoder, M1-Exhaustive und CP-SAT äquivalenzgeprüft. Keine Ablehnung führte zu einem Ersatzseed. Der neue
sparse-Skills-Fall ohne Assignment-Bits bleibt als triviale Kontrolle erhalten.

| Gruppe / Fall | Bits | Ground-Encodings | bewiesenes CP-SAT-Objective | lokale CP-SAT-Zeit, ms |
| --- | ---: | ---: | ---: | ---: |
| bekannt A | 2 | 1 | 0 | 8,293 |
| bekannt E | 4 | 2 | 7 | 4,821 |
| bekannt H | 5 | 2 | 7 | 6,780 |
| bekannt I | 3 | 2 | 0 | 3,864 |
| bekannt K | 7 | 2 | 7 | 1,923 |
| bekannt M | 10 | 2 | 2 | 2,313 |
| neu simple | 4 | 1 | 0 | 4,874 |
| neu cost | 6 | 1 | 2 | 2,459 |
| neu conflict | 4 | 2 | 8 | 2,962 |
| neu capacity | 5 | 2 | 8 | 3,552 |
| neu sparse_skills | 2 | 1 | 28 | 3,905 |
| neu imbalance | 10 | 1 | 3 | 52,710 |

Alle zwölf CP-SAT-Belege sind `OPTIMAL`, mit bewiesenem Bound und Gap null. Diese gemessenen lokalen Solverzeiten bleiben praktischer klassischer Kontext; sie sind
keine gleichartigen Hardwarekosten eines Quantum-Laufs.

Die einzige [Warm-Start-Fortsetzung](m4_warm_start_formulation.md) setzt A_ij=b_ij/2, A_ii=0 und lambda über die exakte Gershgorin-Zeilenschranke. Minimiert wird
f(y)=c+(a−lambda·1)^T y+y^T(A+lambda·I)y auf [0,1]^n. Die Matrix ist PSD und f(x)=Q(x) für jedes binäre x; der Quantum-Hamiltonian bleibt vollständig unverändert.
Ein deterministischer, skalierter L-BFGS-B-Lauf startet bei 0,5 und nutzt analytische Gradienten, höchstens 300 f/g-Aufrufe und Iterationen, ftol=1e−12 und gtol=1e−8.
Keine CP-SAT-Lösung, Ground States oder Exhaustive-Ergebnisse gehen in die Vorbereitung ein.

12/12 Vorbereitungen meldeten `CONVERGED`; 10/12 erfüllen das strengere 1e−8-Flag. M hat skaliertes projiziertes Residuum 1,604e−7 und Tangentengap 4,252e−7,
neu imbalance 1,246e−8 beziehungsweise 3,403e−8. Beide erfüllen das **vor dem Hauptlauf** festgelegte 1e−6-Verwendungsgate für beide Größen. Das strengere Flag bleibt falsch;
Solver-Toleranzen und Budgets wurden nicht verändert. Float-KKT-/Tangentendiagnosen sind keine rigorosen symbolischen Zertifikate und garantieren keine gute binäre Planung.

Clipping epsilon=0,25 und theta=2 arcsin(sqrt(c)) erzeugen einen klassisch samplebaren Bernoulli-Produktstart. Der passende Mixer übernimmt M3s **s=+1**:
H_warm=sum RY(theta) Z RY(theta)^dagger; Append-Reihenfolge RY(−theta), RZ(2 beta), RY(theta). Der eigene Start ist dabei +1-Eigenzustand, kein Mixer-Grundzustand.
Bei epsilon=0,5 reproduzieren vollständiger Start, Mixer und Ansatz M3 bis auf globale Phase. Die unveränderten Cost-Evolutionen sind exp(−i gamma H), der Offset bleibt in jeder Energie.

## 3. Hilft Warm-Start gegenüber Standard?

Alle folgenden Hauptzahlen stammen aus exakten Verteilungen **nach Readout**; für `ideal` ist dies zugleich die ideale Verteilung. S=Standard, W=Warm.
Jede Tabellenzelle ist zuerst der Median aller fünf Restarts pro Instanz, anschließend das arithmetische Mittel über sechs gleich gewichtete Instanzen.
Werte sind Prozent, Unterschiede Prozentpunkte (pp); keine Signifikanzbehauptung.

### Bekannte explorative Diagnosefälle

| p | Profil | P_schedule_optimal S | W | P_ground_encoding S | W |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | ideal | 32,693 | 33,688 | 25,092 | 27,074 |
| 1 | gate_only | 32,468 | 33,210 | 24,809 | 26,739 |
| 1 | readout_only | 32,229 | 33,165 | 24,639 | 26,597 |
| 1 | combined_low | 32,010 | 32,703 | 24,367 | 26,274 |
| 1 | combined_high | 30,033 | 30,560 | 22,814 | 24,493 |
| 2 | ideal | 35,554 | 46,877 | 25,816 | 36,743 |
| 2 | gate_only | 34,452 | 45,193 | 25,158 | 35,501 |
| 2 | readout_only | 34,968 | 45,826 | 25,343 | 35,814 |
| 2 | combined_low | 33,909 | 44,199 | 24,713 | 34,626 |
| 2 | combined_high | 30,469 | 38,453 | 22,335 | 29,948 |

### Neue kleine synthetische Evaluation

| p | Profil | P_schedule_optimal S | W | P_ground_encoding S | W |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | ideal | 32,712 | 39,386 | 29,653 | 36,067 |
| 1 | gate_only | 32,593 | 39,053 | 29,416 | 35,656 |
| 1 | readout_only | 31,974 | 38,429 | 28,869 | 35,079 |
| 1 | combined_low | 31,862 | 38,111 | 28,646 | 34,688 |
| 1 | combined_high | 30,104 | 35,299 | 26,598 | 31,811 |
| 2 | ideal | 35,848 | 40,427 | 32,395 | 38,548 |
| 2 | gate_only | 35,360 | 39,837 | 31,852 | 37,670 |
| 2 | readout_only | 34,918 | 39,375 | 31,490 | 37,447 |
| 2 | combined_low | 34,462 | 38,817 | 30,978 | 36,612 |
| 2 | combined_high | 31,764 | 35,337 | 28,049 | 32,499 |

Der neue simple-Fall verbessert die idealen Medianwerte um +42,217 / +38,176 pp und dominiert die positiven Gruppenmittel. Ideale Median-Differenzen über Instanzen:
bekannt +0,931 / +6,288 pp, neu +0,153 / −0,383 pp. Der Median der instanzweise berechneten Mediane gepaarter Seed-Differenzen ist bekannt +6,125 / +1,780 pp,
neu −0,471 / +0,043 pp. Diese verschiedenen Schätzgrößen zeigen keine breite neue Verbesserung. Der triviale sparse-Skills-Fall liegt nahe eins; winzige numerische
Differenzen sind kein substanzieller Vorteil.

Einzelfälle und Streuung bei p=2, ideal: **Median [Minimum, Maximum]** der fünf Restarts, P_schedule_optimal in Prozent. Vollständige p=1/2-Streuungen stehen im JSON und in den Plots.

| Fall | Standard | Warm |
| --- | --- | --- |
| A | 51,827 [46,323; 66,072] | 76,915 [70,106; 93,594] |
| E | 36,762 [27,146; 54,517] | 44,935 [32,631; 51,636] |
| H | 28,484 [16,266; 42,510] | 16,473 [11,316; 45,448] |
| I | 43,074 [13,543; 85,739] | 84,348 [57,444; 85,962] |
| K | 8,107 [5,200; 14,271] | 9,121 [2,803; 10,403] |
| M | 45,068 [7,060; 50,169] | 49,471 [17,589; 54,015] |
| neu simple | 42,580 [32,897; 78,859] | 80,757 [71,661; 90,811] |
| neu cost | 4,655 [0,015; 14,877] | 3,889 [0,101; 5,945] |
| neu conflict | 38,288 [35,437; 47,863] | 36,292 [22,238; 57,595] |
| neu capacity | 21,641 [4,793; 32,326] | 13,691 [0,876; 29,203] |
| neu sparse_skills | 100,000 [100,000; 100,000] | 100,000 [99,972; 100,000] |
| neu imbalance | 7,924 [3,741; 13,872] | 7,931 [5,554; 25,417] |

## 4. Welcher Anteil ist schon klassisch oder ohne QAOA vorhanden?

Deterministisches Runden ist nur bei A, neu simple und neu sparse_skills gültig (3/12); die anderen neun Pläne bleiben ungültig mit Objective `null`. Die geclippte Produkt-
verteilung benötigt keine Quantum-Schichten. Der relevante Schichtenzusatznutzen ist Warm-QAOA minus Produktstart, beziehungsweise Standard-QAOA minus uniforme Verteilung.

| Gruppe | p | Warm−Produkt: Mittel / Median über Instanzen, pp | Standard−Uniform: Mittel / Median, pp |
| --- | ---: | ---: | ---: |
| bekannt | 1 | +8,046 / +5,291 | +14,985 / +14,691 |
| bekannt | 2 | +21,235 / +19,789 | +17,845 / +19,071 |
| neu | 1 | +17,646 / +15,628 | +20,994 / +13,902 |
| neu | 2 | +18,687 / +13,205 | +24,129 / +17,464 |

Diese idealen Unterschiede vergleichen den jeweiligen Restart-Median mit seiner eigenen klassischen Initialverteilung. Sie erlauben keinen pauschalen Quantum-Zusatznutzen:
Warm-Schichten verlieren gegenüber dem eigenen Produktstart bei M p=1 −2,247 pp, neu cost p=1 −2,824 pp und neu imbalance p=1/2 −0,864 / −7,704 pp.
Vorbereitung und Sampling dieses Produkts bleiben klassische Leistungen.

## 5. Was verändert Noise bei festen Parametern?

Compiler: `rz/sx/x/cx`, optimization_level=0, Seed 424242, kein Hardwaretarget/Routing. Der eingefrorene gebundene Circuit bleibt über alle Profile identisch. Depolarisation
wirkt nach real enthaltenen `sx/x` und `cx`; `rz` ist ideal. Zustandserzeugung und Warm-Mixer sind enthalten. Warm-Schaltungen haben in den strukturell gepaarten Vergleichen
genau doppelt so viele `sx/x`, dieselben `cx`; dies isoliert keine kausale Leistungserklärung.

| Profil | lambda_1q | lambda_2q | Readout-Flip |
| --- | ---: | ---: | ---: |
| ideal | 0 | 0 | 0 |
| gate_only | 0,0005 | 0,005 | 0 |
| readout_only | 0 | 0 | 0,01 |
| combined_low | 0,0005 | 0,005 | 0,01 |
| combined_high | 0,002 | 0,02 | 0,03 |

Die API bedeutet E(rho)=(1−lambda)rho+lambda I/2^k, keine pauschale Pauli- Fehlerwahrscheinlichkeit oder Gate-Infidelity. Die ungemessene Density-Matrix liefert
p_true; anschließend wirkt einmal klassisch R[true,observed], also p_observed=R^T p_true. Readout steckt nicht in der gespeicherten Vor-Messungs-Dichtematrix. Die 1024 Counts
folgen erst danach. Exakte Verteilungen haben keinen Shot-Fehler; mehr Shots beseitigen keine Kanalfehler. Erwartete Energie enthält in allen Ebenen den konstanten Offset.

Beispiel ohne Best-Seed-Auswahl: **K, Warm, p=2, Seed 0, combined_high**, Sampling-Seed 4041204. Werte sind Prozent, Energie originale QUBO-Einheiten; empirisch aus 1024 Counts.

| Ebene | Energie | P_ground | P_encoding_feasible | P_schedule_feasible | P_schedule_optimal |
| --- | ---: | ---: | ---: | ---: | ---: |
| ideal exakt | 82,568 | 1,045 | 3,378 | 13,709 | 9,121 |
| Kanal, vor Readout exakt | 107,868 | 1,279 | 3,183 | 12,809 | 7,503 |
| nach Readout exakt | 111,656 | 1,298 | 3,245 | 13,225 | 7,478 |
| empirische Häufigkeit / Energie | 108,019 | 1,172 | 3,027 | 13,770 | 7,520 |

Die besten gültigen Samples erreichen hier Objective 7, Gap null; 16 unterschiedliche Pläne wurden gesampelt. Das veranschaulicht Encoding-/Anwendungsunterschied und endliche
Stichprobe. Die alle Profile umfassenden Gruppentabellen oben zeigen in beiden Gruppen geringere Mittelwerte unter den stärkeren Profilen, ohne Monotonie jedes Seeds zu behaupten.
Unter combined_high bleibt der neue Mittelvorteil +5,194 / +3,573 pp, die Median- Instanzdifferenz ist dagegen −0,127 / −0,613 pp; damit bleibt die breite Evidenz schwach.

## 6. Was passiert in K, und welche Befunde sind negativ oder unsicher?

K ist ausdrücklich explorativ ausgewählt. Uniforme Planungsoptimalität ist 6,25%, Produktstart 5,885%; uniforme Ground-Masse dagegen 1,5625%. Ideale Medianwerte:

| p | P_schedule_optimal S / W, % | Differenz der Mediane W−S, pp | Median gepaarter Seed-Differenzen, pp | P_ground S / W, % |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 3,400 / 9,395 | +5,995 | +6,664 | 0,465 / 1,431 |
| 2 | 8,107 / 9,121 | +1,014 | −0,366 | 0,735 / 1,045 |

Alle vier idealen Ground-Mediane bleiben unter Uniform. Bei p=2 widersprechen sich Differenz der Mediane und Median gepaarter Differenzen im Vorzeichen. Unter combined_high
steigt Standards p=2-Ground-Median auf 1,147%, während die Planungsoptimalität auf 6,948% fällt. Warms p=1-Ground-Median steigt auf 1,890%, doch sein Median gepaarter Ground-
Änderungen ist −0,065 pp: drei Seeds fallen, zwei steigen und die Ränge wechseln. Sein Planungsoptimalitätsmedian fällt auf 8,398%. Dies ist kein isolierter Noise-Nutzen.

Weitere negative ideale Warm−Standard-Median-Differenzen: H −4,133 / −12,012 pp, neu conflict −3,346 / −1,996 pp, neu capacity −2,289 / −7,950 pp. Dazu kommen die
negativen Schichtenzuwächse, starke Seed-Streuung, 184 MAXFUN-Terminationen und zwei verfehlte strikte KKT-Diagnosen. Es wurden weder Fälle noch Seeds entfernt oder Budgets
nach Erfolg erweitert. Ursachen wie Hilfsbits, Landschaft oder zusätzliche Gatefehler wurden nicht einzeln interveniert und werden deshalb nicht als bewiesen ausgegeben.

Gleiche Winkelbudgets sind keine gleichen Gesamtkosten. Tatsächlich deduplizierte Komponenten der Kampagne, Sekunden:

| Komponente | Zeit |
| --- | ---: |
| Relaxationen einmal pro Instanz | 0,048 |
| ideales Training einmal pro Restart | 542,475 |
| Compile einmal pro gebundenem Circuit | 4,470 |
| 1.200 Density-Auswertungen | 374,276 |
| klassisches Readout | 0,715 |
| Sampling einschließlich Auswertung | 54,930 |
| Verteilungsanalyse | 0,624 |
| Komponentensumme | 977,538 |
| tatsächliche Kampagnen-Wall-Time | 1.104,264 |

Die Differenz 126,726 s enthält gemeinsame Gates, Tabellen/Kontrollen, Datei-I/O und Harness-Overhead. Die spätere Vollaudit-Zeit ist ebenfalls getrennt. Pro hypothetischer
Einzelpipeline wird Relaxation vollständig angerechnet; separat wird sie über zehn Warm-Restarts amortisiert. Summen über fünf Profile dürfen wiederverwendetes Training/
Compile/Relaxation nicht fünfmal als ausgeführte Arbeit zählen. Im unangetasteten Standardpfad bleiben interne Vorbereitung und erste finale Zustandsanalyse kombiniert;
M4s zusätzlich gemessene Zeitfelder sind dort ausdrücklich **nicht additiv**. Warm-Phasen sind direkt gemessen. Lokale Compile-Gates, Simulation und CP-SAT-Zeiten sind keine QPU-Kosten.

## 7. Grenzen und Vorbereitung eines späteren M5

Je sechs bekannte und neue kleine synthetische Fälle begründen keine breite Scheduling-Generalisierung. Shots und Restarts sind keine neuen Kundenprobleme;
Sampling-Konfidenzintervalle beschreiben keine Generalisierungsunsicherheit. Relative Veränderungen bei Nullbasis sind undefiniert; das JSON berichtet absolute Änderungen in Prozentpunkten.
Die einzelne konvexe Fortsetzung und feste Clipping-Regel können einen schwachen Start liefern; die Relaxation beweist keine Rundungsgüte. p=3 wurde nicht nachträglich ergänzt.

Density-Simulation skaliert O(4^n): eine 10-Qubit-complex128-Matrix hat 16 MiB; die Vorprüfung reserviert acht Matrizen als 128-MiB-Workspace-Schätzung, Aer höchstens
512 MiB und zwei Threads, einen Run gleichzeitig. Dies ist keine gemessene Prozess- Spitzenlast oder Skalierungsgarantie. Die künstlichen Profile sind keine IBM-Kalibration.
Thermal Relaxation, Idle-Zeiten, Drift, Crosstalk, echte Topologie und noisy Training sind nicht modelliert. Keine Mitigation, CVaR, dynamische Planreparatur, Decomposition,
Produkt-UI oder Quantum-Advantage-Behauptung wurde eingeführt.

Für M5 liegen Instanzen, gebundene Schaltungen und Parameter eingefroren bereit. Konkrete Hardware-Kompilierung und Kalibrationsdaten wären ein neuer kontrollierter
Versuchsaufbau. **M5 ist nicht implementiert; keine QPU, IBM-Accounts, Credentials, Cloud-Jobs oder kostenpflichtigen Dienste wurden benutzt.**

## Artefakte, Reproduktion, Versionen und Commits

Die fünf Plots sind visuell geprüft und zeigen Einzelfälle/Streuung, bekannte/neue Gruppen und K ausdrücklich explorativ: [Standard/Warm](../results/m4/standard_vs_warm_start.png),
[Produkt ohne/mit QAOA](../results/m4/warm_product_vs_qaoa.png), [Noise](../results/m4/noise_influence.png), [Feasibility](../results/m4/encoding_vs_schedule_feasibility.png),
[K](../results/m4/case_k_exploratory.png). Die CLI-Originale liegen direkt in `results/m4`; identische Kopien liegen zur Abnahme in `results/m4/plots`. Diese Katalogorganisation
ist in [plot_qa.json](../results/m4/plot_qa.json) belegt und bei neuer Ausgabe vor der Abnahme ebenfalls herzustellen; sie verändert keine Daten oder Plots.

Wesentliche Dateien: `m4/{relaxation,warm_start,training,noise,metrics,verification, manifest,benchmark,audit,reporting,acceptance,cli}.py`, neue M4-Tests,
`configs/m4_baseline.json`, `data/synthetic/m4/{provenance,generator_configs, selection_audit,references,benchmark_manifest}.*`, `results/m4/{preparations,controls,
training,traces,compiled,records}`, [Summary](../results/m4/summary.json), [Audit](../results/m4/audit.json), [Tooling](../results/m4/tooling.json),
[Referenzintegrität danach](../results/m4/reference_integrity_after.json) sowie beide [Qualitäts-](../results/m4/preflight/independent_quality_review.md) und
[Noise-Reviews](../results/m4/preflight/independent_noise_review.md).

```text
uv sync --frozen
uv run km-quantum m4 manifest
uv run km-quantum m4 benchmark --dry-run
uv run km-quantum m4 verify --output-directory results/m4-reproduction
uv run km-quantum m4 benchmark --output-directory results/m4-reproduction
uv run km-quantum m4 summarize --output-directory results/m4-reproduction
```

Eine neue Pflichtausführung benötigt einen sauberen Code-Commit; bei einer Unterbrechung setzt `m4 benchmark --resume --output-directory results/m4-reproduction` sie mit geprüften Rohdaten fort.
Die in `benchmark_execution.json` gemessene Dauer gehört zur jeweiligen Invocation; die originale Hauptkampagnenzeit dieses Berichts stammt aus der vollständigen ersten Ausführung.
Nach tatsächlichen Tooling-Prüfungen und
Katalogorganisation erzeugt `m4 acceptance --tooling results/m4/tooling.json` die Abnahme. Für eine andere Ausgabe ist das passende tatsächlich aufgezeichnete Tooling zu verwenden.

| Tatsächlich verwendeter Stack | Version |
| --- | --- |
| Python / Qiskit / Aer | 3.12.15 / 2.5.2 / 0.17.2 |
| NumPy / SciPy / Matplotlib | 2.5.3 / 1.18.1 / 3.11.2 |
| OR-Tools / Pydantic | 9.15.6755 / 2.13.5 |
| psutil / threadpoolctl | 7.2.2 / 3.7.0 |
| pytest / Ruff / mypy | 9.1.1 / 0.16.10 / 1.20.2 |

Alle 240 Trainings-/1.200 Noise-Records stammen von `27f2b9e82fd76394f9860f455953eacc66b3cc42`, **dirty=false**. Quellinhalt-Hash:
`066ee057437dce5db4b04e6f61496827e7ca57a92f621d121285e260eb23c750`. M3-Archiv: `98bbe8bc4106f79372d71560eb9a324edb6fe15e`; gemeldeter M3-Abschluss:
`f2a26ed75fe0c44120df45a6e46c2f0cc47b0fa8`; tatsächliches M3-Training: `1f0cdd9f2dd4331a6b637ed24d7fb9d783460add`.

| M4-Commit | Zweck |
| --- | --- |
| `d4e8cd936ba8e634fb6a6b6aba2e3e61fef92dd8` | M3-Provenienz sichern |
| `482e8dd53ed0c7df42f7caeb639daf9095e7539a` | Aer und Thread-Budgets pinnen |
| `6c56ca1c4f677815afa7535201b499c9494bf961` | Planmetriken und Warm-Start |
| `993080aafd969c9052d0eeead61048256db78e38` | Circuit-/Noise-Verifikation |
| `1e4ff15bfd3f2071c4c641ed64b0d0d45f0ca7d2` | eingefrorenes Protokoll und Workflow |
| `27f2b9e82fd76394f9860f455953eacc66b3cc42` | vollständiger Ledger und Phasenkosten; tatsächlicher Hauptlauf |
| `4ee735d73fd369d2fcc65ad7b5a3e9de48c9b0ae` | vollständiges Roharchiv und finaler Resume-Smoke |

Dokumentations-/Abnahmecommits sind über `git log -- docs/m4_completion_report.md` nachvollziehbar. M4 wurde auf Nutzerauftrag vollständig nach
`origin/feat/m4-noise-warm-start` gepusht. **In dieser Arbeit wurde kein M4-PR erstellt, kein Merge und kein Force-Push durchgeführt.**
Die vor dem Hauptlauf gespeicherte Statusprüfung dokumentiert den damaligen lokalen Stand; Forschungsdaten und Abnahme bleiben unverändert.

| Hashdefinition / Artefakt | SHA-256 |
| --- | --- |
| kanonischer Manifestinhalt | `9ac70aa0ffd8c8ed145b034edc5ab2a014e841504ffc843bd56e546f012b45f0` |
| kanonische Konfiguration | `74380406a5a1b53e4ddafd9a982833e56b93a05821ccd6bc2803539147165dc4` |
| uv.lock, LF / hier auch rohe Bytes | `d1dc60673534ed2a191fc8c6ce221316fc387a9654d490c8340469716be7320f` |
| Referenz-Provenienz, rohe Datei | `145806867516dc916b9f77c0a3676bffe277c8d07d96d1eabd168f46e57d9ae1` |
| neue Referenzen, rohe JSONL-Datei | `13408920938fcd29e697141c364c0308f9312fc7b720654599faecc712c5fb06` |
| Runs-Export, rohe Datei | `3d082725bc601eec532e508f091991afd3edba0d7b97f2a1faa9cd2908d58d7d` |
| Artefaktindex, rohe Datei | `23cf35120e8af00ccead8db94514cc6d9f78e5943e1dc381af653087080e0e21` |

Provenienz speichert jeden Vorher-Hash; die Nachher-Datei enthält die 2.107 aktuellen Hashes. Portable LF-Normalisierung wird separat von roher Bytegleichheit ausgewiesen.
Primärquellen und eigene Herleitung: [Egger, Mareček, Woerner](https://arxiv.org/abs/2009.10095), [IBM Warm-Start](https://quantum.cloud.ibm.com/docs/en/tutorials/warm-start-qaoa),
[AerSimulator](https://qiskit.github.io/qiskit-aer/stubs/qiskit_aer.AerSimulator.html), [NoiseModel](https://qiskit.github.io/qiskit-aer/stubs/qiskit_aer.noise.NoiseModel.html),
[Depolarisation](https://qiskit.github.io/qiskit-aer/stubs/qiskit_aer.noise.depolarizing_error.html) und [ReadoutError](https://qiskit.github.io/qiskit-aer/stubs/qiskit_aer.noise.ReadoutError.html).
Tutorial-Code, Vorzeichen und kontinuierliche Fortsetzungen wurden durch eigene Matrix-/Gradient-/Grenzfalltests abgesichert, nicht ungeprüft übernommen.
