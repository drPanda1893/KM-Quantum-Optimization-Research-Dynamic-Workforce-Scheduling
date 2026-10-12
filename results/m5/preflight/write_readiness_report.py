from pathlib import Path

from km_quantum.m4.io import read_json
from km_quantum.m5.io import read_sealed

root = Path.cwd()
output = root / "results/m5"
acceptance = read_json(output / "m5-acceptance.json")
prepared = read_sealed(output / "prepared_manifest.json")
context = read_sealed(output / "preparation_context.json")
plan = read_json(output / "execution_plan.json")
request = read_json(output / "approval_request.json")
tooling = read_json(output / "preflight/tooling.json")
assert acceptance["status"] == "OFFLINE_READY"
assert not acceptance["hardware_used"] and not request["approved"]

lines = [
    "# M5 — lokaler Code-, Test- und Readiness-Bericht", "",
    "Stand: 12. Oktober 2026. **Gesamtstatus: OFFLINE_READY.**", "",
    "Die lokale Implementierung ist abgeschlossen und geprüft. Es gibt keine IBM-Hardwaremessungen,",
    "keine QPU-Einreichung, keine kostenpflichtige Instanz, kein Upgrade und keine menschliche Freigabe.",
    "`M5 PASS` wird nicht behauptet. Hardwareanalyse und beobachtete Übereinstimmung sind `NOT_AVAILABLE`.", "",
    "## Referenz, Auswahl und Git", "",
    "- Veröffentlichte M4-Referenz: `4ca11bf8f709aef9f43df4d3f2a0867189b54a7c`.",
    "- Neuer lokaler Branch: `feat/m5-ibm-hardware-validation`.",
    f"- Geprüfter Experimentquellstand: `{context['git']['commit']}`; beim finalen Lauf `dirty=false`.",
    f"- Portabler Quell-/Dependency-Kontext: `{prepared['source_context_sha256']}`.",
    f"- Auswahlmanifest: `{prepared['selection_manifest_sha256']}`.",
    f"- Vorbereitungsmanifest: `{prepared['content_sha256']}`.",
    "- 4.180 veröffentlichte Dateien vorab gesichert; alle **4.176 geschützten Dateien bytegleich**.",
    "  Nur README, pyproject, uv.lock und CLI-Einstieg wurden zur Integration geändert.",
    "  Es gibt keine CRLF/LF-Ausnahmen im finalen Integritätsbefund; Binärdateien werden nicht normalisiert.",
    "- Die 40 ursprünglichen Paketversionen sind unverändert; 22 Pakete wurden mit Runtime `0.47.0` ergänzt.",
    "- Code, Artefakte und Bericht sind in thematischen Commits archiviert. Der M5-Branch wurde am",
    "  12. Oktober 2026 zu `origin` gepusht; der veröffentlichte Artefaktstand ist",
    "  `605585be9012a511e3bff2ff1c5dc3c27f0ab3a2`. Es wurde kein Merge und kein QPU-Job ausgeführt.", "",
    "Die vier bekannten Fälle A/E/H/K bilden eine Diagnosesuite. K bleibt trotz seiner bekannten Schwäche",
    "enthalten. Die 16 QAOA-Schaltungen verwenden ausschließlich gespeicherte M4-Winkel für Standard/Warm",
    "bei p=1/2 und Restartseed 0; acht gespeicherte Starts und vier Basis-Smokes ergänzen sie.",
    "Kein Optimizer, keine neue Relaxation und keine Parameterauswahl anhand von Hardwareergebnissen wurden ausgeführt.", "",
    "## Tatsächlich ausgeführte Prüfungen", "",
    "| Prüfung | Ergebnis |", "| --- | --- |",
    f"| Gesamte pytest-Suite | **{tooling['pytest']['tests']} bestanden**, 0 Fehler, 0 Fehlschläge, 0 übersprungen; 488,16 s |",
    "| Historische Regressionen / neue M5-Tests | 1.094 / 256 |",
    "| Ruff check / Format | PASS / PASS, 109 Dateien |",
    "| mypy | PASS, 70 Quelldateien |",
    "| Offline-Build | sdist und Wheel erfolgreich; 17 M5-Module und exakter Runtime-Pin im Wheel geprüft |",
    "| CLI | alle acht --help-Aufrufe und reale Offline-Kommandokette ausgeführt |",
    "| Freigabeabwehr | submit-next mit unbestätigtem Offlineplan: Exit 1, BLOCKED vor Providerzugriff |",
    "| Resume | vorhandene QPY-Dateien/Hashes und Vorhersagen verifiziert, unverändert erhalten |", "",
    "249 Hinweise betreffen den vom gepinnten Runtime-Paket registrierten, als veraltet markierten",
    "`IBMFractionalTranslationPlugin`. Sie sind keine Testergebnisse oder Hardwarezugriffe.",
    "JUnit, Tooling, Dependency-Erhaltung, Buildmetadaten und CLI-Ausgaben sind unter",
    "[`results/m5/preflight`](../results/m5/preflight/) gespeichert.", "",
    "Der finale prepare-Lauf erzeugte 28 Schaltungen mit jeweils logischer, nativer Provider- und kompakter",
    "QPY-Datei: **84 QPY-Artefakte**. Alle 24 Hauptschaltungen reproduzieren die ursprüngliche M4-Verteilung,",
    "Offset-Energie und vier Metriken auch nach Kompilierung und Roundtrip.",
    f"Maximale ideale Wahrscheinlichkeitsabweichung: `{acceptance['local_verification_summary']['maximum_probability_absolute_difference']:.16g}`",
    "bei vorab festgelegtem Limit `1e-10`; Energielimit `1e-8`.",
    "Der Providerkreis trägt 53 Testdrähte; die Simulation nutzt höchstens sieben aktive physische Qubits",
    "einschließlich aller Routingdrähte, maximal 512 MiB und zwei Aer-/BLAS-Threads.", "",
    "Ein früher Entwicklungslauf deckte Spurverlust im direkten Aer-Mixturpfad auf. Aer 0.17.2 verwirft",
    "Mischungsterme mit p<=1e-10; beim untersuchten 144-Term-ECR-Kanal gingen etwa 8,60e-10 verloren.",
    "Die öffentliche Umwandlung `QuantumError(error.to_quantumchannel())` erhält denselben vollständigen",
    "Kanal als Kraus-Darstellung. Kanalgleichheit und CPTP werden mit `1e-12` geprüft; die Regression",
    "und der vollständige 28-Schaltungen-Lauf bestehen. Weder Dichtematrizen noch Toleranzen wurden angepasst.",
    "[Offizieller Aer-Quellstand 0.17.2](https://github.com/Qiskit/qiskit-aer/blob/0.17.2/src/noise/quantum_error.hpp#L188-L227).", "",
    "## Compilekosten und ausschließlich synthetische Vorhersagen", "",
    "Das Test-Target verwendet ECR und den festen physischen Bereich `[1,4,9,13,22,31,42]`.",
    "Seine Kalibration ist künstlich, mit ausdrücklich fiktivem Epoch-Zeitstempel; sie ist kein IBM-Gerätesnapshot.",
    "Die folgende Tabelle zeigt die primäre Metrik `p_schedule_optimal` als exakte lokale Vorhersage.",
    "Alle vier Metriken, Offset-Energie, vollständige Registerverteilungen, native Instruktionen und Mapping",
    "stehen in den einzelnen versiegelten [Vorhersagen](../results/m5/predictions/) und",
    "[Schaltungsdatensätzen](../results/m5/circuits/). Es sind keine empirischen Hardwarewahrscheinlichkeiten.", "",
    "| Fall / Algorithmus | p | aktive Qubits | 1Q / 2Q | Tiefe | ideal p_opt | synthetisch mit Readout p_opt |",
    "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
]
for record in prepared["records"]:
    if record["kind"] != "main":
        continue
    prediction = read_sealed(output / "predictions" / f"{record['circuit_id']}.json")
    summary = record["summary"]
    ideal = record["equivalence"]["logical_ideal_metrics"]["p_schedule_optimal"]
    model = prediction["device_metrics"]["device_after_readout"]["p_schedule_optimal"]
    lines.append(f"| {record['reference_name']} / {record['algorithm']} | {record['p'] or '—'} | "
                 f"{summary['active_width']} | {summary['one_qubit_gate_count']} / {summary['two_qubit_gate_count']} | "
                 f"{summary['depth']} | {ideal:.6f} | {model:.6f} |")
