# M5 — Budget B und Bereitschaft des neuen konkreten Plans

Stand: 2026-10-12T17:00:50.707624+00:00. **READY_FOR_APPROVAL.** Die ausdrücklich genehmigte Budgetänderung
ist versioniert umgesetzt und lokal geprüft. Die Budgetgenehmigung betrifft
die Vorbereitung mit 20/80/80 Sekunden und 180 Sekunden insgesamt. Die konkrete
Hardwarefreigabe bleibt **nicht erteilt**. **Null QPU-Jobs eingereicht.**
`hardware_enabled=false`, `submission_approved=false`, `hardware_used=false`.
Es gibt keine Hardwaremessungen und keinen M5-Hardware-PASS.

## Budgetgenehmigung und unveränderte Schutzregeln

Der unveränderte Default `m5-budget-v1` bleibt bei 40 Sekunden je Job und
120 Sekunden insgesamt. Historische Konfigurations-, Plan- und Schätzerseals
werden ohne neue Versiegelung gelesen. Der historische blockierte Plan bleibt
`a2288adfb1bd56336ef7df8ff33de4ed9085689c71c9537d655fd4cb473c17ac`; seine bisherigen BLOCKED-Artefakte wurden nicht verändert.

`m5-budget-b-v2` benötigt die vollständige versiegelte Budgetgenehmigung und
passende Grenzen. Konfiguration, Optionen, Plan, Schätzer und Ledger binden
20/80/80 Sekunden je Job und 180 Sekunden Maximalreservierung. Drei Einreichungen,
52 PUBs, 49.664 Shots, höchstens ein eigener ungeklärter Job gleichzeitig,
Open/free und null Euro bleiben zwingend. Unklare Jobs behalten ihre Reservierung;
fehlgeschlagene Jobs gelten nicht pauschal als kostenlos. Globale und individuelle
Runtimeoptionen sind plan- und jobgebunden.

Die [Originalnachricht](m5_budget_b_authorization.md) wurde unverändert in UTF-8
archiviert. Quellenhash: `ec9064416da9a62932ff81dd99cb9ff21077179190edb992d63cdcc7104625da`.
Budgetrecord: `835fa22228731d13c9f7c54bb26f4d6b972b2bd2d13ab98d0677faa13e3e9ff7`, `scope=budget_only`, Ausgangscommit
`5c186536d9b383d66d7a209f2835ccf3748e9b3e`, Erfassungszeit `2026-10-12T15:09:57.286214+00:00`.
Der Zeitpunkt dokumentiert die Nachricht. Der getrennte neue
`approval_request.json` bleibt `approved=false`; alte Hardwarefreigaben und
Planhashes wurden nicht übertragen.

## Tatsächlich ausgeführte Qualitätssicherung

Neue gezielte Prüfung: **217 PASS**; tatsächlicher Logabschluss:
`217 passed, 56 warnings in 109.25s (0:01:49)`. Vollständiger finaler Lauf: **1607 PASS**;
`1607 passed, 305 warnings in 336.56s (0:05:36)`. Ruff check bestand, Ruff format: `130 files already formatted`;
mypy: `Success: no issues found in 77 source files`. `uv build --offline` erstellte Wheel und sdist.
Alle sechs Prüfläufe hatten Exit 0 und denselben Sourcekontext vor/nach den
Checks, siehe Hashbindung unten. Keine Dependency-Upgrades.

Der erste vollständige v1-Lauf bleibt als fehlgeschlagener Nachweis erhalten:
1.603 PASS / vier FAIL. Drei Mock-Anpassungen in
`tests/test_m5_integration_safety.py` ergänzten die neue Config-Ladeabhängigkeit
für vier synthetische Fixtures. Produktionsgates blieben bestehen; keine Tests
wurden deaktiviert. Danach wurde die gesamte Suite erneut ausgeführt.

