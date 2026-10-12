# M5 — sequenzielle Ausführung und frische Gerätebindung

Diese Reparatur baut im separaten Branch `codex/m5-execution-gates-fix` auf
`bad2b09714209df3ccccbbab6d7fd408becc2e36` auf. Der ursprüngliche Checkout,
die bisherigen Ergebnisse, die zwei alten ungetrackten Berichte und der
historische 40/120-Sekunden-Plan bleiben erhalten. Die Freigabe des alten
Hardwareplans `e863b07c25dc95dd9b5350624dc000c96a87c23d5902764c2cc08aeca242db38`
gilt nicht für einen neuen Planhash.

Die Budgetautorisierung für Variante B bleibt separat gebunden: Smoke 20,
Hauptblock 1 und 2 jeweils 80, Kampagne 180 QPU-Sekunden; 0 EUR, ausdrücklich
verifizierter Open/free-Zugang; drei Jobs, 52 PUBs, 49.664 Shots. Schätzmethodik,
Sicherheitsfaktor, M4-Winkel, Fälle, Kontrollen und wissenschaftliche Optionen
bleiben erhalten. Diese Dokumentation erteilt keine Hardwarefreigabe.

## Kampagne und abgeschlossener Einzeljob

`collect` erhält den Kampagnenstatus `PARTIAL`, solange geplante spätere Jobs
fehlen. Der lokale Ausführungszustand darf dennoch `COLLECTED` erreichen,
wenn alle bereits reservierten Jobs abgeschlossen sind, sämtliche zugehörigen
PUB-Daten vorliegen, die tatsächliche Nutzung die Grenzen einhält und die vier
Smoke-Kontrollen ihren technischen Datenvertrag bestehen.

`submit-next` erlaubt weiterhin ausschließlich `READY_FOR_APPROVAL`,
`APPROVED` oder `COLLECTED`. Es gibt keine pauschale Freigabe von `PARTIAL`.
Fehlende Counts, unaufgelöste Jobs, ungültige Freigabe, fehlende Nutzung und
veränderte Planbindungen blockieren vor einer weiteren Reservierung.

## Gemessene Nutzung und Wiederaufnahme

Der Ledger prüft vor dem Laden der nächsten PUBs und vor der atomaren
Reservierung die gemeldeten `provider_metrics.usage.quantum_seconds` aller
abgeschlossenen Vorgänger. Diese müssen endliche, nichtnegative Zahlen sein
und sowohl die jeweilige Jobgrenze als auch die Kampagnengrenze einhalten.
Fehlende Werte sind unbekannt; boolesche, negative, nichtendliche oder
anderweitig ungültige Werte werden nicht als null oder Schätzung verrechnet.

Bei gültigen bereits gesammelten Counts und noch fehlenden Nutzungsmetriken
liest ein expliziter `collect`-Aufruf höchstens einmal je bekannter Job-ID die
Metriken nach. Er ruft deren Ergebnisse nicht erneut ab und reicht keine Jobs
ein. Spätere manuelle Aufrufe bleiben möglich. Frühere Metrikbeobachtungen und
Fehler bleiben im Ledger erhalten; ungültige vorhandene Werte werden nicht
still durch einen günstigeren Nachlesewert ersetzt.

Unklare Einreichungen behalten ihre volle Reservierung. Ein Timeout oder
unbekannter Ausgang erzeugt weder einen Retry noch einen Ersatzjob. Mockdaten
dürfen keinen echten Hauptjob freigeben: Ein echter Hardwareadapter verlangt
für alle Vorgänger und ihre Rohdaten tatsächliche Hardwareherkunft.

## Live-Gerätedaten und unveränderliche wissenschaftliche Basis

Das gepinnte Runtime-SDK aktualisiert lesend Backendkonfiguration,
Eigenschaften und Target. Vor einer Einreichung werden die frischen Fakten
mit dem versiegelten Snapshot des konkreten Plans verglichen: Backend,
Instanzbindung, physischer Bereich, Operationen, Dauern, Fehler,
Relaxationswerte, Readout und Timingbedingungen.

