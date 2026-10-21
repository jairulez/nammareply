import hashlib, hmac, json, os, sys, tempfile, threading, unittest, urllib.request, urllib.error
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from http.server import HTTPServer
from assistant.whatsapp import Config, App, Sender, verify_signature, extract_messages, make_handler

CUST, OWNER = "919900000001", "919900000002"


def payload(frm, text, wamid="wamid.1", typ="text"):
    m = {"from": frm, "id": wamid, "timestamp": "1", "type": typ}
    if typ == "text":
        m["text"] = {"body": text}
    return {"object": "whatsapp_business_account", "entry": [{"id": "W", "changes": [{"value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": "P"}, "messages": [m]}}]}]}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        open(d + "/secret", "w").write("appsecret\n")
        self.env = {"WA_VERIFY_TOKEN": "vt", "WA_APP_SECRET_FILE": d + "/secret", "WA_OWNER_NUMBER": "+" + OWNER,
                    "WA_ALLOWED_NUMBERS": CUST, "WA_DB": ":memory:", "WA_ENV_FILE": d + "/none.env"}
        self.cfg = Config(self.env)
        self.logs = []
        self.app = App(self.cfg, log=self.logs.append)

    def tearDown(self):
        self.tmp.cleanup()


class T(Base):
    def test_signature(self):
        body = b'{"a":1}'
        good = "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest()
        self.assertTrue(verify_signature("appsecret", body, good))
        self.assertFalse(verify_signature("appsecret", body, "sha256=00"))
        self.assertFalse(verify_signature("appsecret", body, None))
        self.assertFalse(verify_signature(None, body, good))

    def test_extract_skips_statuses(self):
        st = {"entry": [{"changes": [{"value": {"statuses": [{"id": "x", "status": "read"}]}}]}]}
        self.assertEqual(list(extract_messages(st)), [])
        self.assertEqual(list(extract_messages(payload(CUST, "hi")))[0][1:], (CUST, "text", "hi"))

    def test_customer_gets_reply_dry_run(self):
        self.assertEqual(self.app.handle_payload(payload(CUST, "how much is teeth cleaning")), 1)
        self.assertEqual(self.app.sender.outbox[0][0], CUST)
        self.assertIn("Rs 800", self.app.sender.outbox[0][1])

    def test_duplicate_delivery_ignored(self):
        self.app.handle_payload(payload(CUST, "parking?", "wamid.dup"))
        n = len(self.app.sender.outbox)
        self.assertEqual(self.app.handle_payload(payload(CUST, "parking?", "wamid.dup")), 0)
        self.assertEqual(len(self.app.sender.outbox), n)

    def test_stranger_ignored(self):
        self.app.handle_payload(payload("919911111111", "hello"))
        self.assertEqual(self.app.sender.outbox, [])

    def test_refund_alerts_owner_then_owner_yes(self):
        self.app.handle_payload(payload(CUST, "I want a refund", "w1"))
        tos = [t for t, _ in self.app.sender.outbox]
        self.assertEqual(sorted(tos), sorted([CUST, OWNER]))
        self.app.sender.outbox.clear()
        self.app.handle_payload(payload(OWNER, "YES 1", "w2"))
        self.assertIn(CUST, [t for t, _ in self.app.sender.outbox])

    def test_owner_garbage_gets_help(self):
        self.app.handle_payload(payload(OWNER, "hello?", "w3"))
        self.assertIn("YES", self.app.sender.outbox[0][1])

    def test_non_text_gets_polite_reply(self):
        self.app.handle_payload(payload(CUST, None, "w4", typ="image"))
        self.assertIn("text", self.app.sender.outbox[0][1])

    def test_dry_run_default_and_no_token_in_logs(self):
        self.assertTrue(self.cfg.dry_run)
        open(self.tmp.name + "/tok", "w").write("SECRET-TOKEN-123")
        cfg = Config({**self.env, "WA_DRY_RUN": "0", "WA_TOKEN_FILE": self.tmp.name + "/tok", "WA_PHONE_NUMBER_ID": "1"})
        logs = []
        s = Sender(cfg, logs.append)
        s.cfg.version = "v0"  # real call would fail offline; we only check the log content
        import unittest.mock as m
        with m.patch("urllib.request.urlopen", side_effect=OSError("boom")):
            self.assertFalse(s.send_text("919900000001", "x"))
        self.assertFalse(any("SECRET-TOKEN" in l for l in logs))

    def test_real_send_builds_correct_request(self):
        open(self.tmp.name + "/tok", "w").write("TKN")
        cfg = Config({**self.env, "WA_DRY_RUN": "0", "WA_TOKEN_FILE": self.tmp.name + "/tok", "WA_PHONE_NUMBER_ID": "555"})
        import unittest.mock as m
        seen = {}
        class R:
            status = 200
            def __enter__(s): return s
            def __exit__(s, *a): pass
        def fake(req, timeout=0):
            seen["url"], seen["auth"], seen["body"] = req.full_url, req.get_header("Authorization"), json.loads(req.data)
            return R()
        with m.patch("urllib.request.urlopen", fake):
            self.assertTrue(Sender(cfg, lambda *_: None).send_text("+91 99000 00001", "hello"))
        self.assertEqual(seen["url"], "https://graph.facebook.com/v25.0/555/messages")
        self.assertEqual(seen["auth"], "Bearer TKN")
        self.assertEqual(seen["body"]["to"], "919900000001")
        self.assertEqual(seen["body"]["messaging_product"], "whatsapp")


class Server(Base):
    def setUp(self):
        super().setUp()
        self.srv = HTTPServer(("127.0.0.1", 0), make_handler(self.app))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_port}"

    def tearDown(self):
        self.srv.shutdown(); self.srv.server_close(); super().tearDown()

    def post(self, body, sig):
        req = urllib.request.Request(self.base + "/", data=body, method="POST", headers={"X-Hub-Signature-256": sig} if sig else {})
        try:
            return urllib.request.urlopen(req).status
        except urllib.error.HTTPError as e:
            return e.code

    def test_verify_handshake(self):
        ok = urllib.request.urlopen(self.base + "/?hub.mode=subscribe&hub.verify_token=vt&hub.challenge=12345").read()
        self.assertEqual(ok, b"12345")
        with self.assertRaises(urllib.error.HTTPError) as c:
            urllib.request.urlopen(self.base + "/?hub.mode=subscribe&hub.verify_token=bad&hub.challenge=1")
        self.assertEqual(c.exception.code, 403)

    def test_post_signature_enforced(self):
        body = json.dumps(payload(CUST, "parking?")).encode()
        self.assertEqual(self.post(body, None), 403)
        self.assertEqual(self.post(body, "sha256=deadbeef"), 403)
        self.assertEqual(self.app.sender.outbox, [])
        sig = "sha256=" + hmac.new(b"appsecret", body, hashlib.sha256).hexdigest()
        self.assertEqual(self.post(body, sig), 200)
        self.assertEqual(len(self.app.sender.outbox), 1)

if __name__ == "__main__":
    unittest.main()
