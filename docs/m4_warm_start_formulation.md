# M4: eindeutige Relaxation und passender Warm-Start-Mixer

M4 verwendet genau eine klassische Box-Relaxation und den dazu passenden
Produktzustand mit Mixer. Die Vorberechnung erhält ausschließlich das
unveränderte M2-Polynom. Sie erhält keine CP-SAT-Planung, gespeicherten
Grundzustände, Exhaustive-Ergebnisse oder Bewertungsmasken. Die Schaltung
erhält ausschließlich das unveränderte Ising-Modell und numerische
Relaxationswerte. Die exakten Referenzen dienen getrennt der Auswertung.

## Kontinuierliche Fortsetzung

Das kanonische M2-Polynom lautet

\[
Q(x)=c+a^Tx+\sum_{i<j}b_{ij}x_ix_j,
\qquad x\in\{0,1\}^n.
\]

Insbesondere enthält die gespeicherte Darstellung keine quadratischen
Diagonalterme: M2 hat diese mit der ausschließlich binär gültigen Identität
\(x_i^2=x_i\) in die linearen Terme übernommen. Eine naive kontinuierliche
Fortsetzung wäre daher eine zusätzliche mathematische Entscheidung.

Für M4 setzen wir \(A_{ii}=0\) und
\(A_{ij}=A_{ji}=b_{ij}/2\). Die exakte, rationale Zeilensumme liefert

\[
L=\max_i\sum_{j\ne i}|A_{ij}|.
\]

Bei \(L>0\) wird \(\lambda=\operatorname{nextafter}(L,+\infty)\) gewählt,
also der nächste größere Float. Bei \(L=0\) bleibt \(\lambda=0\).
Die Implementierung prüft, dass Koeffizienten und \(L\) exakt als Float
darstellbar sind, und vergleicht \(\lambda\) nochmals rational mit \(L\).
Nicht darstellbare oder nicht endliche Werte werden abgewiesen.

Die symmetrische Matrix \(B=A+\lambda I\) ist aufgrund der
Gershgorin-Schranke positiv semidefinit: alle Eigenwerte liegen oberhalb
von \(\lambda-L\ge0\). Die gewählte Fortsetzung ist

\[
f(y)=c+(a-\lambda\mathbf1)^Ty+y^TBy,
\qquad 0\le y_i\le1.
\]

Für jede binäre Belegung verschwindet
\(\lambda\sum_i(x_i^2-x_i)\), also gilt \(f(x)=Q(x)\).
Der unveränderte M2-Hamiltonian wird weder verschoben noch reskaliert.
Die Diagonaltransformation betrifft ausschließlich die klassische
Vorberechnung; sie ist eine M4-Designentscheidung und keine bisherige
Eigenschaft des Encoders. Auf der kontinuierlichen Box ist der Zusatzterm
nicht positiv, weshalb diese Relaxation schwach beziehungsweise nahezu
uniform ausfallen kann.

## Deterministische numerische Vorberechnung

Ein einzelner L-BFGS-B-Lauf startet bei \(y_i=0.5\). Es gibt keine
zusätzlichen Startpunkte und keine Auswahl unter Relaxationsvarianten.
Primär sind höchstens 300 gemeinsame Funktions-/Gradientenauswertungen
und 300 Iterationen erlaubt; `ftol=1e-12`, `gtol=1e-8`, `maxls=20`.
Der analytische Gradient ist

\[
\nabla f(y)=a-\lambda\mathbf1+2By.
\]

Für diesen klassischen Optimierer werden Funktion und Gradient gemeinsam
durch den positiven, deterministischen Faktor

\[
S=\max\left(1,|c|,\|a-\lambda\mathbf1\|_\infty,
2\max_i\sum_j|B_{ij}|\right)
\]

dividiert. Dies ändert die Minimierer nicht. `lambda`, `numerical_scale`,
die verwendete Konfiguration, der unskalierte Funktionswert und die
numerischen Eigenwertdiagnosen werden gespeichert. Die Quantenschaltung
verwendet weiterhin die originale M2-Energieskala samt separat
ausgewiesenem konstantem Offset.