Neue Snapshots verwenden Schema 2 und speichern `Target.dt` sowie
`Target.timing_constraints()` mit ihrer SDK-Quelle. SDK-exponierte Werte
werden als solche bezeichnet; fehlende Angaben erhalten keine eigenen
Defaultwerte. Ein älterer Snapshot ohne diese Timingbasis bleibt als Archiv
gültig, reicht jedoch nicht für die neue Live-Ausführung aus.

Ein fortgeschrittener Kalibrationszeitstempel allein ist zulässig, wenn die
gebundenen Parameter exakt unverändert sind. Alter und aktueller Zeitstempel
bleiben getrennt erhalten. Rückläufige, fehlende oder zukünftige Zeitstempel,
veränderte Parameter oder nicht mehr unterstützte Repetition-Delay-Optionen
blockieren. Unbenötigte unbekannte Frequenz-/Instruktionsfelder bleiben
unbekannt und müssen ebenfalls unverändert sein; erforderliche Native-/Noise-
und Nutzungsdaten dürfen nicht fehlen.

Erfolgreiche und abgewiesene Live-Beobachtungen werden unter
`preflight/live_device/<assessment-sha256>/` separat archiviert. Sie ersetzen
weder den eingefrorenen Target-Snapshot noch QPY, Noise-Vorhersagen oder den
Plan. Bei Ablehnung wird die aktuelle Preflightdiagnose als `BLOCKED`
gespeichert. Die erfolgreiche Assessment-Seal wird zusätzlich in der
Freigabeevidenz des konkreten Ledgerjobs gebunden.

## Smoke-Kontrollen

Die vier eingefrorenen Basispräparationen werden über ihre Plan-/PUB-IDs,
QPY-Rohhashes, terminalen Messungen, logischen M2-Bits und tatsächlichen
klassischen/physikalischen Mappings geprüft. Counts werden vollständig
erhalten. Ein separater versiegelter Bericht nennt Musterhäufigkeiten,
exakte binomiale 95-%-Intervalle und die eingefrorenen idealen und
gerätebezogenen Vorhersagen.

Es gibt keine neu erfundene statistische Qualitätsgrenze. Falsche Outcomes
und null Treffer bleiben gültige Messdaten. Der harte Smoke-Vertrag betrifft
Identität, Mapping, vollständige Counts und Shotzahlen. Ein Mockbericht ist
`TEST_DATA_ONLY`; er belegt weder Hardwareausführung noch M5 PASS.

## Lokale Umgebung und Freigabe

Der Fix-Worktree nutzt den bestehenden Windows-Projektinterpreter
`C:\Users\flori\Documents\Quantum\.venv\Scripts\python.exe`.
`PYTHONPATH` zeigt für diesen Prozess auf `src` des Fix-Worktrees, damit die
editable Installation des alten Checkouts nicht versehentlich ausgeführt
wird. Paketpins und Benutzerprofil werden nicht geändert.

Der lesende Preflight benutzt ausschließlich den bestehenden Named Account
`km3-m5-open` und die explizite Bindung in der ignorierten lokalen Datei
`C:\Users\flori\Documents\Quantum\.m5-local\provider.json`.
Credentials werden nicht in den Worktree kopiert. Bekannte Secrets aus dem
lazy geladenen Named Account werden auch bei Job-, Result- und Metrikfehlern
vor dem Speichern öffentlicher Diagnosen entfernt.

Ein neuer Plan erhält einen neuen Hash und einen unbestätigten
`approval_request.json`. Beide Planflags bleiben false. Die alte lokale
Freigabedatei wird weder geändert noch übernommen. Vor einer späteren
Einreichung braucht genau dieser neue Plan die ausdrückliche menschliche
Freigabe und erneut frische Provider-, Geräte-, Kontingent- und Ledgerprüfungen.

Die Implementierungsprüfung umfasst einen tatsächlichen dreistufigen
CLI-Mocklauf mit echten lokalen QPY-/Smoke-/Device-Gates und gezielte
Negativfälle. Das historische M4-/Quellgleichheitsgate wird in dessen
Basispräparationsfixture ausdrücklich separat gestubbt; die vollständig
lokale Hardwarekompilierung prüft anschließend die tatsächlichen M4-Schaltungen.
Kein Mocktest kontaktiert IBM oder führt Hardware aus.
