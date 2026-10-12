# M5B — Abschluss der Preflight-Blocker-Reparatur

Stand: 12. Oktober 2026. **BLOCKED / USAGE_LIMIT_EXCEEDED.** Die drei
nachgewiesenen Implementierungsprobleme wurden repariert und lokal geprüft.
Der konkrete Hardwareentwurf ist noch nicht freigabefähig: Das ausgewiesene
konservative lokale Nutzungsszenario überschreitet die unveränderten 40 Sekunden
je Hauptjob und 120 Sekunden für die Kampagne. **Null QPU-Jobs eingereicht.**
`hardware_enabled=false`, `submission_approved=false`, `hardware_used=false`.
Keine Hardwaremessung und kein M5-Hardware-PASS.

## Nachgewiesene Ursachen und Reparaturen

1. **Providervertrag:** Das bisherige Gate verlangte obsolete
   `max_shots`/`max_experiments` und ein nicht veröffentlichtes Kampagnenlimit
   `max_jobs`. Es klassifizierte diesen Adapterfehler als fehlende
   Eigentümerkonfiguration. Die [IBM-Jobgrenzen](https://quantum.cloud.ibm.com/docs/en/guides/job-limits)
   wurden erneut geprüft. Der korrigierte Vertrag unterscheidet aktuelle
   Sampler-Executions nach Broadcasting, native Gate-/Instruktionsgrenzen,
   lokale Payloadgrenze, Kontonutzung und Einreichungsrate. Unbekannte
   Providerlimits bleiben `not_exposed`; es wird weder drei noch unendlich
   eingesetzt. Die [REST-Einreichungsrate](https://quantum.cloud.ibm.com/docs/en/api/qiskit-runtime-rest/tags/jobs)
   von fünf Jobs pro Minute ist eine gesonderte Servicegrenze. Die lokalen
   drei seriellen Einreichungen und höchstens ein eigener ungeklärter Job
   gleichzeitig bleiben bestehen. INITIALIZING wird in der begrenzten
   Instanzinventur mitgeprüft. Details: [Providervertrag](m5b_provider_contract_fix.md).

2. **Nutzungsmodell:** Die vorherige manuell einzutragende Sekundenangabe wurde
   durch ein vollständig gebundenes lokales Modell aus den tatsächlichen
   Targetdauern und den tatsächlich expandierten PUBs ersetzt. Heterogene
   Shots, native kritische Pfade, parallele Initialisierung, Readout im
   Schaltungspfad und der unveränderte Backend-Rep-Delay werden ohne
   Doppelzählung berücksichtigt. Timing arbeitet nur auf einer Analysekopie;
   keine Delays, Winkel oder Gates werden in Hardware-/Noise-Schaltungen
   eingefügt. Ein zentraler Schätzwert und ein gesondertes Risikoszenario
   weisen Overhead, Unterteilung und Unsicherheiten aus. Dies ist ausdrücklich
   **kein Providerangebot und keine garantierte obere Schranke**. Die
   [IBM-Nutzungsdokumentation](https://quantum.cloud.ibm.com/docs/en/guides/estimate-job-run-time)
   nennt näherungsweise zwei Sekunden Overhead pro internem Subjob. Die echte
   interne Partitionierung ist vorab nicht belegt. Details:
   [Nutzungsmodell](m5b_usage_model.md).

3. **Kraus-Rekonstruktion:** Der tatsächliche bereinigte Traceback und der
   kleinste lesend gesicherte `ibm_fez`-Kanal (`id`, physisches Qubit 0)
   bestätigen die Hypothese `serializable=True → QuantumError.from_dict`.
   Komplexe 2×2-Matrizen werden im JSON-Pfad zu `(2,2,2)`-Daten; `Kraus(params)`
   scheitert in der installierten Aer-0.17.2-Quelle an `quantum_error.py:383`.
   Die eng begrenzte interne Rekonstruktion verwendet jetzt numerische
   Dictionaries. Zusätzlich verlor die kanonische Choi→Kraus-Konversion kleine
   Mischungsterme; der vorhandene 5e-11-Term wird jetzt aus den ursprünglichen
   gewichteten numerischen Operationen erhalten. Es gibt keine reale
   Projektion, pauschales Reshape oder Toleranzlockerung. Der einzelne
   Kraus-Kanal verhindert weiterhin Aers Abschneiden winziger Mischungsterme.
   Superoperator, Choi, direkte Dichtematrixanwendung, gerichtete Qargs und
   Readout genau einmal bestehen. Details: [Kraus-Fix](m5b_kraus_fix.md).

Die Abnahme hatte zusätzlich einen Diagnoseverlust: Ein gültig versiegelter,
wegen Nutzung blockierter Entwurf wurde verworfen und als fehlende
Owner-Konfiguration dargestellt. Die schmale Folgekorrektur erhält seine
typisierte Diagnose und bereits bestandene Teilprüfungen. Blockierte Entwürfe
werden dadurch weder zur gültigen Hardwarefreigabe noch zu Ledger-/Jobdaten.
Historische Accountdaten werden nicht als frische Freigabe ausgegeben.

## Tatsächlich ausgeführte Tests und Tooling

Die ursprünglichen roten Belege bleiben in `results/m5b-fix-20261012-v1`:

- Provider: drei gezielte Regressionen scheiterten vor der Reparatur; danach
  bestanden 50 Provider-/Vertragstests.
- Nutzung: Import der neuen Tests scheiterte zunächst am noch fehlenden Modul;
  danach bestanden 38 Schätzermodelltests.
- Kraus: erster originaler Source-Lauf 12 FAIL/3 PASS; der vollständige finale
  Testsatz gegen den unveränderten ursprünglichen Git-Blob 13 FAIL/3 PASS.
  Danach 16 neue und 15 bestehende Device-Noise-Tests: 31 PASS. Neun numerische
  Kanalfamilien: maximale Superoperator-/Choiabweichung 1.7763568394002505e-15,
  maximale TP-Spurabweichung 1.887379141862766e-15 bei unverändertem 1e-12-Guard.
- Integration: Alte positive Vertragsfixtures wurden auf vollständige,
  synthetische versiegelte Schätzer- und Limitnachweise umgestellt. Keine
  Tests deaktiviert; nackte manuelle 20-Sekunden-Felder erteilen keine Freigabe.
- Abnahmediagnose: Der zusätzliche rote und grüne Regressionslauf liegt im
  neuen v2-Verzeichnis. Der vorherige tatsächliche Abnahmelauf bleibt erhalten.

Der vollständige finale Lauf auf dem stabilen Sourcekontext:
**1442 passed, 249 warnings in 573.53s (0:09:33)**. Ruff `check`, Ruff `format --check`, mypy und `uv build
--offline` bestanden tatsächlich. Vollständige Logs, JUnit, Exitcodes und
Start-/Endzeiten: [Tooling](../results/m5b-fix-20261012-v2/tooling.json).
Sourcekontext vor und nach allen Checks identisch:
`12506cd22bf0effab7e7445ca601aa1f1e696483951a4df6466dc18b78a750d4`.

Python-Projektinterpreter: vorhandenes Windows-`.venv/Scripts/python.exe`;
Qiskit `2.5.2`, Aer `0.17.2`,
Runtime `0.47.0`. Keine Dependency-Upgrades.
Der erste lokale sdist enthielt trotz Git-ignore das private Bindungsverzeichnis;
die Datei wurde nie hochgeladen. Eng begrenzte sdist-Ausschlüsse verhindern
dies im neu gebauten und entpackt geprüften Paket. Der erste fehlgeschlagene
Paketcheck bleibt separat dokumentiert; öffentliche Artefakte enthalten keine
Tokens oder vollständigen CRNs.

## Neuer tatsächlich ausgeführter lesender Provider-Preflight

Der bestehende Named Account `km3-m5-open` wurde ohne erneute Einrichtung
verwendet, ausschließlich mit seiner vorhandenen gespeicherten Instanz und
dem bereits festgelegten Backend **ibm_fez**. Keine andere Instanz,
kein Paid-Fallback, kein Upgrade. Neue Ergebnisse:
`results/m5b-fix-20261012-v2/provider`.

```powershell
.venv/Scripts/python.exe -m km_quantum m5 provider-preflight --root . --output results/m5b-fix-20261012-v2/provider --provider-config .m5-local/provider.json
.venv/Scripts/python.exe -m km_quantum m5 verify-local --root . --output results/m5b-fix-20261012-v2/provider
.venv/Scripts/python.exe -m km_quantum m5 plan --root . --output results/m5b-fix-20261012-v2/provider
.venv/Scripts/python.exe -m km_quantum m5 acceptance --root . --output results/m5b-fix-20261012-v2/provider --tooling results/m5b-fix-20261012-v2/tooling.json
```

| Prüfung | Belegtes Ergebnis |
| --- | --- |
| Accountbeobachtung nach lokaler Berechnung | `2026-10-12T13:42:46.896662+00:00` |
| Plan / Preis / Kosten | `open` / `free` / 0 Euro |
| Nutzung dieser Instanz | 0 s verbraucht; 600 s Limit; 600 s verbleibend |
| Separates Exhaustion-Flag | `usage_limit_reached=false` |
| Begrenzte offene Jobinventur | vollständig; 0 offene Jobs; 0 s beobachtete Reservierungen |
| Backend | real, operational, `active`, 156 Qubits, Region `us-east` |
| Physischer Bereich | `[0, 1, 2, 3, 4, 16, 5]` |
| Kalibrationszeitpunkt | `2026-10-12T15:11:31+02:00` |
| Snapshot-Abruf | `2026-10-12T13:37:01.248880+00:00` |
| Rep-Delay | 0.00025 s; unveränderter Backenddefault |
| Konkrete Vorbereitung | 28 Schaltungen, 84 QPY-Dateien, 28 echte gerätebezogene lokale Noise-Vorhersagen READY |
| Lokale Gleichheit / Mapping | PASS; maximal 7 aktive Qubits; maximale Wahrscheinlichkeitsabweichung 2.7755575615628914e-15 |
| Sampler-/native-/lokale Payloadgrenzen | `PASS`; tatsächliche PUB-Expansion und QPY-Bindung geprüft |
| Abschließender Preflight | `BLOCKED` / `USAGE_LIMIT_EXCEEDED`; Exit 1 |
| verify-local | Exit 0 / PASS |
| plan | Exit 1 / BLOCKED; kein Freigabenachweis |
| acceptance | Exit 1 / `BLOCKED`; Implementierung PASS; keine Hardwaredaten |

Die Kontobeobachtung war bei Abschluss des Preflights frisch. Die anschließende
Abnahme liest gespeicherte Evidenz und erneuert keinen Providerzeitstempel.
Bei jeder späteren Freigabe/Einreichungsprüfung sind Account, Bindung, Nutzung
und Ledger erneut zu prüfen. Das fünfminütige Fenster wurde nicht verlängert;
Kalibrations- und Accountzeiten bleiben ausdrücklich getrennt.

## Konkreter Entwurf und verbleibender Blocker

| Job | PUBs | expandierte Circuits | Executions/Shots | zentral s | lokales Risikoszenario s | SDK-Payload Bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `smoke` | 4 | 4 | 512 | 2.129721344 | 10.162151680 | 8,349 |
| `main-block-1` | 24 | 24 | 24,576 | 8.266077184 | 67.832596480 | 59,740 |
| `main-block-2` | 24 | 24 | 24,576 | 8.266077184 | 67.832596480 | 59,724 |
| Gesamt | 52 | 52 | 49,664 | 18.661875712 | 145.827344640 | — |

Die vier Smoke-PUBs stehen ausschließlich im unbestätigten Entwurf. Sie wurden
ebenso wenig eingereicht wie die beiden Hauptblöcke. Die harten Grenzen bleiben
3 Jobs, 49.664 Shots, 40 s/Job, 120 s gesamt, null Euro. Maximalreservierung und
lokale Belegung sind getrennt: aktuell wurden **0 s und 0 Shots reserviert**.
Die [maximale Ausführungszeit](https://quantum.cloud.ibm.com/docs/en/guides/max-execution-time)
bleibt als eigener Runtime-Schutz aktiv.

Das zentrale Modell nimmt einen Subjob je Job an; das lokale Default-
Risikoszenario einen Subjob je expandiertem Circuit und Faktor 1,25. Schon
24 × 2 s × 1,25 ergeben 60 s Overhead pro Hauptjob. Die tatsächliche interne
IBM-Unterteilung ist unbekannt. Diese lokale Annahme wird nicht zur gemessenen
IBM-Laufzeit umetikettiert und nicht abgesenkt, um READY zu erzwingen.
Der präzise verbleibende Blocker ist deshalb **USAGE_LIMIT_EXCEEDED unter der
offengelegten Default-Richtlinie**, keine fehlende Credential-Konfiguration.
Eine Änderung dieser Risikobasis benötigt eine gesondert nachvollziehbare
fachliche Grundlage/Prüfung innerhalb der bestehenden Grenzen. Kein QPU-Job
darf als Probe zur Ermittlung der Partitionierung gestartet werden.

Entwurfshash: `a2288adfb1bd56336ef7df8ff33de4ed9085689c71c9537d655fd4cb473c17ac`.
Schätzermodell: `2e2e6eb9835922a2bd58317c33525bc9a389903dc1fa91b0764c70de1f4a0ed9`.
Provider-Limitassessment: `745f5f7053c94e4f67fbca13c271020df34e661915b22c7745ca1c4345bce8fc`.
Targetsnapshot: `9ccbc1391bd9e218700420d012ae7468e21d5238a6342cba6953878530f91711`.
Instanz ausschließlich als Pseudonym: `c4a7bb9239a2bd371a037ae1c9291de4aa5ca1932a7b84ad1710aaed3483bbfe`.
Die `approval_request.json` ist **unsigned / approved=false**. Der lokale
State kann OFFLINE_READY ausweisen; Provider-/Plan-/Abnahmestatus bleibt
BLOCKED. **READY_FOR_APPROVAL wird nicht behauptet.**

## Erhaltung, Prüfung und Git

Ausgangs-HEAD: `f31d4ee38372338d43f17cc2e301a2051b0f2a55`.
Branch: `fix/m5b-preflight-contracts`.
Veröffentlichte M4-Referenz: `4ca11bf8f709aef9f43df4d3f2a0867189b54a7c`.
Die Baseline sicherte 4.533 Dateien vor der Reparatur; der Abschlusscheck
vergleicht deren ursprüngliche Rohhashes: 4.521 Dateien unverändert, zwölf
explizite Fixpfade geändert, null unerwartete Änderungen. Historische M1–M4-Referenzen,
M4-Winkel, M5-Protokoll/Selection und bisherige M5A-/M5B-Ergebnisse bleiben
unverändert. Auch die beiden schon vorher untracked vorhandenen alten Berichte
werden unverändert erhalten. Keine historische Datei wurde neu versiegelt,
um eine Veränderung zu verdecken. v1 einschließlich ursprünglicher Abnahme
bleibt erhalten; v2 ist der finale neue Sourcekontext.

Die unabhängige Prüfung von v1 hatte zunächst einen Audit-Fehlalarm wegen
unterstellter Byteidentität bei SDK-Neuserialisierung. Der erste FAIL-Bericht
bleibt erhalten. Der korrigierte Review bestätigt die originalen Raw-QPY-
Hashes und den archivierten Assessmentseal **exakt**, die semantischen
Limitdaten ebenfalls. Wiederholtes RuntimeEncoder-Serialisieren schwankte um
wenige Bytes weit unter dem lokalen 1-MiB-Guard. Dies ist kein Nachweis einer
universellen Varianzgrenze oder einer unbekannten IBM-Payloadgrenze.
Keine Produktionsprüfung wurde dafür gelockert.

Auch der finale unabhängige v2-Review besteht: 84 originale QPY-Rohhashes,
gespeicherte Seals, Sourcekontext, Planbindungen und semantische Limits stimmen
exakt. Die unabhängige DAG-/Reset-/Readout-/Rep-Delay-Rechnung reproduziert die
145,82734464 Sekunden des lokalen Risikoszenarios. Beobachtete separate
SDK-Neuserialisierung: −8 bis +8 Bytes, mindestens 987.812 Bytes Abstand zum
lokalen Guard. Keine Providerzugriffe oder Freigabe durch den Review.
[Finaler unabhängiger Review](../results/m5b-fix-20261012-v2/independent_review.json).

Thematische lokale Fix-Commits vor dem abschließenden Dokumentationscommit:

- `1a53d36d739159c9c8b8a91487fa500729d6ce9b` — fix(m5): preserve numerical Kraus channels and tiny mixture terms
- `b9adcea1a77ce4ae3768084542df83b4a6646819` — feat(m5): model native circuit usage with explicit uncertainty and fixed caps
- `a679d61f6476f8c39d1c5a80fb62fa7d46e1e49f` — fix(m5): validate actual Sampler contracts and bind local execution models
- `82da5814cdc8fc341e9178cf9491824fe09d2410` — fix(m5): retain blocked preflight diagnostics in read-only acceptance

Vor dem Dokumentationscommit war der getrackte Arbeitsbaum sauber; lediglich
`docs/m5b_named_account_preflight_report.md` und
`docs/m5b_provider_preflight_report.md` blieben wie zuvor untracked. Der
abschließende Dokumentations-HEAD und der tatsächliche finale Gitstatus stehen
im separaten versionierten `final_status.json`, um eine zyklische Selbst-SHA
im Commit zu vermeiden. **Kein Merge, Force-Push oder Remote-Push.**

Belege: [Tooling](../results/m5b-fix-20261012-v2/tooling.json),
[Provider](../results/m5b-fix-20261012-v2/provider/provider_preflight.json),
[Schätzer](../results/m5b-fix-20261012-v2/provider/local_usage_model.json),
[Plan](../results/m5b-fix-20261012-v2/provider/execution_plan.json),
[Abnahme](../results/m5b-fix-20261012-v2/provider/m5-acceptance.json),
[Erhaltungsprüfung](../results/m5b-fix-20261012-v2/preservation_check.json),
[Privacy-/Zustandsprüfung nach Abschlussbericht](../results/m5b-fix-20261012-v2/final_privacy_and_state_audit_after_review.json).

Das gerätebezogene Noise-Modell bleibt eine kalibrationsbasierte Approximation.
Idle-Zeitpläne, Crosstalk und Drift sind nicht vollständig modelliert; lokale
Histogramme sind weder Hardwaremessung noch Zustandstomographie. Die bekannte
Vier-Fälle-Suite und zwei Wiederholblöcke belegen keine Warm-Start-Überlegenheit.
**Null QPU-Jobs eingereicht; keine Freigabe erteilt.**