Die Paketprüfung bestätigt private Pfade als ausgeschlossen. Der sdist enthält
die zwei alten öffentlichen, lokal ungetrackten Berichte; das Wheel enthält sie
nicht. Kein Paketupload. Rohhashes, Commandlogs, JUnit, Exitcodes und Zeitstempel
stehen im [neuen QA-Verzeichnis](../results/m5-budget-b-20261012-v2/tooling.json)
und [Paketcheck](../results/m5-budget-b-20261012-v2/package_inspection.json).

## Neuer lesender Provider-Preflight und konkrete lokale Prüfung

Verwendet wurden der vorhandene Named Account `km3-m5-open`, ausschließlich
seine gespeicherte Instanz und der Windows-Projektinterpreter. Runtime `0.47.0`,
Qiskit `2.5.2` und Aer `0.17.2` bleiben gepinnt. Keine erneute Einrichtung,
Credentialkopie, andere Instanz oder kostenpflichtige Alternative.

| Prüfung | Gespeicherter tatsächlicher Nachweis |
| --- | --- |
| Providerstatus | `PROVIDER_PREFLIGHT_READY` |
| Account-/Nutzungsbeobachtung | `2026-10-12T16:57:53.044845+00:00` |
| Plan / Preis / Kosten | `open` / `free` / 0 EUR |
| Nutzung | 0 s verbraucht; 600 s Limit; 600 s verbleibend |
| Exhaustion-Flag | `false` |
| Offene Jobinventur | `VERIFIED`, vollständig, 0 offene Jobs |
| Backend / Status / Region | `ibm_fez` / `active` / `us-east` |
| Physischer Bereich | `[0, 1, 2, 3, 4, 16, 5]` |
| Tatsächlicher Kalibrationszeitpunkt | `2026-10-12T18:22:41+02:00` |
| Snapshot-Abruf | `2026-10-12T16:53:39.449922+00:00` |
| Rep-Delay | 0.00025 s; unveränderter expliziter Backenddefault |
| Lokale Vorbereitung | 28 native Schaltungen; 84 QPY-Artefakte; 28 gerätebezogene Noise-Vorhersagen READY |
| verify-local | `PASS`, Mapping und QPY-Roundtrips bestätigt |
| Maximale ideale Wahrscheinlichkeitsabweichung | 2.77555756156e-15; unveränderter Guard 1e-10 |
| Maximale Offset-Energieabweichung | 1.17950094136e-12; unveränderter Guard 1e-8 |
| Maximale Abweichung der vier Metriken | 2.77555756156e-15; unveränderter Guard 1e-10 |
| Aktive Simulationsbreite | höchstens 7 Qubits |
| Planstatus | `READY_FOR_APPROVAL` |
| Read-only acceptance | `OFFLINE_READY`; Implementierung `PASS`; Hardware `NOT_STARTED` |

Kontodaten sind Momentaufnahmen. Die lokalen Folgekommandos erneuern keinen
Providerzeitstempel. Das fünfminütige Fenster bleibt bestehen: Vor späterer
Hardwarefreigabe/Einreichungsprüfung sind Konto, Nutzung, Bindung und offene/
unklare Jobs erneut zu lesen. Kalibrations- und Accountzeiten bleiben getrennt.

## Konkreter Plan, Schätzung und Reserven

Plan-ID / Hash: `e863b07c25dc95dd9b5350624dc000c96a87c23d5902764c2cc08aeca242db38`.
Backend: `ibm_fez`. Umfang unverändert: Smoke viermal 128 Shots;
jeder Hauptblock 24-mal 1.024 Shots. Drei geplante Jobs, 52 PUBs und 49.664
tatsächliche Executions. Die Smoke-PUBs wurden ebenfalls nicht eingereicht.
Aktuelle lokale Reservierung: **0 Sekunden / 0 Shots**.

