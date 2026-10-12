Ich genehmige für M5 ausdrücklich die Budgetänderung auf Variante B.

Diese Genehmigung betrifft ausschließlich die lokalen QPU-Zeitgrenzen
und die Vorbereitung eines neuen konkreten Hardwareplans.

Ausgangspunkt:
codex/m5b-budget-plan-comparison
5c186536d9b383d66d7a209f2835ccf3748e9b3e

Genehmigte neue Obergrenzen:
- smoke: maximal 20 QPU-Sekunden
- main-block-1: maximal 80 QPU-Sekunden
- main-block-2: maximal 80 QPU-Sekunden
- gesamte Kampagne: maximal 180 QPU-Sekunden
- Geldbudget: 0 EUR, ausschließlich verifizierter Open/free-Zugang

Unveränderter Umfang:
- 3 Jobs
- 52 PUBs
- 49.664 Shots
- Smoke: 4 × 128 Shots
- jeder Hauptblock: 24 × 1.024 Shots
- unveränderte Fälle, M4-Winkel, Kontrollen und wissenschaftliche Optionen

Setze diese Budgetänderung nachvollziehbar versioniert um.
Keine pauschale Entfernung oder Umgehung der bisherigen Prüfungen.

Erhalte den historischen 40/120-Sekunden-Plan und dessen
BLOCKED-Ergebnis. Bestehende Freigaben oder Planhashes dürfen nicht
auf den neuen Plan übertragen werden.

Prüfe alle betroffenen Stellen:
Konfiguration, Optionsvalidierung, Planbindung, lokales
Reservierungsledger, Budgetabrechnung und Regressionstests.

Die Budgetgenehmigung ist getrennt von der Hardwarefreigabe abzulegen.
hardware_enabled=false und submission_approved=false bleiben bestehen.

Führe danach den vorhandenen lesenden Provider-Preflight erneut aus:
- ausdrücklich gebundene kostenlose Instanz;
- aktuelles Restkontingent;
- offene oder im Ausgang unklare Jobs;
- konkretes Backend und Kalibrationsdaten;
- vollständige lokale Hardware-/Noise-/Mapping-Prüfungen;
- unveränderte Schätzmethodik und konservative Annahmen.

Fehlen notwendige Nachweise oder reicht das Budget nicht:
blockieren, nicht selbst weitere Grenzen erhöhen.

Erzeuge einen neuen konkreten Plan mit neuem Hash.
Archiviere die Budgetänderung und ihre Herkunft nachvollziehbar.
Die zwei alten ungetrackten Berichte bleiben unberührt.

Führe betroffene Tests und die vollständigen Qualitätsprüfungen aus.
Committe die Änderungen thematisch; kein automatischer Push oder Merge.

Abschluss:
- READY_FOR_APPROVAL oder konkreter verbleibender Blocker;
- neuer Commit und Planhash;
- Backend, Jobs, PUBs, Shots und Zeitobergrenzen;
- frisch belegtes Restkontingent;
- tatsächlich ausgeführte Prüfungen.

KEIN submit-next, KEIN Smoke-Job, KEINE QPU-Einreichung.
Diese Budgetgenehmigung ersetzt nicht meine spätere Freigabe
des konkreten Hardwareplans.
