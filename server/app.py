#!/usr/bin/env python3
"""DirectQuotes API: saves quote requests and sends two emails. Standard library only."""
import json, os, re, smtplib, sqlite3, ssl, threading, time, urllib.parse, urllib.request
from email.message import EmailMessage
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

E = os.environ.get
SMTP_HOST, SMTP_PORT = E("SMTP_HOST", ""), int(E("SMTP_PORT", "465"))
SMTP_USER, SMTP_PASS = E("SMTP_USER", "admin@netictechnologies.com"), E("SMTP_PASS", "")
MAIL_FROM = E("MAIL_FROM", "admin@netictechnologies.com")
MAIL_TO = E("MAIL_TO", "info@netictechnologies.com")
TURNSTILE_SECRET = E("TURNSTILE_SECRET", "")
DB_PATH = E("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "leads.db"))
PORT = int(E("PORT", "8099"))
RATE_MAX, RATE_WINDOW = 5, 3600
hits, lock = {}, threading.Lock()

def db():
    c = sqlite3.connect(DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS leads(id INTEGER PRIMARY KEY, created TEXT, issue TEXT, brand TEXT,
      suburb TEXT, property TEXT, name TEXT, phone TEXT, email TEXT, urgency TEXT, page TEXT, ip TEXT, email_status TEXT)""")
    return c

def clean(v, n=120):
    return re.sub(r"[\r\n\t]+", " ", str(v or "")).strip()[:n]

def wa_link(phone):
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("0"):
        digits = "27" + digits[1:]
    return "https://wa.me/" + digits

def send(msg):
    if not SMTP_HOST:
        raise RuntimeError("SMTP_HOST not set")
    if SMTP_PORT == 465:
        s = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=20)
    else:
        s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20); s.starttls(context=ssl.create_default_context())
    with s:
        s.login(SMTP_USER, SMTP_PASS); s.send_message(msg)

def notify(lead_id, d):
    status = []
    try:
        m = EmailMessage()
        m["Subject"] = f"New gate motor lead #{lead_id}: {d['suburb']} ({d['urgency']})"
        m["From"], m["To"], m["Reply-To"] = MAIL_FROM, MAIL_TO, d["email"]
        m.set_content("\n".join([f"Lead #{lead_id}", "",
            f"Name:     {d['name']}", f"Phone:    {d['phone']}", f"Email:    {d['email']}",
            f"WhatsApp: {wa_link(d['phone'])}", "",
            f"Problem:  {d['issue']}", f"Brand:    {d['brand']}", f"Suburb:   {d['suburb']}",
            f"Property: {d['property']}", f"Urgency:  {d['urgency']}", "", f"Source:   {d['page']}"]))
        send(m); status.append("admin:ok")
    except Exception as e:
        status.append(f"admin:{e}")
    try:
        m = EmailMessage()
        m["Subject"] = "We received your gate motor quote request"
        m["From"], m["To"], m["Reply-To"] = f"DirectQuotes <{MAIL_FROM}>", d["email"], MAIL_TO
        m.set_content(f"""Hi {d['name']},

Thanks for your request. We have received it and will get a quote to you within 48 hours.

Here is what you told us:
- Problem: {d['issue']}
- Motor brand: {d['brand']}
- Suburb: {d['suburb']}
- Urgency: {d['urgency']}

If anything changes, or the gate is unsecured right now, reply to this email.

Regards,
DirectQuotes
Netic Technologies
""")
        send(m); status.append("user:ok")
    except Exception as e:
        status.append(f"user:{e}")
    try:
        c = db(); c.execute("UPDATE leads SET email_status=? WHERE id=?", (" | ".join(status)[:500], lead_id)); c.commit(); c.close()
    except Exception:
        pass

def turnstile_ok(token, ip):
    if not TURNSTILE_SECRET:
        return True
    try:
        data = urllib.parse.urlencode({"secret": TURNSTILE_SECRET, "response": token, "remoteip": ip}).encode()
        with urllib.request.urlopen("https://challenges.cloudflare.com/turnstile/v0/siteverify", data, timeout=10) as r:
            return json.load(r).get("success", False)
    except Exception:
        return False

class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def reply(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        self.reply(200, {"ok": True}) if self.path == "/api/health" else self.reply(404, {"error": "not found"})
    def do_POST(self):
        if self.path != "/api/quote":
            return self.reply(404, {"error": "not found"})
        ip = self.headers.get("CF-Connecting-IP") or self.headers.get("X-Real-IP") or self.client_address[0]
        try:
            n = int(self.headers.get("Content-Length", 0))
            if n > 10000: return self.reply(413, {"error": "Request too large."})
            raw = json.loads(self.rfile.read(n))
        except Exception:
            return self.reply(400, {"error": "Invalid request."})
        if raw.get("website"):  # honeypot: pretend success
            return self.reply(200, {"ok": True})
        now = time.time()
        with lock:
            hits[ip] = [t for t in hits.get(ip, []) if now - t < RATE_WINDOW]
            if len(hits[ip]) >= RATE_MAX:
                return self.reply(429, {"error": "Too many requests. Try again later."})
            hits[ip].append(now)
        d = {k: clean(raw.get(k), 300 if k == "page" else 120) for k in
             ("issue", "brand", "suburb", "property", "name", "phone", "email", "urgency", "page")}
        if not raw.get("consent"): return self.reply(400, {"error": "Consent is required."})
        if len(d["name"]) < 2 or len(d["suburb"]) < 2 or not d["issue"]:
            return self.reply(400, {"error": "Please fill in all fields."})
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", d["email"]):
            return self.reply(400, {"error": "Enter a valid email address."})
        if not re.fullmatch(r"(\+?27|0)[\d\s-]{8,12}", d["phone"]):
            return self.reply(400, {"error": "Enter a valid South African phone number."})
        if not turnstile_ok(clean(raw.get("turnstile"), 2000), ip):
            return self.reply(400, {"error": "Spam check failed. Refresh and try again."})
        c = db()
        cur = c.execute("INSERT INTO leads(created,issue,brand,suburb,property,name,phone,email,urgency,page,ip) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (time.strftime("%Y-%m-%d %H:%M:%S"), d["issue"], d["brand"], d["suburb"], d["property"], d["name"], d["phone"], d["email"], d["urgency"], d["page"], ip))
        c.commit(); lid = cur.lastrowid; c.close()
        threading.Thread(target=notify, args=(lid, d), daemon=True).start()
        self.reply(200, {"ok": True})

if __name__ == "__main__":
    db().close()
    print(f"DirectQuotes API on 127.0.0.1:{PORT}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
