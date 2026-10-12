# M5 — geprüfter Fix und neuer Hardwareplan

Status: **READY_FOR_APPROVAL**. Die lokale Implementierung ist geprüft,
Hardwareausführung ist **NOT_STARTED**. Es wurden **keine QPU-Jobs** eingereicht.
Beide Planflags bleiben false; der neue Freigabeentwurf ist unbestätigt.
Der Gesamtstatus ist kein M5 PASS.

## Git-Stand und konkrete Bindung

- Branch: `codex/m5-execution-gates-fix`.
- Geprüfter Code-Commit: `880705ce98358787e5f1a7892ab81464873879d6`.
- Quell-/Dependencyhash: `16016c77ecbceea2f9230432acd20ca12dfae77a43779df8ceb549b2f3f8ef38`.
- Neuer Planhash: `2eb423c2403cd73aa4104c83eb5fce0d29c950fdb0377501dcca1a9df4c4e6a5`.
- Originalcheckout: unverändert auf `bad2b09714209df3ccccbbab6d7fd408becc2e36`.
- Backend: `ibm_fez`, 156 Qubits;
  eingefrorener physischer Bereich `[0, 1, 2, 3, 4, 16, 5]`.
- Kalibrationszeit: `2026-10-12T20:53:19+02:00`;
  Target-Snapshot `8fe0e2bc64339858a9e52dc6f8881d50db91a1c9bdb9cc8c6b26c7bab9adcac7`, Schema 2.
- Öffentliche Readiness-Seal: `77f424732ad70815cabaa532f23153980cf45fdcaea9162a6b75d150472dbfe2`.

Der Code liegt im separaten lokalen Worktree
`C:\Users\flori\.codex\worktrees\m5-execution-gates-fix\Quantum`.
Der ursprüngliche Checkout unter `Documents\Quantum`, alle historischen
Ergebnisse und die beiden alten ungetrackten Preflight-Berichte bleiben erhalten.
Es erfolgte kein Push oder Merge. Die Freigabe des alten Plans `e863b07…`
wurde nicht auf den neuen Hash übertragen.

## Frisch belegter Providerstatus

Die letzte lesende Bestätigung stammt von **2026-10-12T20:01:01.561548+00:00**:
`plan=open`, `pricing_type=free`, Geldbudget 0 EUR, operationaler echter
Backend und unveränderte ausdrückliche Instanzbindung. Das vorhandene
Windows-Benutzerprofil und der Named Account `km3-m5-open` wurden verwendet.
Credentials wurden nicht kopiert oder neu eingerichtet.

Die SDK-Angaben belegen 600 Sekunden Restkontingent,
0 Sekunden Verbrauch und
600 Sekunden Gesamtlimit.
Die Restzeit ist nachvollziehbar aus SDK-Limit minus Verbrauch abgeleitet;
das getrennte Feld `time_available` ist nicht verfügbar. Das zusätzliche
Admissionflag `usage_limit_reached=false` ist belegt.
Die vollständige Abfrage der gebundenen Instanz fand **0 offene oder im Ausgang
unklare Jobs**, keine bestehenden Reservierungen und keine neue lokale Jobdatei.

Die letzte frische Geräteprüfung vergleicht Kalibration, Mapping, native Daten,
Target-Timing und Rep-Delay mit der eingefrorenen Basis. Ihre Seal lautet
`7c8d3ac452405479003c5239ef59ed5f9614ef4f97efd38dc34c5d996208389e`.
Sie hat den Plan, den eingefrorenen Snapshot und die Vorhersagen nicht ersetzt.
Ein späterer Submit benötigt erneut frische Nachweise; diese Beobachtung ist
keine dauerhafte Kontingent- oder Gerätegarantie.

## Unveränderter wissenschaftlicher Umfang und Budget

| Job | PUBs | Shots | Konservative lokale Schätzung, s | Obergrenze, s |
| --- | ---: | ---: | ---: | ---: |
| smoke | 4 | 512 | 10.162151680 | 20 |
| main-block-1 | 24 | 24,576 | 67.832596480 | 80 |
| main-block-2 | 24 | 24,576 | 67.832596480 | 80 |

Gesamt: drei Jobs, 52 PUBs, 49.664 Shots; konservativ
145.827344640 Sekunden gegenüber 180 Sekunden Grenze.
Smoke bleibt 4 × 128, jeder Hauptblock 24 × 1.024 Shots.
Die separate Budgetautorisierung für Variante B bleibt unverändert gebunden.

Die Methodik bleibt unverändert: native Target-Kritikalität, einmalige
Readout-/Initialisierungszeit, expliziter Rep-Delay, ca. 2 Sekunden modellierter
Aufwand je interner Unterteilung, lokale konservative Annahme von 4/24/24
Unterteilungen und Faktor 1,25. Interne Unterteilung und QPU-Zeit sind nicht
garantiert; die Schätzungen sind keine Providerangebote oder Messwerte.
Es wurden weder günstigere Annahmen eingesetzt noch Budgets weiter erhöht.

