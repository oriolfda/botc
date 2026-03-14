from fastapi import FastAPI, Form, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3, os, shutil

app = FastAPI()

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],           # Accepta totes
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "data/botc.db"
IMG_DIR = "data/images"
os.makedirs(IMG_DIR, exist_ok=True)

def db_conn():
    return sqlite3.connect(DB_PATH)

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
        event_id INTEGER NOT NULL
    )
    """)
    c.execute("""
    CREATE TABLE IF NOT EXISTS admin_config (
        id INTEGER PRIMARY KEY,
        admin_password VARCHAR NOT NULL
    )
    """)
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
        file_path = os.path.join(IMG_DIR, image.filename)
        with open(file_path, "wb") as f:
            shutil.copyfileobj(image.file, f)
        image_url = f"/images/{image.filename}"

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
        file_path = os.path.join(IMG_DIR, image.filename)
        with open(file_path, "wb") as f:
            shutil.copyfileobj(image.file, f)
        image_url = f"/images/{image.filename}"

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
