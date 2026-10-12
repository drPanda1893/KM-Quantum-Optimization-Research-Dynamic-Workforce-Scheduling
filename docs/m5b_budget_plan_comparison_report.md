# M5B — Laufzeitaufschlüsselung und lokaler Planvergleich

Stand: 12. Oktober 2026. Genau zwei neue, **nicht ausführbare** Beratungsvarianten
wurden auf demselben eingefrorenen `ibm_fez`-Schaltungs-/Targetstand berechnet.
**A bleibt BLOCKED / LOCAL_CAMPAIGN_LIMIT_EXCEEDED. B bleibt
OWNER_BUDGET_APPROVAL_REQUIRED.** Es gibt keinen READY_FOR_APPROVAL-Status und
keinen Hardware-PASS. **Null QPU-Jobs, keine Hardware-Smoke- oder Hauptmessung.**
`hardware_enabled=false`, `submission_approved=false`.

## Ausgangspunkt und unabhängige Begriffe

Geprüfter Ausgangsbranch: `fix/m5b-preflight-contracts`;
vollständiger HEAD `5d771d227ddf5036007173f8eac1c45bb46c044f`.
Originaler blockierter Plan: `a2288adfb1bd56336ef7df8ff33de4ed9085689c71c9537d655fd4cb473c17ac`.
Neuer Planungsbranch: `codex/m5b-budget-plan-comparison`.
Der vollständige übermittelte Auftrag wurde unverändert als
`KM3_M5B_Budget_Plan_Comparison_Prompt.md` gesichert.

| Begriff | Nachweis / Bedeutung |
| --- | --- |
| Providerrestnutzung | Historischer lesender Abruf am 12. Oktober 2026, 15:42:46 Uhr Berlin (Originalzeit `2026-10-12T13:42:46.896662+00:00`): Open/free, 600 s verfügbar, keine offenen Jobs. In diesem Vergleich nicht erneuert; keine aktuelle Erreichbarkeit/Quote behauptet. |
| Aktive lokale Freigabegrenzen | 40 s je Job, 120 s gesamt, höchstens drei Jobs, einer ungeklärt gleichzeitig, 49.664 Shots, 0 Euro und verifizierter kostenloser Open Plan. Unverändert. |
| Basisschätzung | Ein angenommener interner Teiljob je Runtimejob mit ca. 2 s Aufwand plus individuell gewichtete Shotzeit. |
| Lokales Risikoszenario | Ein angenommener interner Teiljob je expandierter Schaltung, derselbe nominelle 2-s-Aufwand und Faktor 1,25 auf die Summe. Lokale Annahme, kein IBM-Fakt. |
| Serverseitiges `max_execution_time` | Im historischen Ausführungsplanschema weiterhin 40 s. B erzeugt keine Runtimeoptionen mit 80 s. Schutzgrenze für QPU-Nutzung, keine Laufzeitprognose. |
| Tatsächlich gemessene spätere QPU-Nutzung | Nicht vorhanden; keine Hardwarejobs. Die 600 s Kontingent erlauben kein eigenmächtiges Budget-Amendment. |