Zeitwerte sind Sekunden. Die Basis nimmt einen internen Subjob je Runtimejob an;
das unveränderte lokale Risikoszenario einen je expandiertem Circuit mit Faktor
1,25. [IBM](https://quantum.cloud.ibm.com/docs/en/guides/estimate-job-run-time)
nennt ungefähr zwei Sekunden je internem Subjob. Die angenommene Unterteilung
bleibt eine lokale Richtlinie und wurde nicht günstiger gewählt.

| Job | PUBs / Circuits | Executions | Basis s | Risiko s | Cap s | Reserve s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Smoke | 4 / 4 | 512 | 2.129721344 | 10.162151680 | 20 | 9.837848320 |
| Hauptblock 1 | 24 / 24 | 24576 | 8.266077184 | 67.832596480 | 80 | 12.167403520 |
| Hauptblock 2 | 24 / 24 | 24576 | 8.266077184 | 67.832596480 | 80 | 12.167403520 |
| Gesamt | 52 / 52 | 49664 | 18.661875712 | 145.827344640 | 180 | 34.172655360 |

| Job | nativ inkl. Readout s | Init/Reset s | Rep-Delay s | fixer Risikooverhead s | Aufschlag s |
| --- | ---: | ---: | ---: | ---: | ---: |
| Smoke | 0.000859136 | 0.000862208 | 0.128 | 8 | 2.032430336 |
| Hauptblock 1 | 0.080691200 | 0.041385984 | 6.144 | 48 | 13.566519296 |
| Hauptblock 2 | 0.080691200 | 0.041385984 | 6.144 | 48 | 13.566519296 |

Readout liegt genau einmal im nativen kritischen Pfad; Initialisierung und
Rep-Delay werden pro Execution addiert. Der analytische feste Risikoanteil bei
verschwindenden Shots bleibt 130 Sekunden; dies ist kein Null-Shot-Plan. Die
neuen B-Grenzen ändern das lokale Budget. Kontingent, Schätzung, Runtimecap und
später gemessene QPU-Nutzung bleiben verschiedene Größen.

| Bindung | Hash |
| --- | --- |
| Sourcekontext | `d9d8582c06faaeeae63582bce4288c87e4d1b3113028296f762e0261b178b5af` |
| Targetschnappschuss | `e1548ca479ead0e822b3e8cd1224bb544238b51e151832e0dc4dd658a4ba6b3f` |
| Vorbereitung | `95225b310fa79cdd602201f290e182ab30dc9a4b9eddeab736d3928f567cac3f` |
| Nutzungsmodell | `30f49e949a20f7ed76be14d248319069aa5188ce8cfe09711a07f44cdbf3aca0` |
| Providerlimitassessment | `fc8bad0f707a6f9ec67317490b4bd0ad2acafee0b765604d392f9afbd8f8ca96` |
| Budgetgenehmigung | `835fa22228731d13c9f7c54bb26f4d6b972b2bd2d13ab98d0677faa13e3e9ff7` |

Executions nach Broadcasting, native Instruktions-/Gatter- und lokale
Payloadgrenzen bestanden. Nicht veröffentlichte Providerlimits bleiben
unbekannt; lokale Serialisierung und Reservierungen ersetzen keine Providerfakten.

## Erhaltung, Git und Einschränkungen

Branch: `codex/m5-budget-b-implementation`.
Geprüfter Implementierungscommit: `d6aa135a23f795d32087165f328a277ac1032b29`.
HEAD beim Erstellen dieses Berichts: `d6aa135a23f795d32087165f328a277ac1032b29`. Ein abschließender
Dokumentationscommit wird separat im finalen Gitstatus erfasst, damit der
Bericht keine zyklische Selbst-SHA behauptet.

- `8dc75063a66fe51351d399acaee8d78381a01e0d` — docs(m5): archive explicit budget B authorization
- `d6aa135a23f795d32087165f328a277ac1032b29` — feat(m5): bind approved budget B across planning and execution guards

Die 6.090 Baseline-Dateien umfassen 5.878 alte Resultate und 933 historische
QPY-Dateien. Der abschließende unabhängige Erhaltungscheck weist neue Artefakte
und tatsächlich veränderte Fixpfade getrennt aus. M4-Erhaltung: **4176 geschützte
Referenzdateien PASS**, veröffentlichter Commit
`4ca11bf8f709aef9f43df4d3f2a0867189b54a7c`. Winkel, Fälle, Tiefen, Seeds,
Kontrollen und historische native Schaltungen bleiben erhalten.
Die zwei zuvor ungetrackten alten Berichte bleiben unverändert ungetrackt;
sie wurden weder automatisch gestaged noch in neue Commits aufgenommen.
Kein Merge, Force-Push oder Remote-Push.

Die Schätzung ist kein IBM-Angebot und keine garantierte obere Schranke;
Partitionierung, Kontrollaufwand und zeitliche Ausrichtung können abweichen.
Noise approximiert Kalibration; Idle-Zeitpläne, Crosstalk und Drift bleiben
unvollständig modelliert. Lokale Verteilungen sind keine Hardwaremessungen.
Vier Fälle und zwei Wiederholblöcke belegen keine Warm-Start-Überlegenheit.

Belege: [Budgetrecord](../configs/m5_budget_b_authorization.json),
[Tooling](../results/m5-budget-b-20261012-v2/tooling.json),
[Provider](../results/m5-budget-b-20261012-v2/provider/provider_preflight.json),
[Plan](../results/m5-budget-b-20261012-v2/provider/execution_plan.json),
[Nutzungsmodell](../results/m5-budget-b-20261012-v2/provider/local_usage_model.json),
[Lokale Prüfung](../results/m5-budget-b-20261012-v2/provider/local_verification.json),
[Abnahme](../results/m5-budget-b-20261012-v2/provider/m5-acceptance.json).
Der neue [versiegelte Bereitschaftsnachweis](../data/synthetic/m5/budget_b_readiness_20261012.json)
bindet diese Artefakte und hält die getrennte Budget-/Hardwarefreigabe fest.

**Null QPU-Jobs eingereicht. Kein submit-next im Hardwarebetrieb, kein Smoke-Job, kein
Sampler-/Estimator-Hardwarelauf und keine selbst erteilte Hardwarefreigabe.**

## Abschließende frische Kontobestätigung

Am `2026-10-12T17:10:27.501386+00:00` bestätigt ein zusätzlicher rein lesender
Preflight für denselben Plan erneut Open/free, 0 EUR, 600 Sekunden Restkontingent,
0 verbrauchte Sekunden und eine vollständige Inventur mit 0 offenen Jobs.
Alle konkreten Kalibrations-, Target- und Mappingfelder stimmen unverändert mit
dem eingefrorenen Snapshot überein. Der Planhash bleibt unverändert.
[Abschlussbestätigung](../data/synthetic/m5/budget_b_final_confirmation_20261012.json)
bindet diese frischen Nachweise an den bereits versiegelten Bereitschaftsstand.

Die erste Zusatzkontrolle hatte den absichtlich leeren generischen
`calibration_timestamp`-Backendattributwert mit dem gespeicherten SDK-Zeitpunkt
verglichen. Die korrigierte Kontrolle übernimmt den echten Zeitpunkt aus
`backend.properties().last_update_date`, genauso wie die bestehende Vorbereitung,
und prüft alle Felder weiterhin strikt. Die ursprüngliche Diagnose und der
korrigierte Nachweis liegen getrennt vor; Produktcode und Tests wurden hierfür
nicht geändert. Vor einer späteren Hardwareeinreichung sind alle dynamischen
Nachweise erneut frisch zu prüfen.

## Abschließende Artefaktprüfung

Die [Artefaktprüfung v2](../results/m5-budget-b-20261012-v2/independent_review_v2.json)
besteht mit 26 geprüften Punkten. Der erste Review verglich irrtümlich den
Hash des gesamten versiegelten Noise-Snapshots mit dessen abweichendem
Payloadseal. Die korrigierte Prüfung verifiziert beide tatsächlich verwendeten
Hashbindungen separat für alle 28 Vorhersagen. Der erste Review bleibt
unverändert erhalten; es wurden keine Produktgates oder Toleranzen gelockert.
Alle 5.878 historischen Ergebnisdateien und 933 alten QPY-Dateien sind roh
unverändert. Bei 6.090 Baseline-Dateien wurden genau die elf vorgesehenen
Quell-/Testpfade geändert; die beiden alten Berichte bleiben ungetrackt.