lines.extend([
    "", "Die Kanalabdeckung und qubitspezifische gerichtete Remaps sind geprüft. Readout wird analytisch",
    "genau einmal angewandt. Es gibt keine zusätzliche M4-Depolarisation, Readoutkorrektur oder Postselection.",
    "Idle/Delay, Crosstalk, Drift, reale Initialisierungsfehler und vollständige zeitliche Geräteabläufe",
    "werden nicht modelliert. Reale Target-Mittelwerte können nur eine ausdrücklich symmetrische",
    "Readoutapproximation liefern; fehlende erforderliche Kalibrationswerte blockieren den Gerätevergleich.", "",
    "## Konkreter unbestätigter Ausführungsentwurf", "",
    f"Planhash: `{plan['plan_hash']}`.",
    "[Executionplan](../results/m5/execution_plan.json),",
    "[unbestätigte Freigabe](../results/m5/approval_request.json),",
    "[Acceptance](../results/m5/m5-acceptance.json).", "",
    "| Job | PUBs | Shots/PUB | Shots | QPU-Cap |", "| --- | ---: | ---: | ---: | ---: |",
    "| smoke | 4 | 128 | 512 | 40 s |",
    "| main-block-1 | 24 | 1.024 | 24.576 | 40 s |",
    "| main-block-2 | 24 | 1.024 | 24.576 | 40 s |",
    "| Gesamt | 52 | — | 49.664 | 120 s |", "",
    "Feste gepaarte Reihenfolge mit Seed 20261012; vollständige Umkehr im zweiten Hauptblock.",
    "Runtime JOB-Modus, DD sowie Gate-/Measurement-Twirling aus, normale Initialisierung an, klassifizierte Counts.",
    "0 EUR nur bei verifiziert kostenlosem Open Plan; Zeitcaps sind Schutzgrenzen, keine Zeit-/Eurogarantie.",
    "In diesem Offlineentwurf sind echte Instanzbindung und Backend **null**. Das Testlayout wird nicht",
    "als reales Layout ausgegeben. Er ist deshalb ausdrücklich **nicht freigabefähig**.",
    "`hardware_enabled=false`, `submission_approved=false`, `approved=false`.",
    "Geplant: 3 Jobs/52 PUBs. Eingereicht/abgeschlossen: **0/0**; 52 reale PUBs fehlen.",
    "Reservierte QPU-Sekunden/Shots: 0/0. Provider-Nutzungsmetadaten und tatsächliche QPU-Zeit sind nicht verfügbar.", "",
    "## Fehlende Nutzerschritte und Stoppgrenze", "",
    "1. Vorhandenen benannten IBM-Account, ausdrücklich kostenlose Open-Instanz und operationalen Backend lokal wählen.",
    "   Konfiguration nur ignoriert unter `.m5-local/` oder über die expliziten Umgebungsvariablen hinterlegen.",
    "2. Den lesenden `provider-preflight` ausdrücklich mit neuem `results/m5-provider...`-Output ausführen.",
    "   Plan-/Preisdaten, Quote, Job-/PUB-/Shot-Limits und Kalibration müssen verifizierbar sein.",
    "   Unbekannte API-Felder oder Grenzen bleiben OWNER_CONFIGURATION_REQUIRED; SDK-Mocks belegen keine reale API-Verfügbarkeit.",
    "   Externe, nachvollziehbar geprüfte QPU-Schätzungen je festem Job können via `--usage-estimates` gebunden werden.",
    "3. Reale native Schaltungen/Noise-Vorhersagen vor Ergebnissen einfrieren und `plan` ausführen.",
    "   Den exakten neuen Plan samt Kosten-/Zeitcaps, Instanzpseudonym, Layout, QPY-Hashes und Optionen prüfen.",
    "4. Florian bestätigt ausschließlich eine ignorierte lokale Kopie mit exaktem Planhash, Signaturzeit und Ablauf <=24 h.",
    "   Erst danach darf ein explizites submit-next genau einen Job einreichen; collect liest vorhandene IDs einmalig.",
    "   Unklare Submission bleibt voll reserviert und verlangt manuelle Klärung; keine Ersatzjobs oder Retrys.",
    "5. Erst nach 3 realen Jobs, 52 vollständigen realen PUBs, intakten Rohdaten, Nutzungsnachweis und Analyse kann Acceptance PASS ergeben.", "",
    "Details und tatsächliche Commands: [IBM-Setup](m5_ibm_setup.md), [Protokoll](m5_experiment_protocol.md).",
    "**Die aktuelle Runde stoppt vor der Einreichung.** Es wird kein Hardware-Abschlussbericht mit Platzhalterdaten erzeugt.", "",
    "Die zwei Blöcke bleiben getrennt. 95%-Binomialintervalle und 1.000 feste Multinomialreferenzen pro",
    "Vorhersage berücksichtigen Shot-Effekte; null Treffer bedeuten keine Wahrscheinlichkeit null.",
    "Histogramm-TV ist keine Zustandsfidelity. Schlechte Hardwarewerte sind zulässige Befunde und kein",
    "Sampler-Korrektheitsfehler. Die bekannte kleine Suite belegt weder Warm-Start-Überlegenheit,",
    "Kosten-/Hardwarevorteile, Beschleunigung noch Kundennutzen.", "",
])
(root / "docs/m5_readiness_report.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
print("Readiness report generated from verified offline artifacts")