Die Bibliothek kann beim Line Search mehr Auswertungen anfordern als ihre
weiche `maxfun`-Grenze vermuten lässt. Deshalb verhindert ein zusätzlicher
Zähler jeden Aufruf oberhalb des festen Budgets. Funktion und Gradient
werden zusammen berechnet und zählen als eine Auswertung. Jeder Aufruf
und sein Punkt bleiben im Trace. Bei Abbruch bleiben der beste tatsächlich
ausgewertete Boxpunkt und der ausdrückliche Fehlerstatus erhalten.
Es gibt keinen Ersatz durch ein bekanntes Optimum. Diagnoseberechnungen
nach dem Lauf ändern den Punkt nicht und optimieren nicht weiter.

`library_success` ist kein rigoroses Optimalitätszertifikat. Zusätzlich
werden beide projizierten Gradientennormen gespeichert:

\[
r=\|y-\operatorname{clip}(y-\nabla f(y),0,1)\|_\infty,
\qquad
r_S=\|y-\operatorname{clip}(y-\nabla f(y)/S,0,1)\|_\infty.
\]

Sie verschwinden genau an einem KKT-Punkt dieser konvexen Boxaufgabe.
`numerically_stationary` prüft \(r_S\le\texttt{gtol}\) unabhängig vom
Bibliotheksflag. Aus der konvexen Tangentialungleichung entsteht außerdem
eine auswertbare untere Schranke:

\[
\Delta=\sum_i\left[\max(g_i,0)y_i+
\max(-g_i,0)(1-y_i)\right],\quad g=\nabla f(y),
\qquad f_{\rm lower}=f(y)-\Delta.
\]

Die Schranke und \(\Delta\) werden roh sowie skaliert berichtet.
Dies sind Float-Diagnosen, kein durch Intervallarithmetik oder symbolische
Beweise abgesichertes Zertifikat des numerisch erreichten Optimums.
Ein Bibliothekserfolg ohne kleinen Residuenwert ist daher ausdrücklich
von numerischer Stationarität zu unterscheiden.

Die vor der Hauptevaluation festgelegte Verwendungsprüfung verlangt
Bibliotheksstatus `CONVERGED`, `library_success=true`, einen endlichen
Boxpunkt und **beide** skalierten Größen \(r_S\le10^{-6}\) sowie
\(\Delta/S\le10^{-6}\). Das ist eine eigene numerische Qualitätsgrenze
für die näherungsweise Vorberechnung. Sie verändert weder `gtol=1e-8`
noch `ftol`, Auswertungsbudget oder Optimierer. Ein wegen `ftol`
konvergierter Punkt kann diese Qualitätsgrenze erfüllen, während das
strengere Diagnoseflag `numerically_stationary` weiterhin falsch bleibt;
dieses Flag und die tatsächlichen Residuen werden unverändert berichtet.
Budget-/Iterationsabbrüche, sonstige Bibliotheksfehler oder Überschreiten
einer der beiden Qualitätsgrenzen verhindern den Warm-Start-Lauf ohne
Oracle-Ersatz. Auch diese Verwendungsprüfung ist kein rigoroses Zertifikat.

## Zustand, klassische Kontrollen und Mixer

Primär bleibt \(\varepsilon=0.25\) vor der Evaluation festgelegt:

\[
c_i=\operatorname{clip}(y_i,\varepsilon,1-\varepsilon),
\qquad\theta_i=2\arcsin\sqrt{c_i},
\qquad|\phi\rangle=\bigotimes_i R_Y(\theta_i)|0\rangle.
\]

Die Wahrscheinlichkeit für Bit eins an M2-Index \(i\) ist genau \(c_i\).
Der Basisindex ist unverändert \(\sum_i x_i2^i\): Qubit null ist der
niederwertige Index. Die vollständige Produktverteilung ist
\(p(x)=\prod_i c_i^{x_i}(1-c_i)^{1-x_i}\). Sie kann vollständig klassisch
durch unabhängige Bernoulli-Bits gesampelt werden und enthält noch keine
QAOA-Schicht. Ihr möglicher Erfolg ist klassische Vorarbeit.

Die zweite klassische Kontrolle rundet die **ungeclippten** Werte
deterministisch: \(x_i=1\) genau dann, wenn \(y_i\ge0.5\).
Exakte Ties gehen somit zu eins. Weder Runden noch Decoding reparieren
eine ungültige Planung. Die Bewertung bleibt im getrennten M1-Pfad.

Die installierte M3-Schaltung verwendet
\(H_{\rm standard}=+\sum_i X_i\), Cost-Evolution
\(e^{-i\gamma H_C}\) und Mixer-Evolution \(e^{-i\beta H_M}\).
Der Warm-Mixer übernimmt dieses positive Vorzeichen:

