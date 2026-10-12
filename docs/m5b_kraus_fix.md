# M5B — numerische Kraus-Rekonstruktion

Die interne Rekonstruktion des gerätebezogenen Fehlermodells verwendet jetzt
`NoiseModel.to_dict(serializable=False)`. Die Zahlen bleiben komplexe numerische
Matrizen. JSON-Exporte anderer Artefakte bleiben unverändert. Es gibt weder ein
Dependency-Upgrade noch eine Lockerung von Kalibrations-, Kanal- oder
Dichtematrixprüfungen. Keine QPU-Jobs werden für diese Reparatur ausgeführt.

## Nachgewiesene Ursache

Der installierte Aer-0.17.2-Quellstand wurde geprüft. `AerJSONEncoder` codiert
komplexe Zahlen als `[real, imag]`. `QuantumError.from_dict` reicht dagegen
Kraus-Parameter direkt an `Kraus(...)` weiter. Im JSON-Pfad wird eine komplexe
2×2-Matrix dadurch als Array mit Form `(2,2,2)` interpretiert. Der Abbruch erfolgt
in `qiskit.quantum_info.operators.op_shape.OpShape._validate`, nach
`quantum_error.py:383` und `Kraus(elem["params"])`.

Die lokale Diagnose aus M5B-v3 wurde mit nichtunitärer Relaxation für `T2>T1`
und kombinierten CZ-/Relaxationsfehlern reproduziert. Die `T2<=T1`-Kontrolle
bestand, weil diese Relaxationsdarstellung keine komplexen Kraus-Matrizen enthält.

Zusätzlich wurde ein kleiner betroffener Kanal aus einem tatsächlich lesend
abgerufenen `ibm_fez`-Fehlermodell gesichert:
[Fixture](../tests/fixtures/m5/ibm_fez_kraus_channel_20261012.json). Er betrifft
`id` auf physischem Qubit 0. Die Fixture hält Herkunft, Abruf- und
Kalibrationszeit sowie Parameter und Kanalhash fest. Sie ist eine gespeicherte
Kalibrations-/API-Evidenz, keine Hardwaremessung. Der neue lesende Abruf wird nicht
als ursprünglicher M5B-v3-Zeitpunkt ausgegeben.

Der tatsächliche Backend-Reproducer scheiterte vor der Änderung an derselben
Stelle; sein bereinigter Trace liegt in
`results/m5b-fix-20261012-v1/actual_backend_kraus_trace_before.json`.
Die Regression decodiert ausschließlich die explizit gekennzeichneten
Kraus-/Unitary-Matrixfelder dieser JSON-Fixture und prüft deren Superoperator
gegen den separat gespeicherten numerischen Referenzkanal. Metadaten werden
nicht pauschal umgewandelt.

Installierte und versionierte Primärquellen:

- [Aer 0.17.2 NoiseModel/AerJSONEncoder](https://github.com/Qiskit/qiskit-aer/blob/0.17.2/qiskit_aer/noise/noise_model.py)
- [Aer 0.17.2 QuantumError.from_dict](https://github.com/Qiskit/qiskit-aer/blob/0.17.2/qiskit_aer/noise/errors/quantum_error.py)
- [Aer 0.17.2 Mischungsfilter](https://github.com/Qiskit/qiskit-aer/blob/0.17.2/src/noise/quantum_error.hpp)
- Installiertes Qiskit 2.5.2:
  `qiskit/quantum_info/operators/channel/transformations.py::_choi_to_kraus`,
  `ATOL_DEFAULT=1e-8`.

## Kleine Mischungsterme erhalten

Die vorhandene Zusammenfassung zu einem einzigen Kraus-Kanal bleibt erhalten.
Sie ist notwendig, weil Aers C++-Mischungsloader Terme mit Wahrscheinlichkeit
`p<=1e-10` verwirft. Die bisherige Umwandlung
`QuantumError(error.to_quantumchannel())` verwendet jedoch eine kanonische
Kraus-Rekonstruktion des Gesamtkanals. Im gepinnten Qiskit entfernt diese kleine
Choi-Eigenwerte. Für die geprüfte Pauli-Mischung mit `p=5e-11` betrug die
Kanalabweichung ungefähr `5e-11`; der vorhandene `1e-12`-Guard blockierte dies.

Die Reparatur setzt deshalb die ursprünglichen numerischen Kraus-Operationen
jedes Mischungsterms zusammen, bettet sie mit `Operator.compose(qargs=...)` in
die unveränderte lokale Qubitordnung ein und übernimmt alle gewichteten
Matrizen `sqrt(p)*K` in einen Kraus-Kanal mit Wahrscheinlichkeit eins.
`Kraus`, `Operator`, `QuantumError` und `NoiseModel` sind öffentliche APIs.
Es gibt keine Entfernung kleiner Terme, Eigenwertschwellenänderung, reale
Projektion, pauschales Reshape oder nachträgliche Zustandsnormalisierung.

Der Superoperatorvergleich mit absoluter Toleranz `1e-12` und der bestehende
CPTP-Guard bleiben aktiv. Die ursprünglichen M4-Dichtematrixprüfungen bleiben
unverändert. Auch Fehlerattachments und ihre gerichteten physischen Qargs
werden unverändert auf kompakte Indizes übertragen. Die numerischen
Kanalmatrizen bleiben temporär; Vorhersageartefakte archivieren weiterhin kleine
JSON-Wahrscheinlichkeitsvektoren und überprüfbare Metadaten.

## Tatsächlich ausgeführte Regressionen

Die neuen Tests wurden zuerst gegen die unveränderte Source vom Ausgangspunkt
`f31d4ee38372338d43f17cc2e301a2051b0f2a55` ausgeführt: **12 fehlgeschlagen,
3 bestanden**. Der Sourcehash blieb dabei unverändert; Log und Nachweis stehen
unter `noise_regressions_before.log` / `.json` im neuen Fix-Output.

Eine erste TP-Testkontrolle verwendete selbst die spektrale generische
`Kraus(SuperOp)`-Konversion. Sie wurde durch die direkte partielle Spur der
Choi-Matrix ersetzt, damit der Test keinen kleinen Eigenmodus entfernt. Der
korrigierte Testsatz wurde erneut gegen den originalen Git-Blob ausgeführt,
ohne den Arbeitsbaum zurückzuschreiben. Nach Ergänzung einer direkten
Kleinstterm-Zustandsprüfung lautet dieser Kontrolllauf **13 fehlgeschlagen,
3 bestanden**; Beleg: `noise_regressions_before_final_testset.log` / `.json`.
Der nachfolgende reparierte Lauf ist in `noise_regressions_after_final.log`
und `.json` dokumentiert.

Die 16 dedizierten Regressionen und 15 bestehenden Device-Noise-Tests bestanden
zusammen: **31 Tests**. Scoped Ruff, Format und mypy für die geänderte Source
wurden ebenfalls ausgeführt. Die vollständige Projektprüfung wird separat
berichtet.

Die zusätzliche numerische Prüfung von neun Kanal-Familien, einschließlich der
tatsächlichen Snapshot-Fixture, steht in `noise_channel_equality.json` im
Fix-Output. Maximale Superoperator-/Choi-Abweichung: `1.7763568394002505e-15`;
maximale TP-Abweichung der partiellen Choi-Spur: `1.887379141862766e-15`.
Alle Fälle bestanden die absolute Prüfung bei `1e-12`.

Der Testsatz prüft:

- nichtunitäre Ein-Qubit-Kanäle, komplexe unitäre und gemischte Kanäle;
- thermische Kraus- und Resetdarstellungen sowie kombinierte Zwei-Qubit-Fehler;
- den tatsächlich gesicherten Snapshotkanal und den vorhandenen `5e-11`-Term;
- Superoperator- und Choi-Gleichheit, Choi-Positivität innerhalb der Toleranz und direkte TP-Spur;
- direkte Anwendung `sum(K*rho*K†)` gegen Aers reparierte Dichtematrixauswertung;
- nicht zusammenhängende physische IDs und umgekehrte geordnete Qubitpaare;
- Readout separat und genau einmal sowie unveränderte Eingangsschaltungen;
- unveränderte ursprüngliche Fehlermodelldeklarationen.

Die Tests blockieren Netzwerkverbindungen und lesen keine Accounts oder
Credentials. M1–M4-Referenzen, Winkel, Kostenfunktion und frühere M5A-/M5B-Dateien
werden nicht verändert. Die Korrektur hebt die bekannte Approximation des
Backend-Fehlermodells nicht auf: Crosstalk, Drift und vollständige Idle-Zeitpläne
werden weiterhin nicht modelliert. Das Ergebnis ist kein M5-Hardware-PASS.