## Tatsächlich ausgeführte Prüfungen

- 197 gezielte Tests bestanden.
- Vollständige Suite: 1741 Tests bestanden,
  305 bekannte SDK-Deprecation-Warnungen.
- Ruff, Formatprüfung von 137 Dateien, mypy mit 80 Source-Dateien und
  Offline-Build mit dem vorhandenen Windows-Projektinterpreter bestanden.
- Wheel und Quellarchiv enthalten keine `.m5-local`- oder Ergebnisverzeichnisse;
  es wurde nichts hochgeladen.
- `provider-preflight`, `verify-local`, `plan` und `acceptance` über den
  tatsächlichen CLI-Parser ausgeführt; Sampler, Estimator, Submit und
  Accountmutationen waren im lesenden Prozess ausdrücklich gesperrt.
- 28 eingefrorene Schaltungen, 84 QPY-Dateien und 28 gerätebezogene
  Noise-Vorhersagen geprüft. Maximale ideale Wahrscheinlichkeitsabweichung
  `2.7755575615628914e-15`;
  maximale Energieabweichung `1.1795009413617663e-12`;
  maximale Abweichung der vier Metriken `2.7755575615628914e-15`.
  QPY-Roundtrip und Messmapping bestanden; maximal sieben aktive Qubits.
- Alle 4.176 geschützten M1–M4-Dateien bytegleich; M4-Winkel und historische
  Schaltungen erhalten. Keine neuen Referenzwinkel oder Relaxationen.
- Acceptance: Implementierung PASS, lokal OFFLINE_READY, Hardware NOT_STARTED,
  0 eingereichte Jobs, alle 52 Hardware-PUBs ausstehend, Freigabe NOT_APPROVED.

Frühere Diagnoseergebnisse bleiben erhalten: Der erste vollständige Lauf
bestand 1.740 Tests und scheiterte ausschließlich an byteabweichenden
Windows-Zeilenenden historischer Checkoutdateien. 50 Dateien wurden aus dem
unveränderten Originalcheckout bytegleich wiederhergestellt; der fachliche
Quellhash blieb identisch. Anschließend bestanden der gezielte historische
Test und die vollständige Suite. Die Prüfung wurde nicht abgeschwächt.
Der erste uv-Build zeigte auf einen nicht vorhandenen verwalteten Interpreter;
der dokumentierte Wiederholungslauf nutzte ausdrücklich den bestehenden
Projektinterpreter. Beide ersten Ergebnisse sind weiterhin archiviert.

## Review und nächste Freigabe

Die Reparaturen und Grenzen sind in [m5_execution_gates.md](m5_execution_gates.md)
beschrieben. Der vollständige lokale Plan liegt unter
`results/m5-execution-gates-fix-20261012-v2/provider/execution_plan.json`,
der unbestätigte Vorschlag daneben unter `approval_request.json`.
Sein Ablaufzeitpunkt ist `2026-10-13T19:47:56.805477+00:00`.
Die alte private Freigabedatei wurde nicht verändert oder kopiert.

Vor jeder echten Einreichung braucht **genau der neue Planhash** eine neue
ausdrückliche menschliche Hardwarefreigabe und einen gültigen lokalen
Freigabebeleg. Es gab keinen Smoke-Job, kein echtes `submit-next`, keine
kostenpflichtige Instanz und keine Selbstfreigabe.

Die gerätebezogene Noise-Vorhersage bleibt eine Näherung mit symmetrischer
Readoutapproximation und unmodelliertem Idle/Crosstalk/Drift. Smoke-Muster werden
beschreibend ausgewertet; es wurde keine statistische Qualitätsschwelle
erfunden. Ohne tatsächliche Hardwaremessungen gibt es weder empirische
Hardwareübereinstimmung noch einen allgemeinen Warm-Start-Vorteilsnachweis.

Die abschließende, erneut frische Bestätigung und die unabhängige Artefaktreview
sind zusätzlich in [execution_gates_final_confirmation_20261012.json](../data/synthetic/m5/execution_gates_final_confirmation_20261012.json)
gebunden. Die Review bestand 13 Bündelchecks, sämtliche Seals, 84 rohe QPY-Hashes
und 138 Quell-/Dependency-Dateihashes. Sie verwendete keine SDK-/Konto-/Netzwerkzugriffe;
Native-Kritikalität und Runtime-Encoder wurden dabei nicht nochmals ausgeführt.
Der tatsächliche CLI-Preflight und die lokalen Gleichheitstests liefern diese
separaten Ausführungsnachweise. Die Bestätigung ersetzt weder Plan noch Snapshot.
