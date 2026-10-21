"""WhatsApp Cloud API connector (stdlib only).

Config comes from environment variables or a KEY=VALUE file (WA_ENV_FILE, default ./whatsapp.env).
The access token is NEVER an env value or logged: put it in a file and point WA_TOKEN_FILE at it.

  WA_PHONE_NUMBER_ID   Meta phone number id (not the phone number)
  WA_WABA_ID           WhatsApp Business Account id (informational)
  WA_VERIFY_TOKEN      any string you choose; enter the same in the Meta webhook setup
  WA_APP_SECRET_FILE   file holding the Meta app secret (signature check of POSTs)
  WA_TOKEN_FILE        file holding the access token
  WA_OWNER_NUMBER      owner's number, digits only with country code (gets alerts, sends YES/NO)
  WA_ALLOWED_NUMBERS   comma list of customer numbers allowed to get replies (test mode: max 5)
  WA_CLIENT_FILE       client json (default assistant/clients/demo-clinic.json)
  WA_DB                sqlite path (default nammareply.db)
  WA_GRAPH_VERSION     Graph API version (default v25.0, check Meta's current docs)
  WA_DRY_RUN           1 (default) = print instead of sending. Set 0 to really send.
  WA_PORT              default 8080
"""
import hashlib, hmac, json, os, sys, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from .core import Assistant, load_client
from .store import Store

HERE = os.path.dirname(__file__)


def load_env_file(path):
    out = {}
    if path and os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def read_secret(path):
    if not path:
        return None
    try:
        return open(path, encoding="utf-8").read().strip() or None
    except OSError:
        return None


def digits(s):
    return "".join(ch for ch in (s or "") if ch.isdigit())


class Config:
    def __init__(self, env=None):
        merged = dict(load_env_file((env or os.environ).get("WA_ENV_FILE", "whatsapp.env")))
        merged.update(env if env is not None else os.environ)
        g = merged.get
        self.phone_number_id = g("WA_PHONE_NUMBER_ID", "")
        self.waba_id = g("WA_WABA_ID", "")
        self.verify_token = g("WA_VERIFY_TOKEN", "")
        self.app_secret_file = g("WA_APP_SECRET_FILE", "")
        self.token_file = g("WA_TOKEN_FILE", "")
        self.owner = digits(g("WA_OWNER_NUMBER", ""))
        self.allowed = {digits(x) for x in g("WA_ALLOWED_NUMBERS", "").split(",") if digits(x)}
        self.client_file = g("WA_CLIENT_FILE", os.path.join(HERE, "clients", "demo-clinic.json"))
        self.db = g("WA_DB", "nammareply.db")
        self.version = g("WA_GRAPH_VERSION", "v25.0")
        self.dry_run = g("WA_DRY_RUN", "1") != "0"
        self.port = int(g("WA_PORT", "8080"))


def verify_signature(app_secret, raw_body, header):
    """Meta signs the raw POST body: X-Hub-Signature-256 = 'sha256=' + HMAC-SHA256(app secret, body)."""
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    want = hmac.new(app_secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header[7:])


def extract_messages(payload):
    """Yield (wamid, from_number, type, text) for inbound messages. Status updates are skipped."""
    for entry in payload.get("entry", []) or []:
        for ch in entry.get("changes", []) or []:
            for m in (ch.get("value", {}) or {}).get("messages", []) or []:
                text = (m.get("text") or {}).get("body") if m.get("type") == "text" else None
                yield m.get("id"), digits(m.get("from")), m.get("type"), text


class Sender:
    """Sends text through the Graph API. In dry-run it only records what it would send."""

    def __init__(self, cfg, log=lambda m: print(m, flush=True)):
        self.cfg, self.log, self.outbox = cfg, log, []

    def send_text(self, to, body):
        to = digits(to)
        if self.cfg.dry_run:
            self.outbox.append((to, body))
            self.log(f"[dry-run] would send to {to}: {body[:120]!r}")
            return True
        token = read_secret(self.cfg.token_file)
        if not token or not self.cfg.phone_number_id:
            self.log("send skipped: token file or phone number id missing")
            return False
        url = f"https://graph.facebook.com/{self.cfg.version}/{self.cfg.phone_number_id}/messages"
        data = json.dumps({"messaging_product": "whatsapp", "recipient_type": "individual", "to": to,
                           "type": "text", "text": {"preview_url": False, "body": body}}).encode()
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                self.log(f"sent to {to}: HTTP {r.status}")
                return True
        except urllib.error.HTTPError as e:
            self.log(f"send to {to} failed: HTTP {e.code} {e.read()[:200]!r}")  # body never contains our token
        except Exception as e:  # network
            self.log(f"send to {to} failed: {type(e).__name__}")
        return False


class App:
    def __init__(self, cfg, sender=None, log=lambda m: print(m, flush=True)):
        self.cfg, self.log = cfg, log
        self.assistant = Assistant(load_client(cfg.client_file), Store(cfg.db))
        self.sender = sender or Sender(cfg, log)
        self.seen = set()

    def handle_payload(self, payload):
        n = 0
        for wamid, frm, typ, text in extract_messages(payload):
            if not wamid or wamid in self.seen or not frm:
                continue
            self.seen.add(wamid)
            n += 1
            if self.cfg.owner and frm == self.cfg.owner:
                out = self.assistant.handle_owner_decision(text or "") if text else None
                if out is None:
                    self.sender.send_text(frm, "Reply YES <number> or NO <number> to an approval request.")
                    continue
            elif frm not in self.cfg.allowed:
                self.log(f"ignored message from non-allowed number ending {frm[-4:]}")
                continue
            elif text is None:
                self.sender.send_text(frm, "Thanks! I can read text messages for now. Please type your question.")
                continue
            else:
                out = self.assistant.handle_message("whatsapp", frm, text)
            for r in out:
                to = self.cfg.owner if r.to == "OWNER" else r.to
                if to:
                    self.sender.send_text(to, r.text)
        return n


def make_handler(app):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep tokens/query strings out of access logs
            pass

        def _reply(self, code, body=b""):
            self.send_response(code); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            g = lambda k: (q.get(k) or [""])[0]
            if g("hub.mode") == "subscribe" and app.cfg.verify_token and hmac.compare_digest(g("hub.verify_token"), app.cfg.verify_token):
                return self._reply(200, g("hub.challenge").encode())
            self._reply(403)

        def do_POST(self):
            raw = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 1_000_000))
            secret = read_secret(app.cfg.app_secret_file)
            if not verify_signature(secret, raw, self.headers.get("X-Hub-Signature-256", "")):
                return self._reply(403)
            try:
                app.handle_payload(json.loads(raw))
            except Exception as e:
                app.log(f"handler error: {type(e).__name__}")
            self._reply(200)  # always 200 once authentic, so Meta does not retry-storm
    return H


def main():
    cfg = Config()
    app = App(cfg)
    print(f"NammaReply WhatsApp webhook on :{cfg.port} | dry_run={cfg.dry_run} | allowed={len(cfg.allowed)} | owner_set={bool(cfg.owner)}")
    if not cfg.verify_token or not cfg.app_secret_file:
        print("warning: WA_VERIFY_TOKEN or WA_APP_SECRET_FILE missing; Meta verification/POSTs will be rejected")
    HTTPServer(("0.0.0.0", cfg.port), make_handler(app)).serve_forever()


if __name__ == "__main__":
    main()
