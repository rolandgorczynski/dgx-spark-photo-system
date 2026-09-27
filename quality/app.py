import base64
import hashlib
import io
import os
import secrets
import shutil
import sqlite3
import threading
from pathlib import Path

import imagehash
import requests
from flask import Flask, Response, abort, redirect, render_template_string, request, url_for
from PIL import Image, ImageFilter, ImageOps, ImageStat, UnidentifiedImageError

app = Flask(__name__)
ROOT = Path('/photos').resolve()
QUARANTINE = Path('/quarantine').resolve()
DB = Path('/state/quality.sqlite3')
PASSWORD = os.environ['QUALITY_PASSWORD']
CSRF = os.environ['QUALITY_CSRF']
OLLAMA = os.getenv('OLLAMA_URL', 'http://host.docker.internal:11434')
MODEL = os.getenv('VISION_MODEL', 'qwen3-vl:8b')
EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.tif', '.tiff', '.bmp', '.gif', '.heic', '.heif'}
lock = threading.Lock()
status = {'running': False, 'processed': 0, 'total': 0, 'error': ''}


def connection():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE IF NOT EXISTS photos (
        path TEXT PRIMARY KEY, size INTEGER, mtime_ns INTEGER, sha TEXT,
        phash TEXT, width INTEGER, height INTEGER, sharpness REAL,
        brightness REAL, state TEXT NOT NULL DEFAULT 'active')''')
    return db


def authorized():
    auth = request.authorization
    return auth and secrets.compare_digest(auth.username or '', 'sysop') and secrets.compare_digest(auth.password or '', PASSWORD)


@app.before_request
def protect():
    if request.path == '/health':
        return None
    if not authorized():
        return Response('Anmeldung erforderlich', 401, {'WWW-Authenticate': 'Basic realm="Fotoqualitaet"'})
    if request.method == 'POST' and not secrets.compare_digest(request.form.get('csrf', ''), CSRF):
        abort(403)


@app.get('/health')
def health():
    return {'ok': True}


def safe_path(relative):
    path = ROOT / relative
    if path.is_symlink() or not path.resolve().is_relative_to(ROOT) or not path.is_file():
        raise ValueError('Datei nicht mehr im Fotoordner')
    return path


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(part)
    return h.hexdigest()


def measure(path):
    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source)
        w, h = image.size
        small = image.convert('RGB')
        small.thumbnail((512, 512))
        gray = small.convert('L')
        sharpness = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).var[0]
        brightness = ImageStat.Stat(gray).mean[0]
        return str(imagehash.phash(small)), w, h, round(sharpness, 2), round(brightness, 2)


def scan():
    try:
        files = []
        for directory, dirs, names in os.walk(ROOT, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
            files += [Path(directory) / name for name in names if Path(name).suffix.lower() in EXTS]
        status['total'] = len(files)
        with connection() as db:
            for path in files:
                status['processed'] += 1
                try:
                    relative = str(path.relative_to(ROOT))
                    p = safe_path(relative)
                    stat = p.stat()
                    old = db.execute('SELECT size,mtime_ns,state FROM photos WHERE path=?', (relative,)).fetchone()
                    if old and old['size'] == stat.st_size and old['mtime_ns'] == stat.st_mtime_ns and old['state'] == 'active':
                        continue
                    sha = digest(p)
                    phash, width, height, sharp, bright = measure(p)
                    db.execute('''INSERT INTO photos(path,size,mtime_ns,sha,phash,width,height,sharpness,brightness,state)
                        VALUES(?,?,?,?,?,?,?,?,?,'active') ON CONFLICT(path) DO UPDATE SET
                        size=excluded.size,mtime_ns=excluded.mtime_ns,sha=excluded.sha,phash=excluded.phash,
                        width=excluded.width,height=excluded.height,sharpness=excluded.sharpness,
                        brightness=excluded.brightness,state='active' ''',
                        (relative, stat.st_size, stat.st_mtime_ns, sha, phash, width, height, sharp, bright))
                    db.commit()
                except (OSError, UnidentifiedImageError, ValueError) as exc:
                    status['error'] = f'{path.name}: {exc}'
            current = {str(p.relative_to(ROOT)) for p in files}
            for row in db.execute("SELECT path FROM photos WHERE state='active'").fetchall():
                if row['path'] not in current:
                    db.execute("UPDATE photos SET state='missing' WHERE path=?", (row['path'],))
            db.commit()
    except Exception as exc:
        status['error'] = str(exc)
    finally:
        status['running'] = False
        lock.release()


def groups(rows):
    parents = list(range(len(rows)))
    def find(x):
        while parents[x] != x:
            parents[x] = parents[parents[x]]
            x = parents[x]
        return x
    def union(a, b):
        parents[find(a)] = find(b)
    exact = {}
    for i, row in enumerate(rows):
        if row['sha'] in exact:
            union(i, exact[row['sha']])
        exact[row['sha']] = i
    # BK tree searches every branch consistent with the Hamming metric.
    tree = None
    hashes = [int(row['phash'], 16) for row in rows]
    for i, value in enumerate(hashes):
        if tree is None:
            tree = [i, {}]
            continue
        pending = [tree]
        while pending:
            candidate = pending.pop()
            distance = (value ^ hashes[candidate[0]]).bit_count()
            if distance <= 5:
                union(i, candidate[0])
            pending.extend(child for edge, child in candidate[1].items() if distance - 5 <= edge <= distance + 5)
        node = tree
        while True:
            distance = (value ^ hashes[node[0]]).bit_count()
            if distance not in node[1]:
                node[1][distance] = [i, {}]
                break
            node = node[1][distance]
    result = {}
    for i, row in enumerate(rows):
        result.setdefault(find(i), []).append(row)
    return [group for group in result.values() if len(group) > 1]


HTML = '''<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Fotoqualität</title><style>body{font:16px system-ui;background:#111923;color:#e9f0f7;max-width:1200px;margin:auto;padding:25px}a{color:#8bd7ff}button{background:#286a92;color:white;border:0;padding:9px 13px;border-radius:6px;cursor:pointer}article{background:#1d2b39;padding:16px;border-radius:12px;margin:20px 0}.grid{display:flex;gap:14px;overflow-x:auto}.card{width:210px;flex:none}.card img{width:210px;height:150px;object-fit:contain;background:#0b1119}.meta{font-size:13px;overflow-wrap:anywhere}.warn{color:#ffd888}</style>
<h1>Fotoqualität</h1><p>Fotos: {{ count }} · Scan: {{ status.processed }}/{{ status.total }} {% if status.running %}(läuft){% endif %}</p>
{% if status.error %}<p class="warn">Letzte Meldung: {{ status.error }}</p>{% endif %}
<form action="{{ url_for('start_scan') }}" method="post"><input type="hidden" name="csrf" value="{{ csrf }}"><button>Ordner prüfen</button></form>
<p><a href="{{ url_for('quarantined') }}">Quarantäne ansehen und wiederherstellen</a></p>
<p>Ähnliche Fotos sind Vorschläge. Bildgröße, Schärfe und Belichtung sind Messwerte, keine sichere Qualitätsentscheidung. Verschieben nur nach Auswahl; Dateien bleiben in der Quarantäne erhalten.</p>
{% for group in groups %}<article><h2>Gruppe {{ loop.index }} · {{ group|length }} Bilder</h2><div class="grid">
{% for photo in group %}<div class="card"><img loading="lazy" src="{{ url_for('thumb', path=photo.path) }}"><div class="meta"><b>{{ photo.path }}</b><br>{{ photo.width }} × {{ photo.height }} · {{ photo.size }} Bytes<br>Schärfe {{ photo.sharpness }} · Helligkeit {{ photo.brightness }}<br>{% if photo.sha == group[0].sha %}identischer Dateiinhalt{% else %}visuell ähnlich{% endif %}</div>
<form action="{{ url_for('quarantine') }}" method="post"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="path" value="{{ photo.path }}"><input type="hidden" name="sha" value="{{ photo.sha }}"><button>In Quarantäne</button></form></div>{% endfor %}</div>
<form action="{{ url_for('vision') }}" method="post"><input type="hidden" name="csrf" value="{{ csrf }}">{% for photo in group[:3] %}<input type="hidden" name="path" value="{{ photo.path }}">{% endfor %}<button>Lokales Vision-Modell fragen</button></form></article>{% endfor %}
{% if not groups %}<p>Noch keine ähnlichen Bilder gefunden. Lege Fotos unter <code>photos/</code> ab und starte den Scan.</p>{% endif %}</html>'''


@app.get('/')
def index():
    with connection() as db:
        rows = db.execute("SELECT * FROM photos WHERE state='active' AND phash IS NOT NULL ORDER BY path").fetchall()
    matches = sorted(groups(rows), key=lambda group: -len(group))[:100]
    return render_template_string(HTML, count=len(rows), groups=matches, csrf=CSRF, status=status)


@app.post('/scan')
def start_scan():
    if lock.acquire(blocking=False):
        status.update(running=True, processed=0, total=0, error='')
        threading.Thread(target=scan, daemon=True).start()
    return redirect(url_for('index'))


@app.get('/thumb/<path:path>')
def thumb(path):
    with connection() as db:
        row = db.execute("SELECT path FROM photos WHERE path=? AND state='active'", (path,)).fetchone()
    if not row:
        abort(404)
    try:
        with Image.open(safe_path(path)) as source:
            image = ImageOps.exif_transpose(source).convert('RGB')
            image.thumbnail((480, 360))
            out = io.BytesIO()
            image.save(out, 'JPEG', quality=75)
        return Response(out.getvalue(), mimetype='image/jpeg')
    except (OSError, ValueError):
        abort(404)


@app.post('/quarantine')
def quarantine():
    relative = request.form.get('path', '')
    with connection() as db:
        row = db.execute("SELECT * FROM photos WHERE path=? AND state='active'", (relative,)).fetchone()
        if not row or row['sha'] != request.form.get('sha'):
            abort(409)
        try:
            source = safe_path(relative)
            if digest(source) != row['sha']:
                abort(409)
            target = QUARANTINE / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                target = target.with_name(target.stem + '-' + row['sha'][:12] + target.suffix)
            shutil.move(str(source), str(target))
            db.execute("UPDATE photos SET state=? WHERE path=?", ('quarantine:' + str(target.relative_to(QUARANTINE)), relative))
            db.commit()
        except (OSError, ValueError):
            abort(409)
    return redirect(url_for('index'))


@app.get('/quarantined')
def quarantined():
    with connection() as db:
        rows = db.execute("SELECT path,state FROM photos WHERE state LIKE 'quarantine:%' ORDER BY path").fetchall()
    parts = ['<meta charset="utf-8"><h1>Quarantäne</h1><a href="/">Zurück</a><ul>']
    from markupsafe import escape
    for row in rows:
        parts.append(f'<li>{escape(row["path"])} <form method="post" action="/restore"><input type="hidden" name="csrf" value="{CSRF}"><input type="hidden" name="path" value="{escape(row["path"])}"><button>Wiederherstellen</button></form></li>')
    return ''.join(parts) + '</ul>'


@app.post('/restore')
def restore():
    relative = request.form.get('path', '')
    with connection() as db:
        row = db.execute("SELECT state FROM photos WHERE path=?", (relative,)).fetchone()
        if not row or not row['state'].startswith('quarantine:'):
            abort(404)
        source = (QUARANTINE / row['state'][11:]).resolve()
        target = (ROOT / relative).resolve()
        if not source.is_relative_to(QUARANTINE) or not target.is_relative_to(ROOT) or target.exists() or not source.is_file():
            abort(409)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(target))
        db.execute("UPDATE photos SET state='active' WHERE path=?", (relative,))
        db.commit()
    return redirect(url_for('quarantined'))


@app.post('/vision')
def vision():
    paths = request.form.getlist('path')[:3]
    encoded = []
    try:
        for relative in paths:
            with Image.open(safe_path(relative)) as source:
                image = ImageOps.exif_transpose(source).convert('RGB')
                image.thumbnail((900, 900))
                out = io.BytesIO()
                image.save(out, 'JPEG', quality=82)
                encoded.append(base64.b64encode(out.getvalue()).decode())
        response = requests.post(f'{OLLAMA}/api/generate', json={
            'model': MODEL, 'stream': False, 'images': encoded,
            'options': {'num_ctx': 4096}, 'keep_alive': 0,
            'prompt': 'Vergleiche diese Bilder in ihrer Reihenfolge. Sind sie dieselbe Szene? Beurteile nur sichtbare Schärfe, Belichtung und Bildinhalt. Nenne Unsicherheiten. Empfiehl höchstens eine Datei zur manuellen Prüfung, keine automatische Löschung.'
        }, timeout=180)
        response.raise_for_status()
        answer = response.json().get('response', 'Keine Antwort')
    except (OSError, ValueError, requests.RequestException) as exc:
        answer = f'Lokales Modell nicht erreichbar: {exc}'
    from markupsafe import escape
    return f'<meta charset="utf-8"><pre style="white-space:pre-wrap;font:16px system-ui">{escape(answer)}</pre><a href="/">Zurück</a>'
