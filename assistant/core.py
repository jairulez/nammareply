"""Assistant core: channel-agnostic. Connectors (WhatsApp, email) call handle_message()
and send whatever Reply objects come back. Nothing here sends anything itself."""
import json, re, datetime
from dataclasses import dataclass
from .store import Store

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


@dataclass
class Reply:
    to: str            # customer id (phone/email) or "OWNER"
    channel: str       # "whatsapp" | "email"
    text: str
    kind: str = "reply"  # reply | owner_alert


def load_client(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _has(text, words):
    t = text.lower()
    return any(w in t for w in words)


def parse_day(text, today):
    t = text.lower()
    if "today" in t:
        return today
    if "tomorrow" in t:
        return today + datetime.timedelta(days=1)
    m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", t)
    if m:
        return datetime.date.fromisoformat(m.group(1))
    for i, d in enumerate(["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]):
        if d in t or re.search(rf"\b{d[:3]}\b", t):
            delta = (i - today.weekday()) % 7
            return today + datetime.timedelta(days=delta or 7)
    return None


def parse_time(text):
    t = text.lower()
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", t)
    if m:
        h, mi, ap = int(m.group(1)), int(m.group(2) or 0), m.group(3)
        if not 1 <= h <= 12 or mi > 59:
            return None
        h = h % 12 + (12 if ap == "pm" else 0)
        return f"{h:02d}:{mi:02d}"
    m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", t)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return None


def open_window(client, day):
    key = DAYS[day.weekday()]
    for k, v in client["hours"].items():
        if "-" in k and len(k) == 7 and k[:3] in DAYS and k[4:] in DAYS:
            a, b = DAYS.index(k[:3]), DAYS.index(k[4:])
            if a <= DAYS.index(key) <= b:
                return None if v == "closed" else v
        elif k == key:
            return None if v == "closed" else v
    return None


def slot_ok(client, day, hhmm):
    w = open_window(client, day)
    if not w:
        return False
    a, b = w.split("-")
    step = client.get("slot_minutes", 30)
    mins = int(hhmm[:2]) * 60 + int(hhmm[3:])
    start, end = int(a[:2]) * 60 + int(a[3:]), int(b[:2]) * 60 + int(b[3:])
    return start <= mins < end and (mins - start) % step == 0


def find_service(client, text):
    for s in client["services"]:
        if _has(text, s["keywords"]) or s["name"].lower() in text.lower():
            return s
    return None


class Assistant:
    def __init__(self, client, store=None, channel_default="whatsapp"):
        self.c = client
        self.store = store or Store()

    def owner_alert(self, channel, text):
        return Reply("OWNER", channel, text, "owner_alert")

    def handle_owner_decision(self, text):
        """Owner replies like 'YES 3' or 'NO 3'. Returns replies to send."""
        m = re.match(r"\s*(yes|no)\s*#?(\d+)\s*$", text, re.I)
        if not m:
            return None
        ok, aid = m.group(1).lower() == "yes", int(m.group(2))
        row = self.store.get_approval(aid)
        if not row or row["client"] != self.c["id"] or row["status"] != "pending":
            return [self.owner_alert("whatsapp", f"Approval #{aid} not found or already handled.")]
        self.store.set_approval(aid, "approved" if ok else "declined")
        if ok:
            msg = "Thanks for waiting. The owner has approved this and will contact you shortly."
        else:
            msg = "Thanks for your message. We are not able to do this one, sorry. Please call us if you would like to talk it through."
        return [Reply(row["sender"], row["channel"], msg),
                self.owner_alert("whatsapp", f"Approval #{aid} marked {'YES' if ok else 'NO'} and customer told.")]

    def handle_message(self, channel, sender, text, today=None):
        today = today or datetime.date.today()
        cid = self.c["id"]
        self.store.log(cid, channel, sender, "in", text)
        replies = self._decide(channel, sender, text, today)
        for r in replies:
            self.store.log(cid, r.channel, r.to, "out", r.text)
        return replies

    def _decide(self, channel, sender, text, today):
        c = self.c
        # 1. approval gate comes first: money, complaints, anything risky
        topic = next((w for w in c["approval_topics"] if re.search(r"\b" + re.escape(w) + r"\b", text.lower())), None)
        if topic:
            aid = self.store.add_approval(c["id"], channel, sender, topic, text)
            self.store.upsert_lead(c["id"], channel, sender, status="needs_owner")
            return [
                Reply(sender, channel, "Thanks for telling us. I have passed this to the owner, who will get back to you personally."),
                self.owner_alert(channel, f"Approval #{aid} needed ({topic}) from {sender}: \"{text[:200]}\"\nReply YES {aid} or NO {aid}."),
            ]
        # 2. booking intent
        if _has(text, ["book", "appointment", "slot", "schedule", "available", "reschedule"]) or (parse_day(text, today) and parse_time(text)):
            return self._booking(channel, sender, text, today)
        # 3. FAQ and service price
        svc = find_service(c, text)
        if svc and _has(text, ["price", "cost", "how much", "rate", "fee", "charges"]):
            self.store.upsert_lead(c["id"], channel, sender, interest=svc["name"])
            return [Reply(sender, channel, f"{svc['name']}: {svc['price']}, about {svc['duration_min']} minutes. Would you like to book a slot? Tell me a day and time.")]
        for f in c["faqs"]:
            if _has(text, f["keywords"]):
                return [Reply(sender, channel, f["answer"])]
        if _has(text, ["hour", "open", "timing", "time"]):
            h = "; ".join(f"{k}: {v}" for k, v in c["hours"].items())
            return [Reply(sender, channel, f"We are open {h}.")]
        if _has(text, ["where", "address", "location", "direction"]):
            return [Reply(sender, channel, f"We are at {c['address']}.")]
        if svc:
            self.store.upsert_lead(c["id"], channel, sender, interest=svc["name"])
            return [Reply(sender, channel, f"Yes, we offer {svc['name']} ({svc['price']}). Would you like to book a slot?")]
        if re.search(r"\b(hi|hello|hey|namaste)\b", text.lower()) and len(text) < 25:
            self.store.upsert_lead(c["id"], channel, sender)
            return [Reply(sender, channel, f"{c['greeting']} How can we help? You can ask about services, prices, timings, or book a slot.")]
        # 4. unknown: do not guess, hand to owner
        aid = self.store.add_approval(c["id"], channel, sender, "unknown question", text)
        self.store.upsert_lead(c["id"], channel, sender, status="needs_owner")
        return [
            Reply(sender, channel, "Good question. Let me check with the team and get back to you soon."),
            self.owner_alert(channel, f"Question I could not answer (#{aid}) from {sender}: \"{text[:200]}\"\nReply to them directly, or YES {aid} / NO {aid} to just close it."),
        ]

    def _booking(self, channel, sender, text, today):
        c = self.c
        svc = find_service(c, text) or next((x for x in c["services"] if x["name"] == c.get("default_service")), c["services"][0])
        day, hhmm = parse_day(text, today), parse_time(text)
        if not day or not hhmm:
            return [Reply(sender, channel, "Happy to book you in. Which day and time suits you? For example: Saturday 10:30 am.")]
        if day < today:
            return [Reply(sender, channel, "That date has passed. Which upcoming day works for you?")]
        if not slot_ok(c, day, hhmm):
            w = open_window(c, day)
            hint = f"We are open {w} that day, in {c.get('slot_minutes', 30)}-minute slots." if w else "We are closed that day."
            return [Reply(sender, channel, f"Sorry, {hhmm} on {day.isoformat()} is not available. {hint} Which other time suits you?")]
        if self.store.booked(c["id"], day.isoformat(), hhmm):
            return [Reply(sender, channel, f"Sorry, {hhmm} on {day.isoformat()} is already taken. Could you pick another time?")]
        bid = self.store.add_booking(c["id"], channel, sender, svc["name"], day.isoformat(), hhmm)
        self.store.upsert_lead(c["id"], channel, sender, interest=svc["name"], status="booked")
        return [
            Reply(sender, channel, f"Done. {svc['name']} on {day.strftime('%a %d %b')} at {hhmm} is booked (ref #{bid}). We will remind you before the visit."),
            self.owner_alert(channel, f"New booking #{bid}: {svc['name']}, {day.isoformat()} {hhmm}, customer {sender}."),
        ]

    def digest(self, since_ts):
        cid = self.c["id"]
        msgs = [m for m in self.store.since("messages", cid, since_ts) if m["direction"] == "in"]
        books = self.store.since("bookings", cid, since_ts)
        leads = self.store.since("leads", cid, since_ts)
        pend = self.store.db.execute("SELECT * FROM approvals WHERE client=? AND status='pending'", (cid,)).fetchall()
        open_leads = [l for l in leads if l["status"] in ("new", "needs_owner")]
        lines = [f"{self.c['business_name']} - daily summary",
                 f"Messages received: {len(msgs)}",
                 f"Bookings made: {len(books)}"]
        for b in books:
            lines.append(f"  #{b['id']} {b['service']} {b['day']} {b['time']} ({b['sender']})")
        lines.append(f"Waiting for you: {len(pend)}")
        for p in pend:
            lines.append(f"  #{p['id']} {p['reason']}: {p['text'][:80]} (reply YES {p['id']} / NO {p['id']})")
        lines.append(f"Leads not yet booked: {len(open_leads)}")
        for l in open_leads:
            lines.append(f"  {l['sender']} ({l['interest'] or 'general'})")
        return "\n".join(lines)