Die [IBM-Nutzungsbeschreibung](https://quantum.cloud.ibm.com/docs/en/guides/estimate-job-run-time)
nennt ungefähr zwei Sekunden pro internem Teiljob; sie behauptet keine
allgemeine Regel zwei Sekunden pro PUB. Die bestehende Rechnung bezeichnet
diese Gleichsetzung ausdrücklich als lokale Vorsichtsannahme. PUB-Broadcasting
und interne Teiljobs sind verschiedene Begriffe. In diesem eingefrorenen
Versuch sind die vollständig gebundenen PUBs skalar; die lokalen 4/24/24
Teiljobs bleiben für beide Varianten unverändert. Eine günstigere Partition
wurde nicht nachträglich gewählt. Zusätzliche interne Aufteilung ist unbekannt;
das Risikoszenario ist weder Providerangebot noch garantierte obere Schranke.

Die [IBM-Ausführungszeitgrenze](https://quantum.cloud.ibm.com/docs/en/guides/max-execution-time)
bezieht sich auf QPU-Nutzung und kann einen Job abbrechen. Queue-/Client-Wandzeit
und tatsächlich gemessene Usage sind davon getrennt. Die
[Empfehlung zur Shotreduktion](https://quantum.cloud.ibm.com/docs/en/guides/minimize-time)
wird hier auf den shotabhängigen Anteil angewandt; feste Modellkosten werden
vollständig erhalten. SDK-Pins bleiben Qiskit 2.5.2, Aer 0.17.2 und Runtime 0.47.0.

## Nachgeprüfte 67,83 Sekunden

Alle Zeiten in Sekunden. Budgets werden mit ungerundeten Werten verglichen.
Keine Rundung wurde als zusätzliche Zeitersparnis ausgegeben.

| Originaljob | PUBs | expandierte Schaltungen | Shots pro Schaltung | Executions | Basis s | Risiko s | Abstand zu 40 s |
| --- | ---: | ---: | --- | ---: | ---: | ---: | ---: |
| `smoke` | 4 | 4 | 128 | 512 | 2.129721344 | 10.162151680 | 29.837848320 |
| `main-block-1` | 24 | 24 | 1024 | 24,576 | 8.266077184 | 67.832596480 | -27.832596480 |
| `main-block-2` | 24 | 24 | 1024 | 24,576 | 8.266077184 | 67.832596480 | -27.832596480 |

| Originaljob | native ohne Readout s | Readout-Inkrement s | Init/Reset s | rep_delay s | nominaler fester Aufwand s | Aufschlag fest s | Aufschlag shotabhängig s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `smoke` | 0.000009216 | 0.000849920 | 0.000862208 | 0.128000000 | 8.000 | 2.000 | 0.032430336 |
| `main-block-1` | 0.039895040 | 0.040796160 | 0.041385984 | 6.144000000 | 48.000 | 12.000 | 1.566519296 |
| `main-block-2` | 0.039895040 | 0.040796160 | 0.041385984 | 6.144000000 | 48.000 | 12.000 | 1.566519296 |

Die native Dauer wurde zusätzlich direkt aus den QPY-Daten und gespeicherten
SI-Instruktionsdauern über die quanten-/klassischen Drahtabhängigkeiten geprüft.
Ein zweiter ausschließlich diagnostischer Durchlauf setzt Readoutdauern auf
null. Das **Differenzinkrement der kritischen Pfade** trennt Readout korrekt,
auch wenn dabei der kritische Draht wechselt. Es ist keine Summe parallel
ausgeführter Messdauern. Die Initialisierung ist der parallele Reset-Maximalwert
der aktiven physischen Qubits; explizite Circuit-Resets bleiben eigene Arbeit.
Alle archivierten Circuitzeiten passen bei absolut 1e-15 s. Weder QPY noch
Noise-Schaltung oder Snapshot werden verändert. Diese Diagnose rekonstruiert
keinen vollständigen Live-Target inklusive fehlendem dt/Alignmentkontext.

Je Hauptjob:

`Shotzeit = 0,039895040 + 0,040796160 + 0,041385984 + 6,144 = 6,266077184 s`.

`Basis = 2 + 6,266077184 = 8,266077184 s`.

`Risiko = 48 + 12 + 6,266077184 + 1,566519296 = 67,832596480 s`.

Dabei sind 48 s der angenommene Aufwand für 24 Teiljobs; 12 s dessen
25%-Aufschlag. Die übrigen 1,566519296 s sind der gesonderte 25%-Aufschlag auf
die Shotzeit. Der Backend-Rep-Delay bleibt exakt 0,00025 s je Execution.
Winkel, Resetentscheidung und Compiler-/Noise-Optionen bleiben erhalten.

Im rein theoretischen Grenzfall Shots→0 bleiben **10/60/60 s, insgesamt 130 s**
Risikobeitrag. Das ist keine zulässige Null-Shot-Planvariante. Schon der feste
Anteil überschreitet 40 s je Hauptjob und 120 s gesamt: **Shotreduktion allein
kann unter unveränderter Unterteilungsannahme die aktiven Grenzen nicht erfüllen.**

## Original gegenüber genau A und B

Alle Fälle A/E/H/K, Standard/Warm-Start, p=1/2, M4-Restart 0, acht
Initialverteilungskontrollen, vier Smoke-Kontrollen und zwei getrennte
Hauptblöcke bleiben vollständig erhalten. Originale Schaltungen und Verteilungen
werden mit ihren Referenzhashes wiederverwendet, ohne neue Winkel zu trainieren.

| Referenz/Variante | Hauptshots/PUB | Gesamtshots | Jobs / PUBs | Basis gesamt s | Risiko Smoke/Main1/Main2 s | Risiko gesamt s | fester Risikobeitrag s | maximale Reservierungsvorlage s | Status |
| --- | ---: | ---: | --- | ---: | --- | ---: | ---: | --- | --- |
| Originalreferenz | 1.024 | 49.664 | 3 / 52 | 18.661875712 | 10,16215168 / 67,83259648 / 67,83259648 | 145.827344640 | 130 | 40/40/40 = 120, aktiv | BLOCKED; lokales Kampagnenlimit |
| A | 512 | 25.088 | 3 / 52 | 12.395798528 | 10,16215168 / 63,91629824 / 63,91629824 | 137.994748160 | 130 | 40/40/40 = 120, aktiv | BLOCKED / LOCAL_CAMPAIGN_LIMIT_EXCEEDED |
| B | 1.024 | 49.664 | 3 / 52 | 18.661875712 | 10,16215168 / 67,83259648 / 67,83259648 | 145.827344640 | 130 | **20/80/80 = 180, nur vorgeschlagen** | OWNER_BUDGET_APPROVAL_REQUIRED |

A zählt unabhängig 512 Smoke-Executions plus 12.288 je Hauptblock = 25.088.
Nur die gewichteten Haupt-Shotzeiten werden halbiert: 3,133038592 s je Hauptjob.
Der feste Risikobeitrag bleibt 60 s, der variable Aufschlag wird 0,783259648 s.
Es ergibt sich **63,91629824 s** je Hauptjob, kein 33,91629824-s-Halbierungswert.
A spart insgesamt 7.832596480 s, nur 5.371% der Risikoschätzung.
Seine Abstände: Smoke +29,83784832 s, beide Hauptjobs −23,91629824 s,
Kampagne −17,99474816 s. Keine weitere Shot-/Fall-/Tiefen-/Jobreduktion.

B behält sämtliche 49.664 Shots und die ursprüngliche Schätzung. Innerhalb der
**nur vorgeschlagenen** Grenzen liegen die Reserven bei +9,83784832 s Smoke,
+12,16740352 s je Hauptjob und +34,17265536 s gesamt. Dies ist ausschließlich
ein rechnerischer Modellvergleich. Aktive 40/120-Guards, Runtimeoptionen,
Freigabedateien und Ledger wurden nicht angepasst. Tatsächlich wurden **null
Sekunden und null Shots reserviert**. Erst eine separate menschliche
Budgetentscheidung und ein gesonderter Implementierungs-/Prüfschritt könnten
später einen passenden konkreten Hardwareplan ermöglichen.

Der bestehende Submissionvertrag verlangt weiterhin 1.024 Hauptshots und den
ursprünglichen vollständigen Plan. Auch A ist deshalb ein Beratungsentwurf;
kein 512-Shot-Plan wird still in die bestehende Submission-CLI eingeführt.
Beide Artefakte enthalten keine ausführbaren `jobs`, `runtime_options` oder
`plan_hash`-Felder. Ein lokaler Entwurfshash ist keine Freigabe.

## Messgenauigkeit bei 512 statt 1.024

Bei identischer wahrer Wahrscheinlichkeit gilt pro Circuit und Block
`SE=sqrt(p*(1-p)/N)`: 512 Shots ergeben √2 ≈ 1,414214 mal so großen
Standardfehler und die doppelte Varianz. Bei p=0 oder 1 ist die theoretische
Bernoulli-Varianz null; ein 0/0-Verhältnis wird nicht als Messbeweis ausgegeben.
Die theoretische ideale Verteilung selbst ändert sich nicht.

| Allgemeine Einzelmetrik | 1.024 Shots | 512 Shots |
| --- | ---: | ---: |
| Größter SE bei p=0,5 | 1,5625 Prozentpunkte | 2,20970869 Prozentpunkte |
| Hypothetisch null Treffer: exakte zweiseitige 95%-Obergrenze | 0.359594041% | 0.717895003% |

Die Null-/Ein-/Zwei-Trefferintervalle werden mit SciPys bestehender exakter
Binomialmethode für die **jeweils tatsächliche N** neu berechnet und eindeutig
als hypothetisch, nicht beobachtet markiert. Null Treffer sind kein Nachweis
einer genau null großen Wahrscheinlichkeit. Die Intervalle beziehen sich
jeweils auf eine Ereignismetrik eines Circuits in einem Block; keine gemeinsame
Konfidenzaussage über sämtliche Vergleiche wird behauptet.

Vier getrennte M4-Ereignisse; folgende Bereiche beziehen sich auf die 24
gespeicherten gerätebezogenen Referenzverteilungen, **keine QPU-Messungen**:

| Metrik | Referenz-p min–max | SE 1.024 min–max, Prozentpunkte | SE 512 min–max, Prozentpunkte |
| --- | --- | --- | --- |
| `p_ground_encoding` | 0.005547–0.779404 | 0.232102–1.562208 | 0.328242–2.209295 |
| `p_schedule_optimal` | 0.059114–0.779404 | 0.736996–1.562208 | 1.042270–2.209295 |
| `p_encoding_feasible` | 0.031643–0.779404 | 0.547024–1.562436 | 0.773609–2.209618 |
| `p_schedule_feasible` | 0.115573–0.779404 | 0.999099–1.562436 | 1.412939–2.209618 |

Zusätzlich wurden für alle 24 Circuits beide idealen/gerätebezogenen
Vorhersagereferenzen verglichen: 48 Referenzzeilen mit beiden Shotzahlen und
den vier Metriken. Die finite Histogramm-TV-Referenz wird mit 1.000 **neuen
lokalen Multinomialziehungen je Referenz und Shotzahl** und dem unveränderten
Seed 515151 berechnet. 2,5/50/97,5%-Quantile stehen im neuen Statistikartefakt.
Keine alte 1.024-Shot-Samplingdatei wird als 512-Shot-Messung ausgegeben.
Die zwei Blöcke behalten getrennte Referenzen und werden nicht automatisch
gepoolt. Unabhängigkeit/iid wird angenommen, Drift und Korrelation sind nicht
erfasst; gute Referenzübereinstimmung bestätigt kein Noise-Modell.

## Prüfungen, Erhaltung und Git

Neue gezielte Prüfung: **163 passed in 10.97s**. Neue vollständige Prüfung:
**1524 passed, 249 warnings in 345.06s (0:05:45)**. Ruff `check`, Ruff `format --check`, mypy einschließlich
des Skripts und `uv build --offline` bestanden tatsächlich. Quellenkontext
und Skript-Rohhash sind vor/nach jedem finalen Check identisch; die alten
1.442 Tests werden nicht als neuer Lauf ausgegeben. Vollständige Belege:
[Tooling](../results/m5b-budget-comparison-20261012-v1/tooling.json).

82 neue Tests prüfen feste/shotabhängige Anteile, Heterogenität/Broadcasting,
die theoretische Untergrenze, tatsächliche A-Zählung, Budgetgrenzen
einschließlich Smoke unter/Main über dem Cap, unveränderte aktive Limits,
die Ablehnung beider Entwürfe durch Hardwareplan-/Freigabegates, Hash- und
Modellbindung, gerichtete DAG-/Readout-/Resetzeit, N512/N1024 und exakte
Intervalle. Auch die vorhandenen Nutzungs- und Approvaltests wurden gezielt
ausgeführt. Keine bestehenden Tests oder Prüfungen wurden deaktiviert.

Originalsourcekontext: `12506cd22bf0effab7e7445ca601aa1f1e696483951a4df6466dc18b78a750d4`.
Neuer Vergleichskontext: `aad662c8b370247993b65b93251b1cd1b6ce92669e8647efc1968aeeaccf86c4`.
Vergleichsskript-Rohhash: `657ddb363ed05f887aab23849a81433cc2e1494fd4b1359f852425e06daeabb0`.
Targetsnapshot: `9ccbc1391bd9e218700420d012ae7468e21d5238a6342cba6953878530f91711`;
Abruf am 12. Oktober 2026, 15:37:01 Uhr Berlin
(Original `2026-10-12T13:37:01.248880+00:00`),
Kalibration am selben Tag, 15:11:31 Uhr Berlin (`2026-10-12T15:11:31+02:00`). Die Ausgangs-
Konfigurations-/Targetdauernhashes sind pro Job im Timingartefakt angegeben;
die neuen geänderten Shotannahmen sind im Entwurfshash vollständig gebunden.
Kein alter Source-/Targethash wurde erneuert, um die Provenienz zu verdecken.

Der unabhängige Abschlussreview besteht mit 21 Checks: sechs neue Artefaktseals,
84 Original-QPY-Rohhashes, 5.885 unveränderte Ausgangsdateien, beide
Variantenrechnungen, tatsächliche N512/N1024 und die Ablehnung durch
Plan-/Freigabegates wurden unabhängig bestätigt. Ein anfänglich falsch
erwartetes Statistiklabel wurde als Audit-Fehlannahme transparent dokumentiert;
das korrekte `device_noise_after_readout` und sämtliche Originalartefakte
blieben unverändert. Keine Produktionsprüfung wurde gelockert.
[Unabhängiger Review](../results/m5b-budget-comparison-20261012-v1/independent_review.json).

Erhaltungsprüfung: **5885 von
5885 Ausgangsdateien bytegleich**, keine
unerwarteten Änderungen. Alle bisherigen Pläne, Ergebnisse, Fix-Evidenzen,
nativen QPYs, historischen M1–M4-Referenzen, Winkel und Kostenfunktionen sowie
die beiden alten ungetrackten Berichte bleiben unverändert. Die neue Baseline
sichert Originalrohhashes; sie ersetzt keine historische Integrität.
Neue Dateien ergänzen ausschließlich isolierte lokale Analyse, Tests, Auftrag
und Bericht. Bestehende Submission-/Provider-/Freigabe-/Ledgerpfade sind
bytegleich. Kein Provideraccount wurde in dieser Runde abgerufen; vor einem
späteren freigabefähigen Plan wären Account, Kontingent, offene/unklare Jobs
und Kalibration frisch zu prüfen.

Thematische lokale Commits vor dem Dokumentationsabschluss:

- `c712bfea5329250c213c136f35207ba2d5056851` — feat(m5): compare two advisory budgets without changing execution guards
- `811528a4a4ebab2607742acb1011a55c6df3606a` — feat(m5): diagnose archived critical path and fixed timing contributions
- `b33be826aa6a4930bb56dead42b46eef261a2e17` — feat(m5): quantify offline precision at 512 and 1024 shots
- `71782050298e2a8a77ca31168dbc209c4d9e0445` — feat(m5): add isolated reproducible local budget comparison command

Der finale Dokumentations-HEAD und der tatsächliche Arbeitsbaumstatus stehen
in `final_status.json`. Die zwei alten ungetrackten Berichte bleiben separat;
kein pauschales `git add .`, kein Reset, Merge, Force-Push oder Remote-Push.

Vergleichsentwurfshash: `832b001a7b927da4bcfaf28015b2132c767671cb4c475b6938ea0cc745a4e114`.
Separate A-Referenz: `fa0273729c75ccc76affb57866e1d9010df94d02fc2fed176666da4d337a8374`;
separate B-Budget-Amendment-Referenz: `e83d998074d8ff33b649f9ccac84409c2e0cc2682c8fb806a148a73a22088401`.
Dies sind **Beratungs-/Artefakthashes, keine ausführbaren Planhashes**.

Reproduktion mit dem bestehenden Projektinterpreter und einem neuen Output:

```powershell
.venv/Scripts/python.exe scripts/compare_m5b_budgets.py --root . --source results/m5b-fix-20261012-v2/provider --output results/m5b-budget-comparison-20261012-v2/analysis
```

## Begründete Empfehlung

**B als Grundlage einer separaten Budgetentscheidung bevorzugen.** Innerhalb
der offengelegten unveränderten Modellannahmen passt sein Budgetentwurf und
erhält die ursprüngliche Messpräzision. A spart nur rund 5,37% der
Risikoschätzung, verschlechtert die statistische Präzision und löst den
konkreten 40/120-Blocker nicht. Diese Empfehlung erteilt weder Hardware- noch
Budgetfreigabe. Derzeit bleibt die Submission gesperrt. Unbekannte interne
Teiljobs, zusätzlicher Steuerungsaufwand, alte Kalibration und statisches
Noise-Modell begrenzen beide Schätzungen; kein Erfolg unter dem Zeitlimit
wird garantiert.

Belege: [Vergleich](../results/m5b-budget-comparison-20261012-v1/analysis/comparison.json),
[A](../results/m5b-budget-comparison-20261012-v1/analysis/variant_a_shot_proposal.json),
[B-Amendment](../results/m5b-budget-comparison-20261012-v1/analysis/variant_b_budget_amendment.json),
[Timing](../results/m5b-budget-comparison-20261012-v1/analysis/timing_breakdown.json),
[Statistik](../results/m5b-budget-comparison-20261012-v1/analysis/precision_comparison.json),
[Inputprüfung](../results/m5b-budget-comparison-20261012-v1/analysis/input_verification.json),
[Erhaltung](../results/m5b-budget-comparison-20261012-v1/preservation_check.json),
[Abschlussaudit](../results/m5b-budget-comparison-20261012-v1/final_audit_after_review.json).

**Keine QPU-Jobs eingereicht. Keine Selbstfreigabe oder aktive Budgeterhöhung.**
