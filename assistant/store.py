"""Tiny sqlite3 store (stdlib). One file per deployment; move to Postgres later."""
import sqlite3, json, time


class Store:
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, client TEXT, channel TEXT, sender TEXT,
            direction TEXT, text TEXT, ts REAL);
        CREATE TABLE IF NOT EXISTS leads(id INTEGER PRIMARY KEY, client TEXT, channel TEXT, sender TEXT,
            name TEXT, interest TEXT, status TEXT, ts REAL, UNIQUE(client, channel, sender));
        CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY, client TEXT, sender TEXT, channel TEXT,
            service TEXT, day TEXT, time TEXT, status TEXT, ts REAL);
        CREATE TABLE IF NOT EXISTS approvals(id INTEGER PRIMARY KEY, client TEXT, channel TEXT, sender TEXT,
            reason TEXT, text TEXT, status TEXT, ts REAL);
        """)

    def log(self, client, channel, sender, direction, text):
        self.db.execute("INSERT INTO messages(client,channel,sender,direction,text,ts) VALUES(?,?,?,?,?,?)",
                        (client, channel, sender, direction, text, time.time()))
        self.db.commit()

    def upsert_lead(self, client, channel, sender, interest=None, status="new"):
        row = self.db.execute("SELECT * FROM leads WHERE client=? AND channel=? AND sender=?",
                              (client, channel, sender)).fetchone()
        if row is None:
            self.db.execute("INSERT INTO leads(client,channel,sender,interest,status,ts) VALUES(?,?,?,?,?,?)",
                            (client, channel, sender, interest, status, time.time()))
        else:
            self.db.execute("UPDATE leads SET interest=COALESCE(?,interest), status=? WHERE id=?",
                            (interest, status if status != "new" else row["status"], row["id"]))
        self.db.commit()

    def booked(self, client, day, time_):
        r = self.db.execute("SELECT 1 FROM bookings WHERE client=? AND day=? AND time=? AND status='confirmed'",
                            (client, day, time_)).fetchone()
        return r is not None

    def add_booking(self, client, channel, sender, service, day, time_):
        cur = self.db.execute("INSERT INTO bookings(client,sender,channel,service,day,time,status,ts) VALUES(?,?,?,?,?,?,?,?)",
                              (client, sender, channel, service, day, time_, "confirmed", time.time()))
        self.db.commit()
        return cur.lastrowid

    def add_approval(self, client, channel, sender, reason, text):
        cur = self.db.execute("INSERT INTO approvals(client,channel,sender,reason,text,status,ts) VALUES(?,?,?,?,?,?,?)",
                              (client, channel, sender, reason, text, "pending", time.time()))
        self.db.commit()
        return cur.lastrowid

    def get_approval(self, aid):
        return self.db.execute("SELECT * FROM approvals WHERE id=?", (aid,)).fetchone()

    def set_approval(self, aid, status):
        self.db.execute("UPDATE approvals SET status=? WHERE id=?", (status, aid))
        self.db.commit()

    def since(self, table, client, ts):
        return self.db.execute(f"SELECT * FROM {table} WHERE client=? AND ts>=? ORDER BY ts", (client, ts)).fetchall()
