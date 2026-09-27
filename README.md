# Privates Fotosystem auf spark-2511

Projekt: `/home/jee/docker/photo-system`. Immich v3.2.2 verwaltet die Bibliothek,
erzeugt lokale ML-Bildindizes und bietet die eingebaute Duplikatprüfung. Die
zusätzliche Qualitätsoberfläche analysiert die Dateien unter `photos/` lokal und
kann bis zu drei ähnliche Bilder bei Ollama `qwen3.8:27b` vergleichen lassen.

## Start und Zugang

```sh
cd /home/jee/docker/photo-system
docker compose up -d --build
docker compose ps
```

Beide Weboberflächen binden an die LAN-Adresse der Spark:
Immich `http://192.168.1.224:2283`, Fotoqualität `http://192.168.1.224:18788`.
Der LAN-Zugriff erfolgt über HTTP ohne TLS und ist für ein vertrauenswürdiges
lokales Netz vorgesehen. Immich fragt beim
ersten Start nach einem Admin-Konto. Die Fotoqualität fragt nach Benutzer
`sysop` und dem Passwort in der lokalen `.env` (Zeile `QUALITY_PASSWORD`).
Die `.env` ist nur für den Besitzer lesbar und enthält auch das DB-Passwort.

## Fotos einbinden

Fotos in `photos/` ablegen. In Immich nach dem ersten Login unter
*Administration → External Libraries* eine Bibliothek für den eigenen Benutzer
mit dem Importpfad `/external/photos` anlegen und scannen. Der Container hat
auf diesen Ordner nur Lesezugriff. Änderungen durch die Qualitätsoberfläche
werden nach erneutem Bibliotheks-Scan in Immich sichtbar.

In Immich unter *Utilities → Review duplicates* die ähnlichen Bilder prüfen.
Die ML-Komponente verwendet das verfügbare ARM64-Image; das CUDA-Image von
Immich v3.2.2 hat kein ARM64-Manifest. Die separate Bildbeurteilung verwendet
Ollama und das vorhandene `qwen3.8:27b` auf der Spark.

## Qualitätsprüfung

In der Qualitätsoberfläche auf **Ordner prüfen** klicken. SHA-256 erkennt
identische Dateien; ein perzeptueller Hash schlägt visuell ähnliche Bilder vor.
Schärfe, Helligkeit und Auflösung sind Vergleichshilfen, keine sichere
Entscheidung über den Wert eines Fotos. **In Quarantäne** verschiebt nur das
gewählte Original in `quarantine/`; über die Quarantäneseite lässt es sich
wiederherstellen. Es gibt keine automatische endgültige Löschung.

Pillow kann HEIC/HEIF ohne zusätzliche Erweiterung nicht dekodieren; solche
Dateien erscheinen aktuell als Scanfehler. Videos und RAW-Dateien sind nicht
Teil der zusätzlichen Qualitätsprüfung, können aber je nach Immich-Unterstützung
in der Fotobibliothek angezeigt werden.

## Betrieb

Originale in `photos/`, Immich-Uploads in `library/`, PostgreSQL in `postgres/`,
Qualitätsdaten in `state/` und Quarantäne in `quarantine/` sichern. Ein Backup
auf demselben NVMe ist kein Schutz gegen Geräteausfall. Der erste ML-Auftrag
kann Modelle aus dem Internet herunterladen; Fotodateien werden dafür nicht an
einen externen Dienst gesendet. Vor späterem vollständigen Offline-Betrieb
Modellcache und Updates separat prüfen.
