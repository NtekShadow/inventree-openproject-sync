# OpenProject Sync für InvenTree

Synchronisiert InvenTree-**Fertigungsaufträge** und **Bestellungen** mit OpenProject-Arbeitspaketen und macht Verweise wie `OP#EL-5` überall in InvenTree klickbar.

Benötigt InvenTree ≥ 1.0. Projektbasierte IDs wie `EL-5` gibt es ab OpenProject 17.5, numerische IDs (`OP#1234`) funktionieren immer.

## Installation

**Über die InvenTree-Oberfläche** (*Einstellungen > Plugins > Plugin installieren*):

- Paketname: `inventree-openproject-sync`
- Quelle: `git+https://github.com/NtekShadow/inventree-openproject-sync@v0.1.2`

**Oder in der `plugins.txt`** von InvenTree (eine Zeile):

```
inventree-openproject-sync @ git+https://github.com/NtekShadow/inventree-openproject-sync@v0.1.2
```

Statt Git geht auch das Wheel aus dem [Release](https://github.com/NtekShadow/inventree-openproject-sync/releases), z. B. `inventree-openproject-sync @ https://github.com/NtekShadow/inventree-openproject-sync/releases/download/v0.1.2/inventree_openproject_sync-0.1.2-py3-none-any.whl`. Das Frontend ist schon gebaut und eingecheckt, ein Node-Build auf dem Server ist nicht nötig.

In InvenTree unter *Einstellungen > Plugins* müssen diese Integrationen aktiviert sein: **Ereignis**, **Zeitplan**, **URL** und **Oberfläche**. Danach das Plugin *OpenProject Sync* aktivieren.

## Einrichtung in InvenTree

Drei Einstellungen sind nötig:

| Einstellung | |
|---|---|
| OpenProject URL | z. B. `https://pm.example.com` |
| OpenProject API-Schlüssel | API-Token eines Benutzers mit Schreibrechten (*Mein Konto > Zugangstokens*) |
| Projekt | Projekt für neue Arbeitspakete: Kennung (`FER_27`) oder einfach den Link zum Projekt einfügen. Zur neuen Saison umstellen. |

Alles andere hat brauchbare Standardwerte:

| Einstellung | Standard | Bedeutung |
|---|---|---|
| Arbeitspakete automatisch anlegen | an | jeder neue offene Auftrag bekommt ein Arbeitspaket im eingestellten Projekt |
| Typ für Fertigungsaufträge / Bestellungen | `Aufgabe` / `Bestellung` | Typ-**Name**; fehlt er im Projekt, wird der erste Typ genommen |
| Status-Zuordnung | `PENDING=Neu; PRODUCTION=In Bearbeitung; …` | InvenTree-Status = OpenProject-Status**name**, nicht aufgeführte werden nicht übertragen |
| Fortschritt übertragen | an | Fertig- bzw. Liefermenge als *% erledigt* |
| Termine aus OpenProject übernehmen | an | Start-/Endtermin in beide Richtungen |
| Verantwortliche übertragen | an | *Verantwortlich* aus InvenTree setzen (siehe unten) |
| Benutzerdefinierte Felder | leer | z. B. `PDM-Nummer=ipn; Lieferant=supplier` (siehe unten) |
| Präfix für Verweise | `OP#` | |

IDs von Projekten, Typen, Status oder Custom Fields braucht man nicht.

## Einrichtung in OpenProject

**Typen:** Im Zielprojekt (*Projekteinstellungen > Arbeitspaket-Typen*) müssen die beiden Typen aktiviert sein, also `Aufgabe` für Fertigungsaufträge und `Bestellung` für Bestellungen (bzw. was in den Plugin-Einstellungen steht).

**Workflow:** Das Plugin setzt die Status aus der Status-Zuordnung direkt, auch in Sprüngen (eine Bestellung kann von *Neu* direkt auf *Abgeschlossen* gehen). Unter *Administration > Arbeitspakete > Workflow* deshalb für **beide Typen** und die **Rolle des API-Benutzers** im Zielprojekt alle Übergänge zwischen den verwendeten Status erlauben:

| von \ nach | Neu | In Bearbeitung | Abgeschlossen |
|---|---|---|---|
| Neu | | ✓ | ✓ |
| In Bearbeitung | ✓ | | ✓ |
| Abgeschlossen | ✓ | ✓ | |

Rückwege braucht es, weil Aufträge in InvenTree wieder geöffnet werden können. Ist ein Übergang nicht erlaubt, speichert das Plugin alles andere trotzdem und zeigt im Panel einen Hinweis.

**% erledigt:** Wird nur gesetzt, wenn die Fortschrittsberechnung arbeitsbasiert ist (*Administration > Arbeitspakete > Fortschrittsberechnung*). Bei statusbasierter Berechnung leitet OpenProject den Wert aus dem Status ab; dann *Fortschritt übertragen* ausschalten.

**Custom Fields:** Keine sind nötig. Referenz, Teil, Menge, Lieferant, Verfahren, Maschine und Link zu InvenTree stehen schon im Titel und im InvenTree-Abschnitt der Beschreibung. Zum Filtern, Gruppieren und für Boards lohnen sich diese, jeweils für die Typen und das Zielprojekt aktiviert:

| Feld (Format) | Zuordnung | |
|---|---|---|
| `Fertigungsverfahren` (Liste) | `process` | Werte wie im InvenTree-Parameter: Drehen, Fräsen, Eloxieren, … |
| `Maschine` (Liste) | `machine` | Werte = Namen der Maschinen (bzw. der Sätze in Manufacturing Costs) |
| `Fertigung` (Liste: Eigenfertigung, Fremdfertigung) | `manufacturing` | zum Filtern auf Eigenfertigung |
| `PDM-Nummer` (Text) | `ipn` | existiert schon, nur für die Typen aktivieren |
| `Lieferant` (Text) | `supplier` | Bestellungen und externe Fertigungsaufträge (Lieferant der zugehörigen Bestellungen) |
| `Menge` / `Gefertigt` (Ganzzahl) | `quantity` / `completed` | Fertigungsaufträge |

Dann in den Plugin-Einstellungen unter *Benutzerdefinierte Felder*:

```
Fertigungsverfahren=process; Maschine=machine; Fertigung=manufacturing; PDM-Nummer=ipn; Lieferant=supplier; Menge=quantity; Gefertigt=completed
```

Weitere mögliche Werte: `reference`, `url`, `status`, `progress`, `order_type`, `part`, `work`, `supplier_reference`. Unterstützt werden Text, Langer Text, Ganzzahl, Gleitkommazahl, Boolean, Datum und Listen (auch Mehrfachauswahl, Werte durch Komma getrennt). Bei Listen wird der Eintrag mit gleichem Namen gewählt. Fehlt ein Wert in der Liste, oder ist ein Feld für einen Typ nicht aktiviert, wird das Feld übersprungen und im Panel als Hinweis angezeigt.

## Eigenfertigung: Verfahren, Maschine, Aufwand

Gedacht für eine Stückliste mit Überbaugruppen, in der zusätzlich jede Fertigungsstufe eines Teils ein eigenes Teil ist, z. B. *Akku* ← *SUS Part 1 – anodized* ← *milled* ← *turned*. Jede Stufe wird ein eigener Fertigungsauftrag (InvenTree legt Unteraufträge auf Wunsch automatisch an) und damit ein eigenes Arbeitspaket. Wie die Arbeitspakete zusammenhängen, steht unter [Struktur](#struktur-baugruppen-und-stufen).

| | Quelle in InvenTree |
|---|---|
| **Fertigungsverfahren** (ein Wert) | Parameter `Fertigungsverfahren` am Teil, denn das Zwischenteil *ist* der Schritt (turned = Drehen). Ein Parameter am Fertigungsauftrag hat Vorrang. |
| **Arbeitsschritte** | Jeder Manufacturing-Costs-Eintrag des Teils ist ein Schritt (Vorbereitung, Drehen, Standzeit, …). Alle Schritte stehen als Tabelle mit Zeit pro Stück und gesamt in der Beschreibung des Arbeitspakets. |
| **Maschine** (ein Wert) | Parameter `Maschine` am **Fertigungsauftrag** (nicht am Teil, die Maschine gehört zur Ausführung). Ohne Parameter wird die Hauptmaschine aus den Manufacturing Costs vorgeschlagen: der Satz aus *Maschinen-Sätze* mit dem größten Zeitanteil. Ändert jemand in OpenProject das Feld `Maschine`, z. B. durch Verschieben der Karte im Board, schreibt das Plugin den Wert in den Parameter des Auftrags zurück. Dafür muss die Parametervorlage `Maschine` in InvenTree existieren, am besten mit denselben Auswahlwerten wie die Liste in OpenProject. |
| **Aufwand** → *Arbeit* in OpenProject | Summe der Schritte mit Zeiteinheit (Einheit des Satzes z. B. `hour`), geteilt durch die Amortisierung, mal Auftragsmenge. Sätze in *Sätze ohne Personalaufwand* (z. B. `Standzeit`, wenn die Maschine allein läuft) zählen nicht mit, stehen aber in der Tabelle. Beispiel: 2 h Vorbereitung amortisiert über 10 Teile + 30 min Drehen pro Teil, Auftrag über 10 Stück → (0,2 + 0,5) × 10 = 7 h. |
| **Eigen-/Fremdfertigung** | Häkchen *Extern* am Fertigungsauftrag. Für Fremdfertigung kann ein eigener Arbeitspaket-Typ eingestellt werden (*Typ für externe Fertigungsaufträge*). |

Die Parameternamen sind einstellbar. Ohne Manufacturing-Costs-Plugin bleiben Schritte, Aufwand und (ohne Parameter) Maschine einfach leer.

**Planung in OpenProject:** Das Plugin fasst Zuweisung, Version/Sprint und Board-Position nie an. Status, Titel und Fortschritt sendet es nur, wenn sie sich in InvenTree ändern. Termine, die ihr in OpenProject verschiebt, werden nach InvenTree übernommen. Ein Board pro Maschine lässt sich als Basis-Board mit einer Spalte je Filter `Maschine = …` und `Fertigung = Eigenfertigung` bauen. Für Sprints reicht der Backlog des Projekts mit demselben Filter, und *Arbeit* zeigt die Auslastung.

## Wo Arbeitspakete landen

- **Projekt:** im eingestellten Projekt (z. B. `FER_27`). Im Panel *OpenProject* lässt sich pro Auftrag ein anderes Projekt aus einer Liste wählen, beim Anlegen oder nachträglich über „Verschieben“. Für die manuelle Wahl vor dem Anlegen *Arbeitspakete automatisch anlegen* ausschalten.
- **Struktur:** siehe [unten](#struktur-baugruppen-und-stufen).
- **Relationen:** Ein `OP#EL-5` irgendwo im Auftrag (Titel, Beschreibung, Notizen, Link, bei Bestellungen auch Positionsnotizen) wird als Relation *bezieht sich auf* zu EL-5 angelegt. Eine Bestellung für einen externen Fertigungsauftrag bekommt die Relation *geht voraus* zu dessen Arbeitspaket.
- **Bestehendes Arbeitspaket:** Den Link zum Arbeitspaket ins *Link*-Feld des Auftrags kopieren oder im Panel auf „Verknüpfen“ klicken. Dann wird dieses Arbeitspaket synchronisiert, sein Titel bleibt erhalten.

## Was synchronisiert wird

| | InvenTree → OpenProject | OpenProject → InvenTree |
|---|---|---|
| Titel | bei Änderung in InvenTree | – |
| Beschreibung | eigener Abschnitt `###### InvenTree` am Ende, Text darüber bleibt unangetastet | – |
| Status | laut Status-Zuordnung | – |
| % erledigt | fertige/bestellte Menge | – |
| Start-/Endtermin | ✓ | ✓ |
| Custom Fields | laut Zuordnung | – |
| Verantwortlich | Benutzer oder Gruppe aus InvenTree | – |

Für jedes Feld speichert das Plugin den Wert, auf den sich beide Seiten beim letzten Sync geeinigt haben. Daran sieht es, welche Seite etwas geändert hat:

- Felder von InvenTree werden nur gesendet, wenn sie sich in InvenTree geändert haben. Wer in OpenProject z. B. den Titel von Hand ändert, behält ihn, bis sich der Auftrag ändert.
- Bei Terminen, die auf beiden Seiten geändert wurden, gewinnt OpenProject.
- Lehnt OpenProject ein Feld ab, etwa *% erledigt* bei statusbasierter Fortschrittsberechnung oder einen Statuswechsel, den der Workflow nicht erlaubt, wird der Rest trotzdem gespeichert. Der Hinweis erscheint im Panel.

**Verantwortlich:** Ein InvenTree-Benutzer wird über die E-Mail-Adresse zugeordnet, ersatzweise über den Login (gleich E-Mail oder Benutzername) und zuletzt über den vollen Namen; eine InvenTree-Gruppe über den Gruppennamen. Gesucht wird nur unter den Mitgliedern des Projekts, in dem das Arbeitspaket liegt. E-Mail-Adressen liefert OpenProject nur, wenn der API-Benutzer Administrator ist oder die Personen ihre Adresse nicht verbergen. Ohne Treffer bleibt das Feld unverändert und das Panel zeigt einen Hinweis. *Zugewiesen an* fasst das Plugin nicht an, das wird in OpenProject geplant.

**Wann:** Sofort beim Speichern eines Auftrags oder einer Bestellposition. Änderungen in OpenProject holt alle 5 Minuten eine Abfrage ab, einen Webhook braucht es nicht. Dabei fragt das Plugin nur die verknüpften Arbeitspakete ab und gleicht nur die geänderten ab.

## Struktur: Baugruppen und Stufen

Das Plugin unterscheidet zwei Arten von Unteraufträgen, und zwar am **Fertigungsverfahren des übergeordneten Teils**:

- **Übergeordnetes Teil ohne Fertigungsverfahren = Baugruppe** (Akku, Modul): Der Unterauftrag wird **Kind** ihres Arbeitspakets.
- **Übergeordnetes Teil mit Fertigungsverfahren = Fertigungsstufe** (anodized = Eloxieren): Der Unterauftrag ist die vorherige Stufe und **geht voraus**. Er hängt unter derselben Baugruppe wie die Stufe.

```
Akku                                  Baugruppe → Elternelement
 ├─ SUS Part 1 – anodized (Eloxieren)  Kind von Akku
 ├─ SUS Part 1 – milled (Fräsen)       Kind von Akku, geht voraus → anodized
 ├─ SUS Part 1 – turned (Drehen)       Kind von Akku, geht voraus → milled
 └─ Modul                              Kind von Akku
     └─ Zelle                          Kind von Modul
```

Alle Stufen eines Teils sind dadurch Geschwister unter der nächsten Baugruppe. OpenProject erlaubt *geht voraus* zwischen Geschwistern, aber nicht zwischen Eltern und Kind. Bestellungen für externe Fertigung hängen unter der Baugruppe ihres Fertigungsauftrags und gehen ihm voraus.

**Boards und Sprints:** Ein Board pro Überthema ist ein Basis-Board mit Filter *Elternelement = Akku* oder ein Eltern-Kind-Board mit einer Spalte pro Baugruppe. Maschinen-Boards (Filter *Maschine*) und Sprints laufen themenübergreifend. Sprints sagen, *wann* etwas gefertigt wird, die Baugruppen, *was* zusammengehört.

**Voraussetzungen:**
- Zwischenstufen brauchen den Parameter *Fertigungsverfahren*. Ohne ihn gilt ein Teil als Baugruppe, seine Vorstufe wird dann Kind statt Vorgänger.
- Die Hierarchie entsteht aus dem Auftragsbaum. Einzelne Fertigungsaufträge ohne übergeordneten Auftrag bekommen kein Elternelement.
- Wird eine Baugruppe erst nach ihren Unteraufträgen synchronisiert, hängt das Plugin die vorhandenen Arbeitspakete nachträglich darunter. Ein in OpenProject von Hand geändertes Elternelement bleibt, bis sich die Struktur in InvenTree ändert.

## OP#-Verweise als Links

- **Notizen** (alle Modelle mit Notizfeld außer Lagerartikeln): Beim Speichern wird `OP#EL-5` zu `[OP#EL-5](https://…/work_packages/EL-5)`. Der Link ist danach überall sichtbar, auch in Exporten und Berichten.
- **Alle anderen Texte** (Titel, Beschreibungen, Tabellen, …): Werden unterstrichen und sind anklickbar, sobald das Plugin-Frontend einmal geladen wurde. Das passiert beim Öffnen des Panels *OpenProject* oder wenn das Dashboard-Widget *OpenProject Sync* auf der Startseite liegt. Am zuverlässigsten ist es, das Widget einmal zum Dashboard hinzuzufügen.
- Seiten, die `OP#`-Verweise enthalten, bekommen ein Panel *OpenProject* mit Titel und Status der verlinkten Arbeitspakete.

`OP#EL-05` und `OP#el-5` sind dasselbe wie `EL-5`.

## Umstieg vom alten Single-File-Plugin

1. `plugin-alt.py` aus dem InvenTree-Plugin-Ordner entfernen (beide Plugins haben denselben Slug).
2. Dieses Plugin installieren. URL und API-Schlüssel sollten übernommen werden, weil die Einstellungen gleich heißen. Kurz prüfen.
3. Im Dashboard-Widget einmal „Alle offenen Aufträge synchronisieren“ klicken. Bestehende Verknüpfungen werden übernommen, die alte generierte Beschreibung wird durch den neuen Abschnitt ersetzt.

Bewusst nicht mehr dabei: Sync von Kommentaren und Anhängen, Benutzerzuweisung, Statusfreigabe aus OpenProject und die Heuristiken zu Maschine und Fertigungsverfahren. Gerade diese Teile haben die alte Version anfällig gemacht.

## Entwicklung

Das Gerüst stammt aus dem [InvenTree Plugin Creator](https://github.com/inventree/plugin-creator).

```
openproject_sync/
  core.py     Plugin-Klasse: Einstellungen, Events, Zeitplan, UI-Registrierung
  sync.py     Sync-Logik
  orders.py   Adapter für Fertigungsauftrag / Bestellung
  client.py   OpenProject API v3
  refs.py     OP#-Verweise erkennen und verlinken
  views.py    API für Panel und Dashboard
frontend/src/
  Panel.tsx, Dashboard.tsx, linkify.ts
```

```bash
python -m unittest discover -s tests   # Tests ohne InvenTree
cd frontend && npm install && npm run build   # schreibt nach openproject_sync/static (wird mit eingecheckt)
```