\[
H_{\rm warm}=\sum_i R_Y(\theta_i)Z_iR_Y(\theta_i)^\dagger.
\]

Für einen Qubit gilt unabhängig von der SDK-Implementierung

\[
R_Y(\theta)ZR_Y(\theta)^\dagger=
\begin{pmatrix}
1-2c&2\sqrt{c(1-c)}\\
2\sqrt{c(1-c)}&2c-1
\end{pmatrix},
\]

\[
U(\beta)=e^{-i\beta H_{\rm warm}}
=R_Y(\theta)R_Z(2\beta)R_Y(-\theta).
\]

Die rechte Matrix wirkt zuerst: die tatsächliche Append-Reihenfolge ist
`RY(-theta)`, `RZ(2*beta)`, `RY(theta)`. Der Anfangszustand ist bei diesem
Vorzeichen der **+1-Eigenzustand** jedes Mixerterms; der Mixer allein
ändert den gesamten Produktzustand lediglich um \(e^{-in\beta}\).
Ihn als Grundzustand zu bezeichnen wäre mit der tatsächlichen positiven
M3-Konvention falsch. Die häufig dargestellte negative Warm-Mixer-Konvention
hat dieselbe Anfangsrichtung als Grundzustand; M4 übernimmt sie nicht
still und ändert keine M3-Winkelbedeutung.

Bei \(\varepsilon=0.5\) gilt \(c_i=0.5\),
\(\theta_i=\pi/2\), \(|\phi\rangle=|+\rangle^{\otimes n}\) und
\(H_{\rm warm}=+\sum_iX_i\). Somit reproduziert der gesamte Ansatz bei
denselben Winkeln M3 bis auf globale Phase.

Die Warm-Schaltung verwendet die tatsächlichen `Parameter`-Objekte des
unveränderten M3-Templates, mit expliziter Namenbindung pro Schicht.
Alle diagonal kommutierenden Cost-Terme werden exakt als `RZ` und `RZZ`
eingefügt; der konstante Offset wäre nur eine globale Phase und bleibt
für die Energieauswertung separat erhalten. Ein nichtkommutierender
X/Z-Mixer wird nicht ungeprüft an eine Standard-Trotterzerlegung gegeben.
Bei konstantem Cost sind beide Ansätze parameterfrei; Warm-Start behält
seine Produktverteilung, Standard-QAOA seine uniforme Verteilung.

## Unabhängige Prüfungen und Grenzen

Die neuen Tests prüfen jede binäre Energie kleiner Polynom-Fixtures,
die Gershgorin-Schranke und numerische PSD, analytische Gradienten gegen
zentrale Differenzen, KKT-/Tangentialdiagnosen und harte Budgetabbrüche.
Weitere Tests vergleichen den Mixer mit unabhängigen 2×2-Matrizen und
`scipy.linalg.expm`, prüfen Anfangswahrscheinlichkeiten, den eigenen
Mixer-Eigenzustand, asymmetrische Mehrqubit-Cost-Phasen sowie den gesamten
Ansatz bei \(\varepsilon=0.5\) gegen die unangetastete M3-Schaltung.
Es gibt keinen Test, der einen Warm-Start-Sieg verlangt.

Das Design ist auf höchstens zehn aktive Qubits begrenzt. Ein kleines
numerisches Experiment mit dieser einzelnen Relaxation stellt keine
allgemeine Scheduling-Garantie oder Aussage über größere Instanzen dar.
M4 trainiert ausschließlich ideal; Noise wird erst nach dem Training
mit unveränderten Parametern untersucht. Kein Training unter Noise,
Epsilon-Tuning, Alternativmixer oder Relaxations-Shopping gehört dazu.

Methodischer Ausgangspunkt sind Zustandserzeugung und kontinuierlicher
Warm-Mixer in [Egger, Mareček und Woerner, Abschnitt II.2](https://arxiv.org/html/2009.10095v3#S2.SS2).
Die vorliegende Gershgorin-Boxfortsetzung, ihr festes numerisches Budget
und die positive M3-Vorzeichenanpassung sind ausdrücklich die hier
hergeleiteten M4-Entscheidungen. Die
[IBM-Methodenreferenz](https://quantum.cloud.ibm.com/docs/en/tutorials/warm-start-qaoa)
liefert Kontext; Tutorial-Code, andere Relaxationen und Hardwarepfade
werden nicht ungeprüft übernommen.
