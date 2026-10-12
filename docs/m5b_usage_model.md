# M5B: lokales Nutzungsmodell für die unveränderten Hardwaregrenzen

Der neue Schätzer in `src/km_quantum/m5/usage.py` arbeitet ausschließlich lokal
mit den gebundenen nativen Schaltungen und ihrem tatsächlichen `Target`.
Er authentifiziert sich nicht und ruft weder einen Backendlauf noch eine
Runtime-Primitive auf. Das Ergebnis ist eine modellierte Näherung mit
ausgewiesener Unsicherheit. Es ist kein Providerangebot, keine Hardwaremessung
und keine garantierte obere Schranke.

## Methode und Quellen

Die offizielle [IBM-Dokumentation zur Nutzung](https://quantum.cloud.ibm.com/docs/en/guides/estimate-job-run-time)
wurde am 12. Oktober 2026 erneut gelesen. Sie beschreibt einen näherungsweisen
Subjob-Overhead von zwei Sekunden und eine Berechnung aus Schaltungsdauer,
Wiederholabstand und nach Broadcasting tatsächlich ausgeführten Shots. Sie
bezieht Initialisierung in ihr lokales Timingbeispiel ein. Die Anzahl interner
Subjobs ist vor der Einreichung nicht als konkrete Providerzusage verfügbar.

Die gepinnte Qiskit-Version bleibt `2.5.2`. Ihre öffentliche Methode
`QuantumCircuit.estimate_duration(target, unit="s")` bestimmt die längste
Instruktionskette aus den kalibrierten Targetdauern in SI-Sekunden. Die Analyse
arbeitet auf einer Kopie. Es werden keine neuen Delays, Winkel oder Gatter in
die archivierte Hardware- oder Noise-Schaltung geschrieben.

Für jeden tatsächlich expandierten PUB-Circuit wird getrennt gerechnet:

`shots × (native critical-path seconds + initialization seconds + rep_delay seconds)`.

Anschließend werden die Beiträge summiert. Damit behalten heterogene PUBs ihre
jeweiligen Shotzahlen und Dauern. Die Expansion verwendet den öffentlichen
`SamplerPub`-Container der gepinnten Version. Ein PUB mit drei Parameterbindungen
und sieben Shots hat 21 Executions. Die M5-Schaltungen sind vollständig gebunden;
der vorhandene Providervertrag prüft ihre wissenschaftliche Inventarbindung
zusätzlich zum allgemeinen Schätzer.

Messinstruktionen liegen bereits im nativen Circuit und damit in der längsten
Instruktionskette. Ihre Dauer wird kein zweites Mal addiert. Als Initialisierung
wird pro Shot eine parallele Resetphase über die tatsächlich aktiven physischen
Qubits modelliert; deren Dauer ist die längste ihrer Target-Resetdauern. Ein im
Circuit explizit vorhandener Reset gehört außerdem zu dessen eigener Arbeit.
Die Parallelisierung ist eine ausgewiesene Modellannahme. Fehlende Reset-,
Readout- oder Gatterdauern werden nicht zu null gemacht.

Der Wiederholabstand kommt mit Wert, Bereich und Quelle aus dem expliziten
Backenddefault und wird pro Execution genau einmal addiert. Der Schätzer
verringert ihn nicht. Unbekannte oder widersprüchliche Angaben blockieren.

## Overhead, lokale Richtlinie und Grenzen

Der zentrale Wert modelliert einen Subjob je Runtimejob. Zusätzlich wird ein
lokales Risikoszenario mit einem Subjob je expandiertem Circuit berechnet.
Auf diesen zweiten Wert wirkt ein expliziter lokaler Faktor `1.25`. Die
Partitionierung und der Faktor sind lokale Richtlinien, keine IBM-Fakten.
Zusätzliche Aufteilung nach Shots oder Payload und abweichender Kontrollaufwand
bleiben möglich. Die Näherung kann deshalb keine tatsächlich ausreichende
obere Schranke versprechen.

Für ausdrücklich geprüfte lokale Szenarien unterstützt die reine Python-API
`UsagePolicy(subjob_policy="reviewed_partition_scenario", max_subjobs=...,
reviewed_source=...)`. Eine positive ganze Subjobzahl und ein nachvollziehbarer
Reviewvermerk sind zwingend und werden vollständig an die Modellhashes gebunden.
Auch dieses Szenario bleibt eine lokale Annahme ohne Providergarantie. Synthetische
positive Vertragsfixtures können so eine bekannte Testpartition abbilden. Die
echte CLI verwendet weiterhin ausschließlich den konservativen Default;
`ibm_fez` erhält in dieser Reparaturrunde kein abgesenktes Szenario.

Beide Werte und die zugehörige Annahme stehen im Artefakt. Die zweite Zahl ist
die bei der Bereitschaft verwendete `local_policy_seconds`. Bereits ihr
Overheadanteil beträgt bei 24 expandierten Circuits `24 × 2 × 1.25 = 60` Sekunden.
Ein solcher Hauptjob bleibt mit der unveränderten 40-Sekunden-Grenze blockiert.
Dieser Befund behauptet keine gemessene Serverlaufzeit. Er benennt die
Auswirkung der konservativen lokalen Richtlinie und der fehlenden konkreten
Serverpartitionierung. Die Richtlinie wird nicht nachträglich abgesenkt, um
einen grünen Status zu erzielen.

Die freigegebenen Höchstgrenzen bleiben drei Einreichungen, 49.664 tatsächliche
Executions, 40 Sekunden je Job und 120 Sekunden insgesamt. Die Runtimezeitgrenze
ist eine gesonderte Schutzgrenze; ein Job, dessen lokale Schätzung sie
überschreitet, wird nicht probeweise eingereicht. Die
[IBM-Dokumentation zur maximalen Ausführungszeit](https://quantum.cloud.ibm.com/docs/en/guides/max-execution-time)
beschreibt diese Grenze als QPU-Nutzungszeit. Frische Open/free- und Nutzungsdaten
sowie Ledgerreservierungen werden weiterhin separat geprüft; lokale Berechnung
erneuert keinen Providerzeitstempel.

## Bindung und Diagnosen

Die Artefakte enthalten QPY-Rohhashes, zusätzliche Strukturhashes der
Analysekopien, Targetschnappschusshash, Hash der tatsächlich verwendeten
Targetdauern samt `dt`, Konfigurationshash, Studie, ursprüngliche Job-/PUB-Eingaben,
alle Beiträge, Einheiten, fehlende Werte und Unsicherheiten. `model_sha256`
versiegelt jeweils die übrigen Felder; der eigene Seal wird ausgeschlossen.
Geschätzte Ausgabefelder im Plan sind keine Schätzereingaben und verursachen
keine zyklische Hashbindung.

`validate_usage_model` prüft das vollständige Modell gegen den eingefrorenen
Plan: Jobs, Reihenfolge, PUBs, Shots, QPY- und Targetbindung, Rep-Delay,
Initialisierung, Grenzen, Berechnung und Seals. Beim Laden der QPY-Dateien muss
der Aufrufer zusätzlich deren tatsächliche Rohbytes verifizieren. Die kurze
`execution_estimates`-Map reicht allein nicht als Nachweis. Eine vollständige
BLOCKED-Diagnose lässt sich separat auf Integrität prüfen, ohne sie dadurch
freigabefähig zu machen.

Unzureichende Eingangsdaten heißen `INSUFFICIENT_USAGE_MODEL`. Eine modellierte
Überschreitung heißt `USAGE_LIMIT_EXCEEDED`. Beides startet keine erneute
Credentialeinrichtung.

## Tatsächlich ausgeführte Regressionen

Vor der Implementierung scheiterte die neue Testsammlung tatsächlich mit
`ModuleNotFoundError`, weil das Modellmodul fehlte. Der rote Lauf liegt im neuen
Verzeichnis `results/m5b-fix-20261012-v1/usage_regression_before.txt`. Anschließend
bestanden 38 gezielte Tests. Sie prüfen heterogene Shots, Broadcasting, Einheiten,
Reset und Readout, das unveränderte Default-Rep-Delay, fehlende Dauern,
Unterteilungsannahmen, Werte oberhalb von 40/120 Sekunden, unveränderte Circuits,
Hashbindungen, manipulierte beziehungsweise neu versiegelte widersprüchliche
Modelle und die Trennung zwischen archivierbarer Diagnose und Bereitschaft.
Gezieltes Ruff und mypy bestanden ebenfalls. Die vollständige Suite und die
erneute echte lesende Providerprüfung werden im gemeinsamen Fixbericht mit
ihren tatsächlichen Ergebnissen dokumentiert.

Historische M1–M4-Dateien, M4-Winkel und bisherige M5-Artefakte wurden von diesem
Schätzermodul nicht verändert. `hardware_enabled=false` und
`submission_approved=false` bleiben bestehen. Null QPU-Jobs wurden eingereicht.
