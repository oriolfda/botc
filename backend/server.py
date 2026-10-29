from fastapi import Form, HTTPException

from main import app, db_conn


@app.put("/groups/{group_id}")
@app.put("/api/groups/{group_id}")
async def update_group(
    group_id: int,
    name: str = Form(...),
    codeUsr: str = Form(None),
    codeAdm: str = Form(None),
):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Group name is required")

    with db_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM groups WHERE id=?", (group_id,))
        if not c.fetchone():
            raise HTTPException(status_code=404, detail="Group not found")
        c.execute(
            "UPDATE groups SET name=?, codeUsr=?, codeAdm=? WHERE id=?",
            (name, codeUsr or None, codeAdm or None, group_id),
        )
        conn.commit()

    return {"status": "updated"}
