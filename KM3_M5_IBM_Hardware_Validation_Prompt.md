M5 — IBM-Hardwarevergleich mit eingefrorenen QAOA-Schaltungen
1. Auftrag und verbindlicher Ausgangspunkt
Arbeite im Repository:
https://github.com/drPanda1893/KM-Quantum-Optimization-Research-Dynamic-Workforce-Scheduling
Der veröffentlichte M4-Branch ist feat/m4-noise-warm-start.
Der am 12. Oktober 2026 geprüfte Remote-Stand ist:
4ca11bf8f709aef9f43df4d3f2a0867189b54a7c.
Nicht den zuvor nur lokal gemeldeten Kurzcommit 440c7c9 blind als neuesten Stand verwenden. Löse die vollständige SHA auf und prüfe neuere lokale Änderungen, ohne sie zu verwerfen.
M4 dokumentiert 240 ideale Trainingsläufe, 1.200 Noise-Auswertungen, 1.094 bestandene Tests und 2.107 historisch unveränderte Dateien. Dies sind zu prüfende Referenzangaben, keine von dir bereits reproduzierten Befunde. Warm-Start zeigte keinen breiten Vorteil auf der neuen kleinen Suite. M4 nutzte synthetische Fehlerprofile ohne konkrete Hardwaretopologie.
Erstelle feat/m5-ibm-hardware-validation auf dem überprüften M4-Stand. Keine automatische Zusammenführung, kein Force-Push, keine Änderungen an Production. Historische Daten, Modelle, Penalties, Parameter und M1–M4-Auswertungen bleiben unverändert. Dependencies, CLI und README dürfen nachvollziehbar erweitert werden.
2. Forschungsfrage — keine neue Optimierung
Implementiere einen kontrollierten Vergleich bereits festgelegter Schaltungen:
1. Ursprüngliche ideale Schaltung aus M4.
2. Hardwareangepasste Schaltung, ideal simuliert.
3. Dieselbe hardwareangepasste Schaltung unter einem dokumentierten, gerätebezogenen Fehlermodell.
4. Tatsächliche Messungen dieser Schaltung auf einer IBM-QPU.
Primäre Frage: Wie unterscheiden sich reale Messverteilungen und Planungskennzahlen von unseren Vorhersagen für dieselbe Schaltung?
M5 ist ein kleiner Hardware-Transfer-/Validierungsversuch, kein Quantum-Advantage-Test und kein neuer Wirksamkeitsnachweis für Warm-Start. Kein neues Training, keine Optimierung auf Hardware, keine neuen Seeds nach Ergebnissichtung, kein Tuning von Penalties oder epsilon, keine Reparatur im Decoder, keine Error Mitigation, keine Decomposition, keine Produktintegration.
3. Zwei Freigabestufen; vorerst keine QPU-Jobs
Führe zunächst selbstständig die lokale Implementierung und Prüfung aus.
M5A — lokale Bereitschaft: Ohne Zugangsdaten, Netzwerkzugriffe im Testlauf oder Hardwareverbrauch. Verwende explizit gekennzeichnete Test-Backends/Snapshots und Mocks. Resultat höchstens OFFLINE_READY.
M5B — konkreter Hardwareplan: Nach lokal eingerichtetem IBM-Zugang dürfen explizit aufgerufene, lesende Preflight-Kommandos verfügbare Backends und Kontoinstanz prüfen. Metadatenabruf ist keine Hardwaremessung. Daraus entstehen der konkrete, gehashte Ausführungsplan und READY_FOR_APPROVAL.
Echte Einreichung erst nach ausdrücklicher Freigabe genau dieses Plans durch Florian. Dieser allgemeine Implementierungsauftrag ist keine Freigabe für unbekannte Cloudkosten oder beliebig viele Jobs. Bis dahin bleiben hardware_enabled=false und submission_approved=false.
Fehlende Credentials blockieren nur den konkreten Online-Schritt, nicht die lokale Implementierung. Nicht den ganzen Auftrag abbrechen, nur weil noch kein Konto eingerichtet ist. Keine automatischen Accounts, kostenpflichtigen Instanzen, Upgrades oder Käufe.
Ein Simulatorlauf, ein FakeBackend und ein reiner API-Aufruf dürfen niemals als echtes QPU-Ergebnis oder vollständiges M5-PASS ausgegeben werden.
4. Zuerst M4 lesen und Referenzen sichern
Lies vollständig:
- README und RESEARCH_PLAN;
- M1-Modell, Validator und Objective-Evaluator;
- M2-Encoding, Ising-Darstellung und Ground-State-Referenzen;
- M3/M4-Protokolle, Abschlussberichte und Acceptance;
- data/synthetic/m4/benchmark_manifest.json, Referenzen und Auswahlprotokoll;
- relevante gespeicherte M4-Preparations, Trainingsläufe und Schaltungsartefakte;
- src/km_quantum/m4/warm_start.py, noise.py, Training und Metriken;
- bestehende Bitordering- und Hilfsvariablen-Tests.
Speichere Referenzhashes vor/nach M5. Unterscheide echte Inhaltsänderungen von dokumentierten Git-CRLF/LF-Portabilitätsunterschieden. Binärarchive und Rohbytes nicht unbemerkt normalisieren. Veränderte Checksummen nicht einfach neu einfrieren, damit ein Gate grün wird.
Ein Regressionstest oder eine kleine unabhängige Reproduktion muss ausgewählte M4-Endverteilungen aus gespeicherten Parametern bestätigen. Das ist kein erneutes Optimieren. Historische Acceptance-Dateien nicht mit neu ausgeführten Resultaten überschreiben.
5. Kleine Hardware-Suite, im Voraus festgelegt
Verwende zunächst diese vier vorhandenen Fälle:
- A_single_job: einfache Zuordnung, 2 logische Qubits;
- E_overlap: Konfliktfall, 4 logische Qubits;
- H_workload: Kapazitätsfall, 5 logische Qubits;
- K_bounded_slack: Hilfsvariablen-/Diagnosefall, 7 logische Qubits.
Alle wurden bereits untersucht. Kennzeichne sie als bekannte strukturorientierte Hardware-Diagnosesuite, nicht als unbekannten Hold-out oder repräsentative Kundendaten. K wurde bewusst als bekannter Problemfall aufgenommen. Die Auswahl ist keine nachträglich verblindete Studie.
Je Fall: Standard und Warm-Start, p=1 und p=2, ausschließlich gespeicherter M4-Restartseed 0. Das ergibt 16 QAOA-Schaltungen. Kein Best-Seed-Picking; auch schlechte Parameter werden übertragen.
Zusätzlich je Fall zwei physisch auszuführende Kontrollen ohne QAOA-Schichten: uniformer Start und der eingefrorene Warm-Produktstart. Damit ergeben sich 24 Schaltungen pro Messblock. Die Kontrollen sind klassisch samplebar und dürfen nicht als Quantum-Zusatznutzen verkauft werden.
Zwei wiederholte Messblöcke mit jeweils 1.024 Shots pro Schaltung: 48 PUBs und 49.152 geplante Hauptshots. Zwei Blöcke sind keine unabhängigen neuen Planungsprobleme und belegen keine Langzeitstabilität.
Ein vorgelagerter Smoke-Job enthält vier kleine, vorab definierte Basispräparationskontrollen mit je 128 Shots: 512 weitere Shots. Verwende Null-, Eins- und zwei eindeutig verschiedene asymmetrische Bitmuster im relevanten Register. Das ist eine Zuordnungs-/Auslesediagnose, keine vollständige Readout-Kalibration oder Zustands-Tomographie.
Gesamter Vorschlag: 49.664 Shots, ein Smoke-Job und zwei Hauptjobs. Keine zusätzlichen Kalibrationsjobs, Wiederholungen oder automatischen Retrys außerhalb dieses Budgets.
Verändere diesen Plan nur vor Einreichung und mit dokumentierter, erneut freizugebender Planversion, falls das Konto oder technische Limits die Ausführung nicht erlauben.
6. Seeds sind keine Hardware-Zufallssteuerung
Der gespeicherte Trainingsseed identifiziert feste Parameter. Lokale Transpiler-, Monte-Carlo- und Reihenfolge-Seeds sichern Reproduzierbarkeit des Versuchsplans.
Sie machen reale QPU-Messungen nicht deterministisch. Erfinde keinen Hardware-Sampler-Seed. Bei Wiederholung sind Counts nicht bytegleich zu erwarten.
Lege die Reihenfolge der 24 PUBs vorab mit einem festen Seed fest; halte Standard/Warm-Paare zeitlich nahe beieinander und kehre ihre Reihenfolge im zweiten Block kontrolliert um. Das ist ein Versuch, zeitliche Verzerrungen zu reduzieren, keine Garantie gegen Drift. Provider können Reihenfolgen anders abarbeiten; tatsächliche zeitliche Informationen nur behaupten, wenn verfügbar.
7. IBM-Zugang und Kostenbegrenzung
Prüfe bei Implementierung die aktuellen offiziellen IBM-Dokumente und die tatsächlich kompatible Runtime-Version. Verwende eine stabile Sampler-Implementierung mit V2-Schnittstelle im Job-Modus, keine veralteten V1-Primitives. Erzwinge keinen experimentellen Executor-Umbau, falls die unterstützte stabile Sampler-Schnittstelle ausreicht. Keine unkontrollierten Updates des funktionierenden Qiskit-/Aer-Stacks.
Kontoinstanz und Backend werden explizit angegeben. Keine stille automatische Auswahl einer kostenpflichtigen Default-Instanz. Ausgangsbudget: 0 EUR, nur ausdrücklich verifizierter kostenloser Open Plan. Verfügbare Restnutzung und aktuelle Zugangsbedingungen prüfen; keine bestimmte Aktion oder kostenlose Zusatzzeit voraussetzen.
Vor Einreichung müssen lokal bekannt sein: Kontoinstanz, Plan, Backend, verfügbare Nutzung beziehungsweise bestätigter aktueller Nutzungsstand, erlaubte Shots und Jobs. Sind diese Angaben nicht sicher feststellbar, status OWNER_CONFIGURATION_REQUIRED, nicht zum Paid Plan wechseln.
Konservativer Vorschlag für diesen Plan: höchstens drei Provider-Jobs, höchstens 40 Sekunden angeforderte QPU-Ausführungszeit pro Job und höchstens 120 Sekunden insgesamt. Das sind vorgeschlagene Schutzgrenzen, keine Laufzeitprognose oder Garantie, dass sie genügen. Bei der Freigabe müssen Schätzung, Spielraum und tatsächlich verfügbare Quote zusammenpassen.
Setze die zur gepinnten API passende serverseitige Laufzeitgrenze; prüfe und dokumentiere ihre aktuelle Semantik. QPU-Nutzung ist nicht Queue- oder gesamte Wandzeit. Clientledger reserviert vor jedem Job dessen vollen genehmigten Betrag. Bereits laufende und im Ausgang unklare Jobs bleiben reserviert. Niemals nach Timeout blind nochmals einreichen.
Wenn SDK oder Service einen Aufruf intern in mehrere kostenwirksame Jobs zerlegt, muss diese Expansion vorab messbar und im Budget enthalten sein; sonst Einreichung blockieren. Unerwartete Providerkosten oder unbekannter Plan führen zum Stopp. Keine Behauptung einer technisch perfekten Euro-Kostengarantie allein durch ein Timeout.
8. Credentials bleiben ausschließlich lokal
Keine Tokens, Authorization-Header oder vollständigen Kontokonfigurationen in Prompts, Commits, Traces, Screenshots oder Fehlermeldungen speichern. Nutze sichere lokale Umgebungsvariablen oder einen explizit ausgewählten vorhandenen Account-Eintrag.
Keine interaktive Tokenabfrage, die das Secret im Log wiederholt. Keine automatischen Credential-Dumps. Öffentliche Artefakte enthalten ein pseudonymes Kontoinstanzmerkmal statt persönlicher Kontodaten. Für die lokale Submit-Prüfung darf die tatsächliche Instanz in einer ignorierten Datei stehen.
Alle Daten bleiben synthetisch. Kontoeinrichtung durch den Nutzer dokumentieren; keine Änderungen an Billing, Zugriffspolitik oder weiteren Projekten.
9. Backend und Qubit-Subset festlegen
Wähle einmalig anhand der aktuell zugänglichen operationalen Hardware, unterstützten Instruktionen und eines vorab dokumentierten Struktur-/Qualitätskriteriums ein Backend. Keine fest codierten Namen möglicherweise zurückgezogener Geräte. Ein echtes QPU-Backend muss von Fake-/Aer-Backends zweifelsfrei unterschieden werden.
Wähle einen zusammenhängenden kleinen physischen Bereich, in dem sieben logische Qubits und nötiges Routing Platz finden. Friere Auswahlregel, Backend, physische IDs und initiale logische Zuordnung vor dem ersten Hardwareergebnis ein. Für Standard/Warm derselben Instanz gilt dieselbe initiale Zuordnung.
Keine Auswahl des Layouts danach, welche Hardwaremessung besser aussieht. Keine Mehr-Backend-Kampagne. Falls Routing zusätzliche physische Qubits beansprucht, müssen diese in Grenzen, Mapping, Fehlermodell und Simulation ausdrücklich enthalten sein.
Gesamte aktive simulierte physische Breite einschließlich Routingqubits maximal zehn; vorzugsweise innerhalb des gewählten Siebenerbereichs. Kein stilles Vergrößern. Ein bloß breites Register mit unbenutzten Gerätedrähten darf nicht eine Simulation über den gesamten Chip auslösen.
10. Drei verschiedene Qubit-/Bit-Zuordnungen
Implementiere genau eine getestete Abbildungskette:
M2-Variablenindex -> logisches Qubit -> physisches Qubit während/nach Routing -> klassisches Messergebnisbit -> M2-Bitvektor.
Ein benanntes klassisches Register m2 mit einem Bit pro M2-Variable ist empfehlenswert. Terminale Messungen werden logisch eindeutig angebracht; Transpilation und finale Permutationen werden kontrolliert übernommen.
Nicht einfach alle Strings umdrehen und hoffen. Nutze die tatsächliche Messzuordnung und die vorhandene M2/Qiskit-Konvention. Vorhandene logische Endianness-Tests ersetzen keine Tests des physischen Layouts.
Prüfe absichtlich nichtidentische Layouts, asymmetrische Bitmuster, CX-Richtung, SWAP-/Routing-Permutationen und unbenutzte Gerätedrähte. Rohcounts und korrekt in M2-Reihenfolge dekodierte Counts separat speichern.
Vermeide measure_all() nach der Ausweitung auf die volle Gerätegröße: Es könnten sonst unbenutzte physische Qubits gemessen und die falschen Bits ausgewertet werden.
11. Neu für M5: Hardwarekompilierung
M4 nutzte rz/sx/x/cx ohne Hardwaretarget. M5 darf nicht voraussetzen, dass dies die native IBM-Basis ist. Die tatsächlich erlaubten Gatter und gerichteten Kopplungen stammen aus dem ausgewählten Backend-Target; beispielsweise CZ oder ECR nicht als CX behandeln.
Baue ursprüngliche logische Schaltungen aus eingefrorenen M4-Parametern und Warm-Vorbereitungen nach. Hardwarekompilierung ist eine neue, dokumentierte Darstellung, kein Ersatz historischer M4-Artefakte.
Nutze einen einheitlichen Preset-Passmanager, initial beispielsweise optimization_level=1, und festen Transpiler-Seed. Keine Suche über viele Einstellungen nach Hardwareerfolg. Vergleiche Standard/Warm unter derselben Compiler-Policy; unterschiedliche Gatekosten ehrlich dokumentieren.
Speichere logische und native Schaltungen als QPY sowie eine prüfbare Gate-/Parameter-/Mapping-Zusammenfassung, Hashes, Qiskit-Version und Target-Snapshot. QPY ist nicht versionsunabhängig; Metadaten und geladenes Round-trip-Verhalten prüfen. Keine manuellen Pulskalibrationen, dynamischen Schaltungen oder neuen Gattervarianten.
12. Lokales Pflichtgate vor jedem Hardwareplan
Für jede Hauptschaltung muss die ideale Messverteilung nach Hardwarekompilierung und korrektem Zurückmapping zur logischen M4-Verteilung passen, innerhalb vorab definierter strenger numerischer Toleranzen.
Prüfe zusätzlich Energie einschließlich Offset und sämtliche vier Wahrscheinlichkeitsmetriken. QPY-Round-trips dürfen diese Ergebnisse nicht verändern. Fehlende Gleichheit blockiert Hardwarejobs; nicht als Noise-Effekt erklären.
Simuliere nicht versehentlich 100+ Qubits, nur weil die native Schaltung die volle Backend-Breite trägt. Erzeuge eine rein umindizierte Simulationskopie aller tatsächlich beteiligten physischen Qubits einschließlich Routingdrähten. Nichttriviale Routing- oder Hilfsqubits dürfen nicht entfernt werden. Die Provider-Schaltung bleibt unverändert.
Rückmapping, eventuelle Randqubits und Marginalisierung müssen nachweislich dieselben gemessenen Registerverteilungen ergeben. Ein synthetisches Test-Target mit nichttrivialer Nummerierung ist Pflicht.
13. Gerätebezogenes Fehlermodell
Speichere vor der Messung einen Snapshot der verfügbaren gerätebezogenen Informationen: Target, erlaubte Gatter/Kopplungen, relevante Gate-Dauern/-Fehler, Readout-Informationen, T1/T2 soweit verfügbar, Abruf- und Kalibrationszeitstempel.
Erzeuge daraus mit den dokumentierten Aer-APIs ein explizit approximatives Backend-Fehlermodell. M4s künstliche Profile sind nicht die gemessene IBM-Kalibration und kein vorgeschriebenes Fehlerintervall für Hardwaredaten.
Das Modell muss zu genau den physischen Qubits, Gattern und Richtungen der kompakt umindizierten Schaltung passen. Remappe auch qubitspezifische Fehler und Readout-Matrizen. Andernfalls kann Noise auf falschen oder gar keinen Gattern liegen.
Erfasse die Fehlerabdeckung der tatsächlich ausgeführten Gatevorkommen. Fehlende Kalibrationswerte explizit behandeln, nicht ohne Kennzeichnung als perfekte Hardware setzen. Kein zusätzliches pauschales M4-Depolarisationsmodell auf bereits gerätebezogene Gatefehler addieren.
T1/T2 in Gatefehlermodellen ist nicht gleich vollständige Berücksichtigung aller Wartezeiten, Crosstalk oder Drift. Dokumentiere, ob Idle-/Delay-Effekte modelliert sind. Kein scheinbar vollständiges digitales Abbild des Geräts behaupten.
Friere die Vorhersagen vor den entsprechenden Hardwareergebnissen ein. Ein nachträgliches besser passendes Kalibrationsmodell darf nur separat explorativ berichtet werden. Wenn Daten für den vorgesehenen gerätebezogenen Vergleich fehlen, nicht heimlich auf ein beliebiges FakeBackend ausweichen; konkretes Gate als BLOCKED ausweisen.
14. Readout genau einmal und Provideroptionen explizit
Nutze die M4-Trennung: Dichtematrix vor Messung, Messwahrscheinlichkeiten und beobachtete Verteilung nach Readout. Bei analytischer Nachbearbeitung Readout einmal anwenden. Wenn ein Aer-Samplingpfad es bereits angewandt hat, nicht nochmals.
Primäre Hardwaremessungen ohne nachträgliche Readout-Korrektur. Keine Postselection ungültiger Pläne. Rohe nichtnegative Counts mit vollständiger Shot-Summe sind der primäre Datensatz.
Deaktiviere optionale Dynamical Decoupling, Gate-Twirling, Measurement-Twirling und vergleichbare zusätzliche Verarbeitung explizit, soweit von der gepinnten Runtime unterstützt. Kein erfundenes Sampler-resilience_level aus Estimator-Beispielen übernehmen. Unbekannte Optionen nicht schweigend ignorieren.
Normale Reset-/Initialisierungseinstellungen und Rep-Delay dokumentieren. Nicht pauschal behaupten, sämtliche physische Gerätekontrolle sei abgeschaltet. Berichte angeforderte Optionen und die tatsächlich vom Dienst bestätigten Metadaten getrennt.
Lokale Runtime-Testmodi können Optionen ignorieren. Ein erfolgreicher FakeBackend-Test beweist deshalb nicht, dass serverseitige Optionen wirken. Sichere API-Contracttests und Provider-Metadaten sind zusätzlich notwendig.
15. Sampler genügt für unsere Messung
Der Kosten-Hamiltonian ist diagonal in der Z-Basis. Werte die ursprüngliche QUBO-Energie direkt pro gemessenem M2-Bitstring aus und bilde ihren Stichprobenmittelwert. Dafür ist kein zusätzlicher kostenpflichtiger Estimator-Job nötig.
Nutze echte terminale Messergebnisse. Die QPU liefert keine vollständige exakte Statevector-Tabelle. Verwechsle aus Hardwarecounts geschätzte Wahrscheinlichkeiten nicht mit simulierten exakten Wahrscheinlichkeiten.
API-Importpfade und Result-Registerstruktur anhand gepinnter offizieller Dokumentation testen; nicht an einen vor Jahren verwendeten Wrapper binden. Eventuelle interne Mehrjob-Ausführung der gewählten Runtime muss mit dem Ledger kompatibel sein.
16. Zustandsmaschine und Freigabedatei
Erstelle eine kleine, dateibasierte Zustandsmaschine, keine neue Datenbank:
DRAFT -> OFFLINE_READY -> PROVIDER_PREFLIGHT_READY -> READY_FOR_APPROVAL
-> APPROVED -> SUBMITTED -> COLLECTED -> ANALYZED.
Zusätzliche Zustände: BLOCKED, PARTIAL, FAILED, CANCELLED, SUBMISSION_UNKNOWN.
Der Freigabeentwurf bleibt unbestätigt. Er enthält mindestens Manifest-/Schaltungshashes, exakte Instanzbindung, Backend, Layout, Ablaufdatum, Shots, PUBs, Jobzahl, Budget, Laufzeitlimits und Runtimeoptionen. Menschliche Zustimmung muss sich auf diesen Plan beziehen. Der Agent darf nicht selbst approved=true setzen.
Geänderte Schaltungen, Backend, Shots, Runtimeoptionen oder Budget machen die Freigabe ungültig. Eine nachträgliche Quota-/Operationalitätsprüfung darf enger blockieren, niemals still erweitern.
Before-submit-Ledger atomar schreiben, dann absenden und Provider-Job-ID unverzüglich persistieren. Falls der Dienst den Job möglicherweise angenommen hat, die Antwort aber fehlt: SUBMISSION_UNKNOWN, Budget weiter reserviert, keine automatische Doppeleinreichung. Mit lokalen IDs/Tags und lesendem Providerabgleich versöhnen; unbekannte Fälle zur manuellen Klärung offenlassen.
Keine wissenschaftlichen Ergebnisse wegwerfen, weil sie schlecht aussehen. Ein Smoke-Ergebnis kann organisatorisch geprüft werden; schlechte Häufigkeiten sind nicht automatisch ein Mappingbeweis. Nachvollziehbare Vertrags-/Datenfehler blockieren die Hauptjobs, reine geringe Lösungsqualität nicht.
17. Einreichung, Abholung und Unterbrechungen
Führe nach Freigabe genau einen Job zur Zeit aus. Speichere für jeden Job Planhash, lokale Job-ID, Provider-ID, PUB-Indexzuordnung, Circuit-Hash, tatsächliche Shots, Status und Zeitstempel.
Ein explizites Submit-Kommando reicht nur den nächsten genehmigten Job ein. collect holt existierende IDs lesend ab. Eine Unterbrechung, Queue oder auslaufende Arbeitsumgebung darf keine erneute Einreichung auslösen. Kein endloses Polling und keine Behauptung, die Ausführung sei schon abgeschlossen, während Ergebnisse ausstehen.
Resultate den persistenten PUB-/Circuit-IDs zuordnen, nicht anhand zufälliger Dateireihenfolge. Providerfehler und Teilresultate mit Originaldiagnose speichern, Secrets filtern. Keine Ersatzmessungen ohne zusätzliche Freigabe. Quota- und tatsächliche QPU-Nutzung nach Jobs dokumentieren; Queue, lokale Rechenzeit und QPU-Zeit getrennt behandeln.
18. Metriken ohne semantische Änderungen
Wiederverwenden statt M1–M4 umzudefinieren:
- p_schedule_optimal;
- p_ground_encoding;
- p_schedule_feasible;
- p_encoding_feasible;
- erwartete beziehungsweise empirisch gemittelte QUBO-Energie mit Offset;
- beste gültige Sample-Planung und Objective-Gap;
- gesamte rohe Histogramme;
- Anzahl verschiedener decodierter Pläne.
Ungültige Pläne behalten Objective null. Vollständige QUBO-Energie darf auch für ungültige Encodings berechnet werden. Unterschiede durch Hilfsbits bleiben sichtbar.
Zusätzlich: absolute Wahrscheinlichkeitsdifferenzen in Prozentpunkten gegen ideale und modellierte Vorhersage; Total-Variation-Distanz der Registerverteilungen. Keine Histogramm-Übereinstimmung als Quantenzustandsfidelity bezeichnen: Es wurde keine Tomographie durchgeführt.
Für Kontrollverteilungen jeweils denselben M1/M2-Evaluator nutzen. Ein Hardware-Warm-Produktstart ist kein quantenalgorithmischer Vorteil gegenüber klassischem Bernoulli-Sampling.
19. Statistik und fehlende Treffer
Reportiere Hauptblöcke zunächst getrennt. Bei deskriptivem Pooling beide Einzelresultate erhalten und zeitliche Abhängigkeit erläutern.
Für binäre Ereignisse wie optimaler Plan: vorab festgelegte 95%-Intervalle, etwa Wilson oder exakte Binomialintervalle, mit Annahmebedingungen. Null Treffer bedeutet nicht Wahrscheinlichkeit null. Zwei wiederholte Blöcke sind kein belastbarer Langzeit- oder Drift-Test.
Für Histogrammdistanzen den endlichen Shot-Effekt berücksichtigen: ziehe lokal aus jeder eingefrorenen Vorhersage beispielsweise 1.000 Multinomialstichproben derselben Größe mit festem Seed und zeige den erwarteten Stichprobenbereich der Distanz. Das ist keine Bestätigung, dass das Fehlermodell wahr ist.
Keine Gleichheit empirical == exact fordern. Das M3/M4-Sampler-Korrektheitsgate darf nicht als Forderung wiederverwendet werden, dass reale Hardware die ideale Verteilung einhält. Eine echte Abweichung ist ein Messbefund, kein automatischer Softwarefehler.
Keine Überlegenheit aus einem einzelnen Seed, keine Signifikanz durch Vermischung von Shots, Schaltungen und Instanzen, keine neue Warm-Start-Generalisierung aus dieser bekannten kleinen Suite.
20. Tests vor Verbrauch von Hardwarezeit
Mindestens: historische Integrität; Wiederherstellung fester M4-Parameter; ideale Parität vor/nach Hardwarekompilierung; nichtidentische physische und klassische Bitzuordnung; QPY-Round-trip; aktive Breite/RAM-Guards; qubitspezifisches Noise-Remapping; Readout einmal; API-/Optionenvertrag; fehlerhafte PUB-Zuordnung; unvollständige oder negative Counts; Secret-Redaktion; Schutz vor Paid-Fallback; Manifeständerungen nach Freigabe; Budgetreservation; Timeout nach möglicher Annahme; Resume ohne Resubmit; fehlende und gescheiterte Jobs; Status vor echten Daten.
Tests und CI dürfen niemals echte Jobs absenden, auch nicht, wenn lokal IBM-Credentials verfügbar sind. Netzwerk- und Submission-Grenzen müssen mockbar sein. Hardwaretests sind explizit deaktivierte, separat freizugebende Integrationstests.
Führe Regressionen, Ruff, Format, mypy und Build tatsächlich aus. Keine erfundenen Testzahlen. Eine vollständige lokale Testsuite beweist nicht, dass ein noch nie auf Hardware getesteter Pfad bereits dort funktioniert.
21. Artefakte und lokale Bedienung
Nutze eine kleine M5-Erweiterung in der bestehenden Architektur. Sinnvolle Dateien:
- docs/m5_experiment_protocol.md
- docs/m5_ibm_setup.md
- docs/m5_readiness_report.md
- später docs/m5_completion_report.md
- configs/m5_baseline.json
- data/synthetic/m5/selection_manifest.json
- results/m5/circuits/, predictions/, jobs/, raw/, summaries/
- results/m5/approval_request.json
- results/m5/m5-acceptance.json
Konkrete CLI konsistent mit dem bestehenden km-quantum-Einstieg implementieren: m5 prepare, verify-local, provider-preflight, plan, submit-next, collect, analyze, acceptance. README-Befehle müssen mit --help und Testläufen bestätigt sein. Keine vorgespielten Commands, die gar nicht implementiert wurden.
prepare und Standardtests sind offline. Providerzugriff nur über explizite Subcommands. submit-next ohne passende menschliche Freigabe verweigert die Einreichung. Ein Resume lädt geprüfte Artefakte, trainiert nichts neu und überschreibt keine Daten.
Trenne historische M4-Zahlen, neue M5-Simulationsvorhersagen, Mockdaten und echte Rohmessungen eindeutig durch Artefakttypen und Verzeichnisse. Keine riesigen Zustandsvektoren oder duplizierten JSON-Gesamtexporte ohne Nutzen archivieren. Nur synthetische Daten veröffentlichen; lokale Credentials und Freigabedetails redigieren.
22. Abschlussstatus und Erfolgskriterien
Acceptance enthält getrennt:
- implementation_status;
- historical_integrity;
- local_compilation_equivalence;
- bit_mapping_verified;
- device_noise_model_status;
- provider_configuration_status;
- approval_status;
- geplante/eingereichte/abgeschlossene/fehlende Providerjobs und PUBs;
- hardware_execution_status;
- hardware_used;
- raw_results_complete;
- analysis_status;
- observed_hardware_agreement;
- Einschränkungen und Kosten-/Nutzungsnachweise.
Ohne echte Messungen höchstens OFFLINE_READY oder READY_FOR_APPROVAL. Mit ausstehenden Jobs PENDING_HARDWARE beziehungsweise PARTIAL. Gesamt-M5-PASS erst nach gültiger Freigabe, vollständigen realen Pflichtmessungen, intakten Rohdaten, bestandenen technischen Gates und fertiger Analyse.
Gute QAOA-Leistung oder geringe Abweichung zum Fehlermodell sind KEIN technisches PASS-Kriterium. Schlechte Hardwareergebnisse sind erlaubt. Fehlende Hardwareergebnisse sind keine erfolgreichen Hardwareexperimente.
Am Ende der aktuellen Implementierungsrunde liefere Code-/Testbericht, Git-Stand, readiness, konkret fehlende lokale Nutzerschritte und den unbestätigten Ausführungsplan. Stoppe vor Einreichung, solange keine Freigabe vorliegt. Kein automatischer Push/Merge ohne Auftrag.
Nach tatsächlich genehmigter und abgeschlossener Messung: zeige pro Fall Schaltung, Histogramme, vier Metriken, Unsicherheit, Compilekosten, Vorhersagefehler und QPU-Nutzung. Sage ausdrücklich, was nicht auf Kosten-Hardwarevergleich, Beschleunigung oder Kundennutzen schließen lässt. Keine erfundenen Messergebnisse als Platzhalter.
23. Zu prüfende offizielle Quellen
Die folgenden Quellen wurden für den Auftrag herangezogen. Verwende zur Implementierung die zur tatsächlich gepinnten Version passenden Seiten; Dokumentationsstände können voneinander abweichen.
- IBM-Zugangspläne: https://quantum.cloud.ibm.com/docs/en/guides/plans-overview
- Sampler-Workflow: https://quantum.cloud.ibm.com/docs/en/guides/get-started-with-sampler
- Transpilation/ISA: https://quantum.cloud.ibm.com/docs/en/guides/transpile
- Bitordering: https://quantum.cloud.ibm.com/docs/en/guides/bit-ordering
- Lokaler Runtime-Testmodus und seine Grenzen: https://quantum.cloud.ibm.com/docs/en/guides/local-testing-mode
- Sampleroptionen: https://quantum.cloud.ibm.com/docs/en/guides/sampler-options
- Laufzeitlimits: https://quantum.cloud.ibm.com/docs/en/guides/max-execution-time
- Nutzung/Schätzungen: https://quantum.cloud.ibm.com/docs/en/guides/estimate-job-run-time
- QPY: https://quantum.cloud.ibm.com/docs/en/guides/save-circuits
- Aer-Backend-Fehlermodell: https://qiskit.github.io/qiskit-aer/stubs/qiskit_aer.noise.NoiseModel.html
Oberster Grundsatz
M5 soll erstmals echte Hardwaredaten liefern — aber erst nach lokaler Prüfung und bewusster Freigabe eines begrenzten Plans. Nicht gleichzeitig Hardware, Algorithmus, Parameter, Layout und Auswertungsdefinition ändern. Wir wollen nachvollziehbar feststellen, was das reale Gerät mit unseren bereits bekannten Schaltungen macht.