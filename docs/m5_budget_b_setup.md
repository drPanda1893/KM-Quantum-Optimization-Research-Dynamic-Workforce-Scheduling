# M5 — genehmigte Budgetversion B, separate Hardwarefreigabe

Die ausdrückliche Nutzerentscheidung ist in
[m5_budget_b_authorization.md](m5_budget_b_authorization.md) archiviert.
Der versiegelte öffentliche Nachweis liegt in
[m5_budget_b_authorization.json](../configs/m5_budget_b_authorization.json).
Sein Ausgang ist `5c186536d9b383d66d7a209f2835ccf3748e9b3e` auf
`codex/m5b-budget-plan-comparison`. Er genehmigt ausschließlich das Budget und
die Vorbereitung eines neuen Plans. Er ist keine Hardwarefreigabe.

| Grenze | Standard `m5-budget-v1` | genehmigtes `m5-budget-b-v2` |
| --- | ---: | ---: |
| Smoke | 40 s | 20 s |
| Hauptblock 1 | 40 s | 80 s |
| Hauptblock 2 | 40 s | 80 s |
| Kampagne | 120 s | 180 s |
| Jobs / PUBs / Shots | 3 / 52 / 49.664 | 3 / 52 / 49.664 |
| Geldbudget | 0 EUR, verifiziert Open/free | 0 EUR, verifiziert Open/free |

Die Standardkonfiguration bleibt unverändert. B benötigt explizit seine
Version, den vollständigen validierten Nachweis und die passenden Grenzen.
[m5_budget_b.json](../configs/m5_budget_b.json) enthält diese Bindung.
Ein beliebig erhöhtes Limit, ein anderer Umfang oder ein ungültiger Nachweis
wird abgewiesen. Beide Hardwareflags bleiben `false`.

Die Schätzmethodik bleibt unverändert: konkrete native Target-Dauern,
Readout genau einmal, normale Initialisierung, dokumentierter Backend-Rep-Delay,
eine angenommene interne Unterteilung je expandierter Schaltung sowie Faktor
1,25. Die ungefähr zwei Sekunden je internem Subjob stammen aus der
[IBM-Nutzungsschätzung](https://quantum.cloud.ibm.com/docs/en/guides/estimate-job-run-time).
Die konkrete Unterteilung und der Sicherheitsfaktor sind lokale Annahmen,
keine vom Provider zugesagten Obergrenzen. Queue- oder Client-Wandzeit ist
keine QPU-Zeit. Das angeforderte Runtime-Limit begrenzt die QPU-Nutzung gemäß
[IBM-Laufzeitvertrag](https://quantum.cloud.ibm.com/docs/en/guides/max-execution-time).

Der vorhandene Windows-Projektinterpreter und der benannte Account
`km3-m5-open` werden weiterverwendet. Die ausdrücklich gebundene Instanz und
das Backend stehen ausschließlich in der ignorierten lokalen Datei
`.m5-local/provider.json`; diese Anleitung enthält keine Zugangsdaten.

Der tatsächlich verwendete lesende Ablauf ist:

```powershell
.venv/Scripts/python.exe -m km_quantum m5 provider-preflight --root . --output results/m5-budget-b-20261012-v2/provider --provider-config .m5-local/provider.json --budget-approval configs/m5_budget_b_authorization.json
.venv/Scripts/python.exe -m km_quantum m5 verify-local --root . --output results/m5-budget-b-20261012-v2/provider
.venv/Scripts/python.exe -m km_quantum m5 plan --root . --output results/m5-budget-b-20261012-v2/provider --budget-approval configs/m5_budget_b_authorization.json
.venv/Scripts/python.exe -m km_quantum m5 acceptance --root . --output results/m5-budget-b-20261012-v2/provider --tooling results/m5-budget-b-20261012-v2/tooling.json
```

Vorhandene Ergebnisse werden nicht überschrieben. B verwendet ein eigenes
Auswahlmanifest im neuen Ausgabeverzeichnis. Konkrete Schaltungen, Kalibration,
Mapping, QPY-Rohhashes, Nutzungsmodell, Optionsprüfung und Budgetnachweis werden
an den neuen Plan gebunden. Frühere Freigaben oder Ledger gelten für diesen
neuen Planhash nicht.

Die tatsächlichen Optionen je Job tragen `max_execution_time=20/80/80`.
Das Ledger reserviert diese Beträge vollständig vor einem später separat
freigegebenen Request. Eine unklare Einreichung, Fehler oder Abbruch setzt
die Reservierung nicht zurück. Es gibt keine automatische Wiederholung.
Tatsächliche QPU-Nutzung wird gegen den jeweiligen Job und die gesamte
180-Sekunden-Grenze geprüft.

Vor jeder späteren Hardwareeinreichung sind frische Instanz-, Quoten- und
Jobnachweise nötig. Für die aktuelle Vorbereitung wird nach längerer lokaler
Kompilierung erneut gelesen. Nicht finale oder im Ausgang unklare Providerjobs,
fehlende Angaben, ein unzureichendes Kontingent oder überschrittene
Schätzgrenzen blockieren weiterhin.

`approval_request.json` bleibt ein unbestätigter Vorschlag (`approved=false`)
für den neuen konkreten Plan. Erst eine spätere ausdrückliche menschliche
Freigabe dieses Plans kann den vorhandenen Hardwarepfad zulassen. In dieser
Runde werden ausschließlich lokale Prüfungen und lesende Providerabfragen
ausgeführt. Ohne Hardwaremessungen ist M5 nicht PASS.
