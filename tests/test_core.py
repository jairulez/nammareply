import os, sys, time, unittest, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from assistant.core import Assistant, load_client, parse_time, parse_day
from assistant.store import Store

CLIENT = os.path.join(os.path.dirname(__file__), "..", "assistant", "clients", "demo-clinic.json")
MON = datetime.date(2026, 10, 26)   # a Monday
SAT = datetime.date(2026, 10, 31)
SUN = datetime.date(2026, 11, 1)


def mk():
    return Assistant(load_client(CLIENT), Store())


def customer_text(replies):
    return " ".join(r.text for r in replies if r.to != "OWNER")


class T(unittest.TestCase):
    def test_dates_sanity(self):
        self.assertEqual(MON.weekday(), 0); self.assertEqual(SAT.weekday(), 5); self.assertEqual(SUN.weekday(), 6)

    def test_parse(self):
        self.assertEqual(parse_time("saturday 10:30 am"), "10:30")
        self.assertEqual(parse_time("at 4pm"), "16:00")
        self.assertEqual(parse_time("14:00"), "14:00")
        self.assertIsNone(parse_time("13pm"))
        self.assertEqual(parse_day("on saturday", MON), SAT)
        self.assertEqual(parse_day("tomorrow", MON), MON + datetime.timedelta(days=1))

    def test_price_faq(self):
        r = mk().handle_message("whatsapp", "c1", "Hi, how much is teeth cleaning?", MON)
        self.assertIn("Rs 800", customer_text(r)); self.assertEqual(len(r), 1)

    def test_faq_and_hours(self):
        a = mk()
        self.assertIn("parking", customer_text(a.handle_message("whatsapp", "c1", "is there parking?", MON)).lower())
        self.assertIn("open", customer_text(a.handle_message("whatsapp", "c1", "what are your timings", MON)))

    def test_booking_and_conflict(self):
        a = mk()
        r = a.handle_message("whatsapp", "c1", "Book teeth cleaning Saturday 10:30 am", MON)
        self.assertIn("booked", customer_text(r)); self.assertTrue(any(x.kind == "owner_alert" for x in r))
        r2 = a.handle_message("whatsapp", "c2", "Book cleaning Saturday 10:30 am", MON)
        self.assertIn("already taken", customer_text(r2))

    def test_closed_and_off_slot(self):
        a = mk()
        self.assertIn("closed", customer_text(a.handle_message("whatsapp", "c1", "book consultation sunday 11 am", MON)))
        self.assertIn("not available", customer_text(a.handle_message("whatsapp", "c1", "book consultation saturday 10:10 am", MON)))
        self.assertIn("not available", customer_text(a.handle_message("whatsapp", "c1", "book consultation saturday 8 pm", MON)))

    def test_missing_time_asks(self):
        r = mk().handle_message("whatsapp", "c1", "I want to book an appointment", MON)
        self.assertIn("Which day and time", customer_text(r))

    def test_approval_gate_yes_no(self):
        a = mk()
        r = a.handle_message("whatsapp", "c1", "I want a refund, bad experience", MON)
        alert = [x for x in r if x.kind == "owner_alert"][0]
        self.assertIn("YES 1", alert.text)
        self.assertNotIn("refund approved", customer_text(r).lower())
        out = a.handle_owner_decision("NO 1")
        self.assertEqual(out[0].to, "c1"); self.assertIn("not able", out[0].text)
        again = a.handle_owner_decision("YES 1")      # already handled
        self.assertIn("already handled", again[0].text)
        self.assertIsNone(a.handle_owner_decision("hello"))

    def test_word_boundary_no_false_gate(self):
        a = mk()
        a.handle_message("whatsapp", "c1", "Do you do painting?", MON)   # 'pain' must not match 'painting'
        row = a.store.get_approval(1)
        self.assertEqual(row["reason"], "unknown question")

    def test_unknown_goes_to_owner_not_guessed(self):
        r = mk().handle_message("whatsapp", "c1", "Can you whiten my tongue piercing?", MON)
        self.assertTrue(any(x.kind == "owner_alert" for x in r))

    def test_greeting_not_triggered_by_this(self):
        r = mk().handle_message("whatsapp", "c1", "this", MON)
        self.assertTrue(any(x.kind == "owner_alert" for x in r))

    def test_digest(self):
        a = mk(); t0 = time.time() - 5
        a.handle_message("whatsapp", "c1", "Book cleaning Saturday 11 am", MON)
        a.handle_message("whatsapp", "c2", "refund please", MON)
        a.handle_message("whatsapp", "c3", "how much is root canal", MON)
        d = a.digest(t0)
        self.assertIn("Bookings made: 1", d); self.assertIn("Waiting for you: 1", d); self.assertIn("c3", d)

if __name__ == "__main__":
    unittest.main()
