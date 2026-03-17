from fastapi import FastAPI, Form, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3, os, shutil, uuid, json
from pathlib import Path
from datetime import datetime, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape
from urllib.request import Request, urlopen
from urllib.error import HTTPError

app = FastAPI()

# --- Config ---
DB_PATH = os.getenv("BOTC_DB_PATH", "data/botc.db")
IMG_DIR = os.getenv("BOTC_IMG_DIR", "data/images")
Path(IMG_DIR).mkdir(parents=True, exist_ok=True)

cors_origins_env = os.getenv("BOTC_CORS_ORIGINS", "*").strip()
if cors_origins_env == "*":
    allow_origins = ["*"]
else:
    allow_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def db_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def telegram_send_publication(message: str, image_url: str | None = None):
    enabled = os.getenv("BOTC_TELEGRAM_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return {"enabled": False, "sent": False}

    token = os.getenv("BOTC_TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("BOTC_TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return {"enabled": True, "sent": False, "error": "Missing BOTC_TELEGRAM_BOT_TOKEN or BOTC_TELEGRAM_CHAT_ID"}

    base = f"https://api.telegram.org/bot{token}"

    def _post(endpoint: str, payload: dict):
        req = Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req, timeout=12) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if not body.get("ok"):
                raise RuntimeError(str(body))
        return body

    message_payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    # Si la imatge és LAN/local, Telegram no la podrà descarregar: enviem text directament
    if image_url and ("192.168." in image_url or "localhost" in image_url or image_url.startswith("/")):
        image_url = None

    if image_url:
        try:
            photo_payload = {
                "chat_id": chat_id,
                "photo": image_url,
                "caption": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }
            _post(f"{base}/sendPhoto", photo_payload)
            return {"enabled": True, "sent": True, "mode": "photo"}
        except Exception as e:
            # fallback a text si sendPhoto falla (URL no pública, etc.)
            try:
                _post(f"{base}/sendMessage", message_payload)
                return {"enabled": True, "sent": True, "mode": "text_fallback", "photo_error": str(e)}
            except Exception as e2:
                return {"enabled": True, "sent": False, "error": str(e2), "photo_error": str(e)}

    try:
        _post(f"{base}/sendMessage", message_payload)
        return {"enabled": True, "sent": True, "mode": "text"}
    except HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            body = ""
        return {"enabled": True, "sent": False, "error": f"HTTP {e.code}", "details": body}
    except Exception as e:
        return {"enabled": True, "sent": False, "error": str(e)}


# --- Init DB ---
with db_conn() as conn:
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY,
        name VARCHAR NOT NULL,
        date VARCHAR,
        location VARCHAR,
        image_url VARCHAR,
        group_id INTEGER,
        min_participants INTEGER,
        max_participants INTEGER,
        status VARCHAR,
        codeEvt VARCHAR
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS groups (
        id INTEGER PRIMARY KEY,
        name VARCHAR NOT NULL,
        codeUsr VARCHAR,
        codeAdm VARCHAR
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS participants (
        id INTEGER PRIMARY KEY,
        name VARCHAR NOT NULL,
        group_id INTEGER,
        event_id INTEGER NOT NULL,
        FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE,
        FOREIGN KEY(group_id) REFERENCES groups(id) ON DELETE SET NULL
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS admin_config (
        id INTEGER PRIMARY KEY,
        admin_password VARCHAR NOT NULL
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS event_publications (
        id INTEGER PRIMARY KEY,
        event_id INTEGER NOT NULL,
        title VARCHAR NOT NULL,
        event_date VARCHAR,
        location VARCHAR,
        participant_count INTEGER DEFAULT 0,
        status VARCHAR,
        optional_text VARCHAR,
        author_role VARCHAR,
        created_at VARCHAR DEFAULT CURRENT_TIMESTAMP,
        guid VARCHAR NOT NULL UNIQUE,
        FOREIGN KEY(event_id) REFERENCES events(id) ON DELETE CASCADE
    )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_events_group_id ON events(group_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_participants_event_id ON participants(event_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_participants_group_id ON participants(group_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_events_codeEvt ON events(codeEvt)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_event_publications_event_id ON event_publications(event_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_event_publications_created_at ON event_publications(created_at)")

    # Migracions suaus
    c.execute("PRAGMA table_info(event_publications)")
    publication_cols = {row[1] for row in c.fetchall()}
    if "image_url" not in publication_cols:
        c.execute("ALTER TABLE event_publications ADD COLUMN image_url VARCHAR")

    conn.commit()

# --- MODELS ---
class Event(BaseModel):
    name: str
    date: str | None = None
    location: str | None = None
    group_id: int | None = None
    min_participants: int | None = None
    max_participants: int | None = None
    status: str | None = "active"
    codeEvt: str | None = None


# --- ROUTES ---
# --- EVENTS ---
@app.get("/events")
async def get_events():
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT e.*, 
            (SELECT COUNT(*) FROM participants p WHERE p.event_id = e.id) AS participant_count
            FROM events e
        """)
        cols = [d[0] for d in c.description]
        rows = [dict(zip(cols, r)) for r in c.fetchall()]
        return rows


@app.get("/events/{event_id}")
async def get_event(event_id: int):
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT e.*, 
                   (SELECT COUNT(*) FROM participants p WHERE p.event_id = e.id) AS participant_count
            FROM events e
            WHERE e.id = ?
        """, (event_id,))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Event not found")
        cols = [d[0] for d in c.description]
        return dict(zip(cols, row))


@app.post("/events")
async def create_event(
    name: str = Form(...),
    date: str = Form(None),
    location: str = Form(None),
    group_id: int = Form(None),
    min_participants: int = Form(None),
    max_participants: int = Form(None),
    status: str = Form("active"),
    image: UploadFile = File(None),
    codeEvt: str = Form(None)
):
    # només name és obligatori
    image_url = None
    if image and image.filename:
        safe_name = Path(image.filename).name
        ext = Path(safe_name).suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            raise HTTPException(status_code=400, detail="Unsupported image format")
        stored_name = f"{uuid.uuid4().hex}{ext}"
        file_path = os.path.join(IMG_DIR, stored_name)
        with open(file_path, "wb") as f:
            shutil.copyfileobj(image.file, f)
        image_url = f"/images/{stored_name}"

    with db_conn() as conn:
        c = conn.cursor()
        c.execute("""
            INSERT INTO events (name, date, location, image_url, group_id, min_participants, max_participants, status, codeEvt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (name, date, location, image_url, group_id, min_participants, max_participants, status, codeEvt))
        conn.commit()
    return {"status": "success"}


@app.put("/events/{event_id}")
async def update_event(
    event_id: int,
    name: str = Form(...),
    date: str = Form(None),
    location: str = Form(None),
    group_id: int = Form(None),
    min_participants: int = Form(None),
    max_participants: int = Form(None),
    status: str = Form("active"),
    codeEvt: str = Form(None),
    image: UploadFile = File(None)    
):
    image_url = None
    if image and image.filename:
        safe_name = Path(image.filename).name
        ext = Path(safe_name).suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            raise HTTPException(status_code=400, detail="Unsupported image format")
        stored_name = f"{uuid.uuid4().hex}{ext}"
        file_path = os.path.join(IMG_DIR, stored_name)
        with open(file_path, "wb") as f:
            shutil.copyfileobj(image.file, f)
        image_url = f"/images/{stored_name}"

    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM events WHERE id=?", (event_id,))
        if not c.fetchone():
            raise HTTPException(status_code=404, detail="Event not found")
        if image_url:
            c.execute("""
                UPDATE events SET name=?, date=?, location=?, image_url=?, group_id=?, 
                min_participants=?, max_participants=?, status=?, codeEvt=? WHERE id=?
            """, (name, date, location, image_url, group_id, min_participants, max_participants, status, codeEvt, event_id))
        else:
            c.execute("""
                UPDATE events SET name=?, date=?, location=?, group_id=?, 
                min_participants=?, max_participants=?, status=?, codeEvt=? WHERE id=?
            """, (name, date, location, group_id, min_participants, max_participants, status, codeEvt, event_id))
        conn.commit()
    return {"status": "updated"}


@app.delete("/events/{event_id}")
async def delete_event(event_id: int):
    with db_conn() as conn:
        c = conn.cursor()
        # Esborra participants associats
        c.execute("DELETE FROM participants WHERE event_id=?", (event_id,))
        c.execute("DELETE FROM events WHERE id=?", (event_id,))
        conn.commit()
    return {"status": "deleted"}


# --- GROUPS ---
@app.get("/groups")
@app.get("/api/groups")
async def get_groups():
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM groups")
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, r)) for r in c.fetchall()]

@app.post("/groups")
@app.post("/api/groups")
async def create_group(name: str = Form(...), codeUsr: str = Form(None), codeAdm: str = Form(None)):
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO groups (name, codeUsr, codeAdm) VALUES (?, ?, ?)", (name, codeUsr, codeAdm))
        conn.commit()
    return {"status": "created"}

@app.get("/groups/{group_id}")
async def get_group(group_id: int):
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT * FROM groups WHERE id=?", (group_id,))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Group not found")
        return dict(row)

@app.delete("/groups/{group_id}")
@app.delete("/api/groups/{group_id}")
async def delete_group(group_id: int):
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM groups WHERE id=?", (group_id,))
        conn.commit()
    return {"status": "deleted"}


# --- PARTICIPANTS ---
@app.get("/participants/{event_id}")
async def get_participants(event_id: int):
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT p.*, g.name as group_name
            FROM participants p
            LEFT JOIN groups g ON p.group_id = g.id
            WHERE event_id=?
        """, (event_id,))
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, r)) for r in c.fetchall()]


@app.post("/participants")
async def add_participant(
    name: str = Form(...),
    event_id: int = Form(...),
    group_id: int = Form(None)
):
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO participants (name, event_id, group_id) VALUES (?, ?, ?)", (name, event_id, group_id))
        conn.commit()
    return {"status": "added"}


@app.delete("/participants/{participant_id}")
async def delete_participant(participant_id: int):
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM participants WHERE id=?", (participant_id,))
        conn.commit()
    return {"status": "deleted"}

# --- ACCESS ---
@app.post("/access/validate")
async def validate_access(code: str = Form(...)):
    with db_conn() as conn:
        c = conn.cursor()
        #Event
        c.execute(""" SELECT e.id, e.group_id, g.name as group_name FROM events e
                      LEFT JOIN groups g ON e.group_id = g.id  
                      WHERE codeEvt=?
                  """, (code,))
        for e in c.fetchall():
            event_id, group_id, group_name = e
            return {"role": "user", "group": group_name, "group_id": group_id, "event_id": event_id}

        # Grups
        c.execute("SELECT id, name, codeUsr, codeAdm FROM groups")
        for g in c.fetchall():
            gid, gname, usr, adm = g
            if code == usr:
                return {"role": "user", "group": gname, "group_id": gid}
            elif code == adm:
                return {"role": "admin", "group": gname, "group_id": gid}
        # Superadmin
        c.execute("SELECT admin_password FROM admin_config LIMIT 1")
        row = c.fetchone()
        if row and row[0] == code:
            return {"role": "superadmin", "group": None, "group_id": None}
    raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/access/has_password")
async def has_password():
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM admin_config")
        exists = c.fetchone()[0] > 0
        return {"has_password": exists}


@app.post("/access/set_password")
async def set_admin_password(password: str = Form(...)):
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM admin_config")
        c.execute("INSERT INTO admin_config (admin_password) VALUES (?)", (password,))
        conn.commit()
    return {"status": "ok"}


# --- PUBLICATIONS / RSS ---
@app.post("/events/{event_id}/publish")
async def publish_event(event_id: int, optional_text: str = Form(None), role: str = Form(None)):
    if role not in {"admin", "superadmin"}:
        raise HTTPException(status_code=403, detail="Only admin/superadmin can publish")

    with db_conn() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT e.id, e.name, e.date, e.location, e.status, e.image_url,
                   (SELECT COUNT(*) FROM participants p WHERE p.event_id = e.id) AS participant_count
            FROM events e
            WHERE e.id = ?
        """, (event_id,))
        row = c.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Event not found")

        publication_id = uuid.uuid4().hex
        guid = f"event-{event_id}-pub-{publication_id}"
        c.execute("""
            INSERT INTO event_publications (
                event_id, title, event_date, location, participant_count, status,
                optional_text, author_role, guid, image_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row["id"], row["name"], row["date"], row["location"], row["participant_count"], row["status"],
            optional_text, role, guid, row["image_url"]
        ))
        conn.commit()

    web_base = os.getenv("BOTC_PUBLIC_BASE_URL", "").rstrip("/")
    api_base = os.getenv("BOTC_PUBLIC_API_BASE_URL", "").rstrip("/")
    event_link = f"{web_base}/events.html?event_id={row['id']}" if web_base else f"/events.html?event_id={row['id']}"

    msg_lines = [
        f"<b>🎭 [PUBLICACIÓ] {escape(row['name'])}</b>",
        f"📅 <b>Data:</b> {escape(row['date'] or '-')}",
        f"📍 <b>Lloc:</b> {escape(row['location'] or '-')}",
        f"👥 <b>Participants:</b> {row['participant_count'] or 0}",
        f"🟢 <b>Estat:</b> {escape(row['status'] or 'active')}",
    ]
    if optional_text:
        msg_lines.append(f"💬 <b>Missatge:</b> {escape(optional_text)}")
    msg_lines.append(f"🔗 <a href=\"{escape(event_link)}\">Obrir event a la web</a>")
    telegram_message = "\n".join(msg_lines)

    image_url = row["image_url"]
    if image_url and image_url.startswith("/") and api_base:
        image_url = f"{api_base}{image_url}"

    tg = telegram_send_publication(telegram_message, image_url=image_url)

    return {"status": "published", "guid": guid, "telegram": tg}


@app.get("/publications")
@app.get("/api/publications")
async def list_publications(role: str):
    if role not in {"admin", "superadmin"}:
        raise HTTPException(status_code=403, detail="Only admin/superadmin can list publications")

    with db_conn() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT id, event_id, title, event_date, location, participant_count, status,
                   optional_text, created_at, guid, image_url
            FROM event_publications
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 500
        """)
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, r)) for r in c.fetchall()]


@app.delete("/publications/{publication_id}")
@app.delete("/api/publications/{publication_id}")
async def delete_publication(publication_id: int, role: str):
    if role not in {"admin", "superadmin"}:
        raise HTTPException(status_code=403, detail="Only admin/superadmin can delete publications")

    with db_conn() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM event_publications WHERE id = ?", (publication_id,))
        conn.commit()
    return {"status": "deleted"}


@app.get("/rss/events.xml")
@app.get("/api/rss/events.xml")
async def rss_events():
    with db_conn() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT id, event_id, title, event_date, location, participant_count, status,
                   optional_text, created_at, guid, image_url
            FROM event_publications
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 200
        """)
        rows = c.fetchall()

    base_url = os.getenv("BOTC_PUBLIC_BASE_URL", "").rstrip("/")
    api_base_url = os.getenv("BOTC_PUBLIC_API_BASE_URL", "").rstrip("/")

    items_xml = []
    for row in rows:
        dt = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        pub_date = format_datetime(dt)

        event_path = f"/events.html?event_id={row['event_id']}"
        link = f"{base_url}{event_path}" if base_url else event_path

        image_url = row["image_url"] or ""
        image_base = api_base_url or base_url
        if image_url and image_base and image_url.startswith("/"):
            image_url = f"{image_base}{image_url}"

        status_label = row["status"] or "active"
        optional = f"<p><strong>Missatge:</strong> {escape(row['optional_text'])}</p>" if row["optional_text"] else ""
        image_html = f'<p><img src="{escape(image_url)}" alt="{escape(row["title"])}" style="max-width:100%;border-radius:8px;"/></p>' if image_url else ""
        description_html = (
            f"{image_html}"
            f"<p><strong>Títol:</strong> {escape(row['title'])}</p>"
            f"<p><strong>Data:</strong> {escape(row['event_date'] or '-')}</p>"
            f"<p><strong>Lloc:</strong> {escape(row['location'] or '-')}</p>"
            f"<p><strong>Participants:</strong> {row['participant_count'] or 0}</p>"
            f"<p><strong>Estat:</strong> {escape(status_label)}</p>"
            f"{optional}"
            f'<p><a href="{escape(link)}">Obrir event a la web</a></p>'
        )

        title = f"[PUBLICACIÓ] {row['title']}"

        items_xml.append(
            "<item>"
            f"<title>{escape(title)}</title>"
            f"<description><![CDATA[{description_html}]]></description>"
            f"<link>{escape(link)}</link>"
            f"<guid isPermaLink=\"false\">{escape(row['guid'])}</guid>"
            f"<pubDate>{escape(pub_date)}</pubDate>"
            "</item>"
        )

    channel_link = f"{base_url}/rss/events.xml" if base_url else "/rss/events.xml"
    xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<rss version=\"2.0\"><channel>"
        "<title>BOTC - Publicacions d'events</title>"
        f"<link>{escape(channel_link)}</link>"
        "<description>Publicacions manuals d'events BOTC</description>"
        "<language>ca</language>"
        + "".join(items_xml)
        + "</channel></rss>"
    )
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8")


# --- IMAGES ---
@app.get("/images/{filename}")
async def get_image(filename: str):
    path = os.path.join(IMG_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path)

# --- HEALTH ---
@app.get("/")
def root():
    return {"message": "BOTC backend running"}
