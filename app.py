from flask import Flask, render_template, request, redirect
import psycopg2
import psycopg2.extras
from datetime import datetime
import os
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, ".env"))

DATABASE_URL = "postgresql://postgres.tvutnuemsqiinkrwjskv:q66Pr%23r-gMBu%24r%24@aws-1-ap-south-1.pooler.supabase.com:5432/postgres"

app = Flask(__name__)

def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

def create_tables():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS devices (
            id SERIAL PRIMARY KEY,
            name TEXT,
            type TEXT,
            serial TEXT UNIQUE,
            tag_no TEXT,
            config TEXT,
            status TEXT DEFAULT 'Available',
            created_at DATE DEFAULT CURRENT_DATE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS rentals (
            id SERIAL PRIMARY KEY,
            device_id INTEGER REFERENCES devices(id) ON DELETE CASCADE,
            client_name TEXT,
            start_date TEXT,
            due_date TEXT,
            returned INTEGER DEFAULT 0,
            notes TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS maintenance (
            id SERIAL PRIMARY KEY,
            device_id INTEGER REFERENCES devices(id) ON DELETE CASCADE,
            issue TEXT,
            reported_date DATE DEFAULT CURRENT_DATE,
            resolved_date DATE,
            resolved INTEGER DEFAULT 0,
            cost TEXT
        )
    """)

    for col in [
        "ALTER TABLE devices ADD COLUMN IF NOT EXISTS tag_no TEXT",
        "ALTER TABLE devices ADD COLUMN IF NOT EXISTS config TEXT",
        "ALTER TABLE devices ADD COLUMN IF NOT EXISTS created_at DATE DEFAULT CURRENT_DATE",
    ]:
        try:
            cur.execute(col)
        except:
            pass

    conn.commit()
    cur.close()
    conn.close()


def get_home_data(search="", status_filter="", client_filter=""):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    query = """
        SELECT devices.*,
               rentals.client_name,
               rentals.start_date,
               rentals.due_date,
               rentals.notes,
               (
                   SELECT MAX(r2.due_date)
                   FROM rentals r2
                   WHERE r2.device_id = devices.id AND r2.returned = 1
               ) AS last_returned_date
        FROM devices
        LEFT JOIN rentals
        ON devices.id = rentals.device_id AND rentals.returned = 0
        WHERE 1=1
    """
    params = []

    if search:
        for word in search.split():
            query += " AND (devices.name ILIKE %s OR devices.type ILIKE %s OR devices.serial ILIKE %s OR devices.tag_no ILIKE %s OR devices.config ILIKE %s)"
            params.extend([f"%{word}%"] * 5)

    if status_filter:
        query += " AND devices.status = %s"
        params.append(status_filter)

    if client_filter:
        query += " AND rentals.client_name ILIKE %s"
        params.append(f"%{client_filter}%")

    cur.execute(query, params)
    rows = cur.fetchall()

    cur.execute("SELECT id, created_at FROM devices")
    created_map = {r["id"]: r["created_at"] for r in cur.fetchall()}

    cur.close()
    conn.close()

    devices = []
    today = datetime.today().date()
    stats = {"total": 0, "available": 0, "rented": 0, "overdue": 0, "idle": 0, "maintenance": 0}
    IDLE_THRESHOLD = 40

    for r in rows:
        stats["total"] += 1

        if r["status"] == "Available":
            stats["available"] += 1
        elif r["status"] == "Maintenance":
            stats["maintenance"] += 1
        else:
            stats["rented"] += 1

        due = r["due_date"]
        days_left = None
        color = ""
        urgency = 999

        if due:
            due_date = datetime.strptime(due, "%Y-%m-%d").date()
            days_left = (due_date - today).days
            urgency = days_left

            if days_left > 7:
                color = "green"
            elif 2 < days_left <= 7:
                color = "yellow"
            elif 0 <= days_left <= 2:
                color = "orange"
            else:
                color = "red"
                stats["overdue"] += 1

        idle_days = None
        is_idle = False

        if r["status"] == "Available":
            if r["last_returned_date"]:
                last_date = datetime.strptime(r["last_returned_date"], "%Y-%m-%d").date()
                idle_days = (today - last_date).days
            elif created_map.get(r["id"]):
                created = created_map[r["id"]]
                if isinstance(created, str):
                    created = datetime.strptime(created[:10], "%Y-%m-%d").date()
                else:
                    created = created if isinstance(created, type(today)) else today
                idle_days = (today - created).days

            if idle_days is not None and idle_days >= IDLE_THRESHOLD:
                is_idle = True
                stats["idle"] += 1

        devices.append({
            **dict(r),
            "days_left": days_left,
            "color": color,
            "urgency": urgency,
            "idle_days": idle_days,
            "is_idle": is_idle
        })

    devices.sort(key=lambda x: x["urgency"])
    return devices, stats


@app.route("/")
def home():
    search        = request.args.get("search", "").strip()
    status_filter = request.args.get("status", "")
    client_filter = request.args.get("client", "").strip()

    devices, stats = get_home_data(search, status_filter, client_filter)

    return render_template("index.html",
                           devices=devices,
                           stats=stats,
                           search=search,
                           status=status_filter,
                           client=client_filter)


@app.route("/add-device", methods=["POST"])
def add_device():
    name   = request.form["name"].strip()
    type_  = request.form["type"].strip()
    serial = request.form["serial"].strip()
    tag_no = request.form.get("tag_no", "").strip()
    config = request.form.get("config", "").strip()

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT id FROM devices WHERE serial = %s", (serial,))
    if cur.fetchone():
        cur.close()
        conn.close()
        return "A device with this serial number already exists.", 400

    cur.execute(
        "INSERT INTO devices (name, type, serial, tag_no, config, status) VALUES (%s, %s, %s, %s, %s, %s)",
        (name, type_, serial, tag_no, config, "Available")
    )
    conn.commit()
    cur.close()
    conn.close()
    return redirect("/")


@app.route("/assign/<int:id>", methods=["GET", "POST"])
def assign_device(id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT * FROM devices WHERE id = %s", (id,))
    device = cur.fetchone()

    if not device:
        cur.close()
        conn.close()
        return "Device not found", 404

    if device["status"] != "Available":
        cur.close()
        conn.close()
        return f"Device is currently {device['status']} and cannot be assigned.", 400

    if request.method == "POST":
        client = request.form["client"].strip()
        start  = request.form["start"]
        due    = request.form["due"]
        notes  = request.form.get("notes", "").strip()

        if start >= due:
            cur.close()
            conn.close()
            return render_template("assign.html", device=device,
                                   error="Due date must be after start date.")

        cur.execute(
            "INSERT INTO rentals (device_id, client_name, start_date, due_date, notes) VALUES (%s, %s, %s, %s, %s)",
            (id, client, start, due, notes)
        )
        cur.execute("UPDATE devices SET status = %s WHERE id = %s", ("Rented", id))
        conn.commit()
        cur.close()
        conn.close()
        return redirect("/")

    cur.close()
    conn.close()
    return render_template("assign.html", device=device, error=None)


@app.route("/return/<int:id>")
def return_device(id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE rentals SET returned = 1 WHERE device_id = %s AND returned = 0", (id,))
    cur.execute("UPDATE devices SET status = %s WHERE id = %s", ("Available", id))
    conn.commit()
    cur.close()
    conn.close()
    return redirect("/")


@app.route("/delete/<int:id>")
def delete_device(id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM devices WHERE id = %s", (id,))
    conn.commit()
    cur.close()
    conn.close()
    return redirect("/")


@app.route("/edit/<int:id>", methods=["GET", "POST"])
def edit_device(id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT * FROM devices WHERE id = %s", (id,))
    device = cur.fetchone()

    if not device:
        cur.close()
        conn.close()
        return "Device not found", 404

    if request.method == "POST":
        name   = request.form["name"].strip()
        type_  = request.form["type"].strip()
        serial = request.form["serial"].strip()
        tag_no = request.form.get("tag_no", "").strip()
        config = request.form.get("config", "").strip()

        cur.execute("SELECT id FROM devices WHERE serial = %s AND id != %s", (serial, id))
        if cur.fetchone():
            return render_template("edit.html", device=device,
                                   error="Serial number already in use.")

        cur.execute(
            "UPDATE devices SET name = %s, type = %s, serial = %s, tag_no = %s, config = %s WHERE id = %s",
            (name, type_, serial, tag_no, config, id)
        )
        conn.commit()
        cur.close()
        conn.close()
        return redirect("/")

    cur.close()
    conn.close()
    return render_template("edit.html", device=device, error=None)


@app.route("/history/<int:id>")
def device_history(id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT * FROM devices WHERE id = %s", (id,))
    device = cur.fetchone()

    if not device:
        cur.close()
        conn.close()
        return "Device not found", 404

    cur.execute(
        "SELECT * FROM rentals WHERE device_id = %s ORDER BY start_date DESC", (id,)
    )
    rentals = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("history.html", device=device, rentals=rentals)


@app.route("/maintenance/<int:id>", methods=["GET", "POST"])
def maintenance(id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT * FROM devices WHERE id = %s", (id,))
    device = cur.fetchone()

    if not device:
        cur.close()
        conn.close()
        return "Device not found", 404

    if request.method == "POST":
        issue = request.form["issue"].strip()
        cost  = request.form.get("cost", "").strip()

        cur.execute(
            "UPDATE rentals SET returned = 1 WHERE device_id = %s AND returned = 0", (id,)
        )
        cur.execute(
            "INSERT INTO maintenance (device_id, issue, cost) VALUES (%s, %s, %s)",
            (id, issue, cost)
        )
        cur.execute("UPDATE devices SET status = %s WHERE id = %s", ("Maintenance", id))
        conn.commit()
        cur.close()
        conn.close()
        return redirect("/")

    cur.execute(
        "SELECT * FROM maintenance WHERE device_id = %s ORDER BY reported_date DESC", (id,)
    )
    logs = cur.fetchall()
    cur.close()
    conn.close()
    return render_template("maintenance.html", device=device, logs=logs, error=None)


@app.route("/maintenance-resolve/<int:id>")
def maintenance_resolve(id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT device_id FROM maintenance WHERE id = %s", (id,))
    row = cur.fetchone()
    if not row:
        cur.close()
        conn.close()
        return "Not found", 404

    device_id = row["device_id"]

    cur.execute(
        "UPDATE maintenance SET resolved = 1, resolved_date = CURRENT_DATE WHERE id = %s", (id,)
    )

    cur.execute(
        "SELECT COUNT(*) as cnt FROM maintenance WHERE device_id = %s AND resolved = 0",
        (device_id,)
    )
    remaining = cur.fetchone()["cnt"]
    if remaining == 0:
        cur.execute("UPDATE devices SET status = %s WHERE id = %s", ("Available", device_id))

    conn.commit()
    cur.close()
    conn.close()
    return redirect(f"/maintenance/{device_id}")


if __name__ == "__main__":
    create_tables()
    app.run(debug=True)