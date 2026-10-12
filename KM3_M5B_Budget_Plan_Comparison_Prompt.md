M5B — Laufzeitaufschlüsselung und genau zwei lokale Planungsvarianten
Auftrag und Sicherheitsgrenze
Erstelle einen nachvollziehbaren lokalen Planungsvergleich. Keine QPU-Einreichung und keine selbst erteilte Freigabe. Dieser Auftrag erlaubt die Berechnung von Entwürfen, nicht deren Hardwareausführung oder die Aktivierung höherer Grenzen.
Repository: drPanda1893/KM-Quantum-Optimization-Research-Dynamic-Workforce-Scheduling
Gemeldeter Ausgangspunkt:
- Branch fix/m5b-preflight-contracts
- HEAD 5d771d227ddf5036007173f8eac1c45bb46c044f
- blockierter Planhash a2288adfb1bd56336ef7df8ff33de4ed9085689c71c9537d655fd4cb473c17ac
- 1.442 Tests und Tooling laut Abschlussbericht bestanden
- lokales Risikoszenario: 67,83 Sekunden je Hauptjob und 145,83 Sekunden insgesamt
- aktive Grenzen: 40 Sekunden je Job / 120 Sekunden insgesamt
- bisher drei Jobs, 52 PUBs und 49.664 Shots
- beim letzten Providerabruf Open/free, 600 Sekunden Restnutzung, keine offenen Jobs
- null eingereichte Hardwarejobs
Die Zahlen sind zu prüfende Ausgangsangaben. Den letzten Nutzungsstand nicht als dauerhaft aktuell behandeln. API-Key und vollständige CRN niemals ausgeben.
1. Lokale Quellen lesen und Stand sichern
Lies den vollständigen lokalen Bericht docs/m5b_preflight_blocker_fix_report.md, die zugehörigen Schätzartefakte, die reparierten Schätzer-/Provider-Komponenten sowie Plan, Freigabe und Ledger. Prüfe die tatsächliche vollständige SHA und vorhandene Änderungen.
Arbeite für neue Änderungen auf einem eigenen Planungsbranch vom geprüften Fixstand. Die zwei alten ungetrackten Berichte weder löschen noch automatisch hinzufügen. Kein pauschales git add ., kein Reset, Merge, Force-Push oder Remote-Push.
Alten Plan samt BLOCKED-Nachweis, Schätzungen und Fix-Evidenz unverändert behalten. Neue Ergebnisse in ein neues versioniertes Verzeichnis schreiben. Historische Referenzen, M4-Winkel und native Schaltungen nicht umdefinieren.
2. Den Status richtig erklären
Trenne ausdrücklich:
- Providerrestnutzung;
- lokale freigegebene Obergrenzen;
- Basisschätzung;
- lokales Risikoszenario;
- serverseitiges max_execution_time;
- tatsächlich gemessene spätere QPU-Nutzung.
Eine Überschreitung unserer 40/120-Grenzen ist nicht automatisch eine Ausschöpfung des IBM-Kontos oder eine Providerablehnung. Zeige im Abschluss zusätzlich einen eindeutigen Grund wie LOCAL_CAMPAIGN_LIMIT_EXCEEDED, ohne alte Artefakte umzuschreiben.
Die lokale Risikoschätzung ist keine garantierte obere Laufzeitschranke und kein IBM-Angebot.
3. Erst die 67,83 Sekunden aufschlüsseln
Erzeuge für Smoke und beide Hauptjobs eine Tabelle mit:
- PUBs, expandierten Schaltungen und tatsächlichen Executions;
- Shots je Schaltung;
- Daueranteilen aus nativen Gates und Messungen;
- Initialisierungs-/Reset- und rep_delay-Anteilen;
- festem Verwaltungs-/Unterjobaufwand;
- Unterteilungsannahmen und deren Quelle;
- Sicherheitsaufschlägen;
- Basisschätzung und Risikoszenario;
- Budgetabstand;
- Einheiten, Snapshot- und Konfigurationshashes.
IBM beschreibt ungefähr zwei Sekunden Overhead je internem Teiljob. Das ist keine pauschale dokumentierte Regel „zwei Sekunden je PUB“. Prüfe, ob die bisherige Rechnung eine solche Gleichsetzung als vorsichtige lokale Annahme oder als angeblichen Providerfakt behandelt.
Keine Annahme nur deshalb günstiger wählen, weil der Plan sonst blockiert bleibt. Eine tatsächliche Zähl-/Einheiten-/Doppelzählungs-Korrektur benötigt Beleg und Regressionstest.
Berechne außerdem den theoretischen festen Anteil bei verschwindender Shotzahl. Das ist nur eine Analyse, kein gültiger Null-Shot-Hardwareplan. Wenn der feste Anteil bereits die Grenze überschreitet, vermerke ausdrücklich, dass reine Shot-Reduktion bei unveränderter Unterteilungsannahme nicht genügt.
4. Variante A — gleicher Versuch, 512 statt 1.024 Haupt-Shots
Behalte vollständig:
- Backend ibm_fez, vorbehaltlich aktueller Erreichbarkeit;
- Fälle A/E/H/K;
- Standard- und Warm-Start-QAOA;
- p=1 und p=2;
- gespeicherten M4-Restartseed 0;
- alle acht Initialverteilungskontrollen;
- beide getrennten Hauptmessblöcke;
- sämtliche vier Smoke-Kontrollen;
- ursprüngliche Compiler-, Reset-, rep_delay- und Noise-Entscheidungen;
- unveränderte aktive 40/120-Grenzen und 0-Euro-Regel.
Ändere im neuen Entwurf ausschließlich die Shots aller Haupt-PUBs symmetrisch auf 512. Smoke bleibt viermal 128.
Erwartete Zählung, unabhängig nachrechnen:
- Smoke: 4 PUBs, 512 Shots gesamt;
- Hauptblock 1: 24 PUBs, 12.288 Shots;
- Hauptblock 2: 24 PUBs, 12.288 Shots;
- insgesamt 3 Jobs, 52 PUBs, 25.088 Shots.
Schätze die Laufzeit neu aus den tatsächlichen Eingängen. Nicht einfach 67,83 durch zwei teilen. Feste Aufwände, interne Teiljobannahmen, Rundung und Aufschläge separat behandeln. Basiert eine Unterteilung auf Nutzlast/Executions, muss sie konsistent neu ermittelt werden.
Bestehen alle Gates, kann ein neuer unbestätigter Plan für Variante A erzeugt werden. Auch READY_FOR_APPROVAL ist keine Einreichungsfreigabe. Nenne den Abstand zu den Grenzen; eine sehr knappe Schätzung ist keine Robustheitsgarantie.
Wenn A nicht passt: ehrlich BLOCKED ausgeben, keine automatische weitere Shot-, Fall-, Tiefen- oder Jobreduktion.
5. Statistik der geringeren Messzahl
Weniger Shots ändern bei derselben idealen Schaltung nicht die theoretische Verteilung, aber die Genauigkeit ihrer empirischen Schätzung. Quantifiziere den Unterschied zwischen 1.024 und 512 Shots.
Für eine Bernoulli-Metrik gilt bei gleicher wahrer Wahrscheinlichkeit der Standardfehler sqrt(p*(1-p)/N). Bei Halbierung von N wächst er um sqrt(2). Für kleine Trefferwahrscheinlichkeiten exakte beziehungsweise geeignete Binomialintervalle verwenden; null Treffer sind kein Nachweis einer exakt null großen Wahrscheinlichkeit.
Metriken weiter getrennt ausweisen:
- Grundzustand des vollständigen Encodings;
- optimaler decodierter Plan;
- Encoding-Feasibility;
- Planungs-Feasibility.
Sampling-Tests/Intervalle müssen die tatsächliche neue Shotzahl verwenden. Keine Fixierung auf 1.024, keine weniger strengen Prüfschwellen nur zum Bestehen. Die zwei Hardwareblöcke zunächst getrennt analysieren; kein automatisches Zusammenlegen, das mögliche Unterschiede verdeckt.
6. Variante B — unveränderter Umfang, nur ein nicht aktives Budget-Amendment
Erstelle zum Vergleich einen ausdrücklich NICHT ausführbaren Alternativentwurf mit den ursprünglichen 49.664 Shots und dem ursprünglichen wissenschaftlichen Umfang.
Vorgeschlagene, noch NICHT genehmigte Reservierungen:
- Smoke: höchstens 20 QPU-Sekunden;
- Hauptblock 1: höchstens 80 QPU-Sekunden;
- Hauptblock 2: höchstens 80 QPU-Sekunden;
- Kampagne insgesamt: höchstens 180 QPU-Sekunden;
- unverändert höchstens drei Jobs, einer gleichzeitig, 0 Euro, nur verifizierter Open Plan.
20+80+80=180 ist eine von uns vorgeschlagene Budgetaufteilung, kein Providerwert. Verifiziere rechnerisch, ob der bestehende Risikoschätzer unter diesen vorgeschlagenen Grenzen bleibt, und zeige die Reserven. Aus der Kontingentverfügbarkeit allein folgt keine Genehmigung.
Dieser Auftrag erlaubt NICHT, aktive 40/120-Guards oder Freigabedateien auf diese höheren Werte umzustellen. Speichere B getrennt als Budget-Amendment mit Status OWNER_BUDGET_APPROVAL_REQUIRED, außerhalb des Einreichungspfads. Ein Hash des Entwurfs ist keine Freigabe und darf nicht mit einem ausführbaren Planhash verwechselt werden.
Ist eine Schemaerweiterung allein zum Anzeigen des Entwurfs nötig, halte sie vom Submission-Code getrennt. Keine großzügigeren Defaultwerte, keine universelle Abschaltung der Schutzprüfungen.
Erst nach ausdrücklicher späterer Zustimmung zu einem Budget-Amendment könnte ein gesonderter Implementierungs-/Prüfschritt einen entsprechenden konkreten Hardwareplan vorbereiten. Das ist nicht Teil dieser Aufgabe.
7. Snapshot-, Konto- und Ergebnisintegrität
Verwende für den reinen Variantenvergleich denselben geprüften Schaltungs-/Targetstand, damit nur die beabsichtigte Variable verändert wird. Datum des Snapshots ehrlich erhalten.
Vor einem freigabefähigen konkreten Plan Konto, Restnutzung und offene/unklare Jobs erneut lesend prüfen. Keine Zugriffsdaten neu einrichten. Verfügbarkeit und Kalibrationsänderungen getrennt vom reinen Budgetvergleich berichten. Erfordert eine Targetänderung neue native Schaltungen, müssen die konkreten lokalen Gates und Hashes erneut geprüft werden.
Shotabhängige Simulationsergebnisse, Bootstrap-/Konfidenzintervalle und Sampling-Dateien dürfen nicht aus alten 1.024-Shot-Ergebnissen als neue 512-Shot-Messung ausgegeben werden. Exakte Verteilungen und identische Schaltungen können mit Referenzhash wiederverwendet werden. Keine neuen Hardwaredaten behaupten.
8. Tests und Abschluss
Teste mindestens:
- fixe und shotabhängige Zeitanteile;
- theoretische Overhead-Untergrenze;
- heterogene Shotzahlen und vollständige PUB-Zählung;
- unveränderte aktive Grenzen;
- Variante A oberhalb/unterhalb der Grenzen;
- Variante B kann weder freigegeben noch eingereicht werden;
- neue Samplinggrößen und Intervalle;
- Hash-/Planbindung und unveränderte Historie.
Führe bei Codeänderungen gezielte Tests, vollständige Tests, Ruff, Format, mypy und Build tatsächlich aus. Testzahlen nicht von der vorigen Meldung kopieren. Ohne Codeänderung klar benennen, welche vorhandenen Nachweise referenziert und welche Planungsprüfungen neu ausgeführt wurden.
Liefere eine knappe Gegenüberstellung: ursprünglicher Plan, A, B. Pro Variante Shots, Jobs, PUBs, Basis-/Risikoschätzung, feste Kosten, Reservierung und Status nennen. Erkläre, ob 512 Shots den konkreten Blocker lösen. Begründe eine Empfehlung ohne Hardware- oder Budgetfreigabe.
Saubere lokale thematische Commits; kein Push/Merge ohne Auftrag. Alte ungetrackte Berichte unverändert lassen. Branch, vollständige HEAD-SHA und Arbeitsbaumstatus nennen.
Unveränderte Verbote
- Kein submit-next, kein Smoke- oder Hauptjob.
- Kein Sampler-/Estimator-Hardwarelauf, auch nicht zum Schätzen.
- Kein Upgrade, kein Pay-As-You-Go und kein Kauf.
- hardware_enabled=false und submission_approved=false.
- Keine Selbstfreigabe und keine automatische Budgeterhöhung.
- Keine geänderten Winkel, Fälle, Tiefen oder günstiger gewählten Seeds.
- Keine Änderung von rep_delay allein zum Verkürzen der Laufzeit.
- Keine Mutation historischer Ergebnisse oder Aufweichung der Noise-Prüfungen.
- Kein automatisches Wiederholen unklar ausgegangener Jobs.
Offizielle Grundlagen für Quellenprüfung
https://quantum.cloud.ibm.com/docs/en/guides/estimate-job-run-time
https://quantum.cloud.ibm.com/docs/en/guides/max-execution-time
https://quantum.cloud.ibm.com/docs/en/guides/minimize-time
Arbeite weiterhin mit dem vorhandenen gepinnten SDK. Neuere Dokumentationsbeispiele sind keine Erlaubnis für unkontrollierte Updates.