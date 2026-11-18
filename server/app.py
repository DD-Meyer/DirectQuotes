#!/usr/bin/env python3
"""DirectQuotes API: saves quote requests and sends two HTML emails. Standard library only."""
import json, os, re, smtplib, sqlite3, ssl, threading, time, urllib.parse, urllib.request
from email.message import EmailMessage
from html import escape as esc
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

E = os.environ.get
SMTP_HOST, SMTP_PORT = E("SMTP_HOST", ""), int(E("SMTP_PORT", "465"))
SMTP_USER, SMTP_PASS = E("SMTP_USER", "admin@netictechnologies.com"), E("SMTP_PASS", "")
MAIL_FROM = E("MAIL_FROM", "admin@netictechnologies.com")
MAIL_TO = E("MAIL_TO", "info@netictechnologies.com")
SITE = E("SITE_URL", "https://directquotes.co.za")
TURNSTILE_SECRET = E("TURNSTILE_SECRET", "")
DB_PATH = E("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "leads.db"))
PORT = int(E("PORT", "8099"))
RATE_MAX, RATE_WINDOW = 5, 3600
MAPS_API_KEY = E("MAPS_API_KEY", "")
hits, lock = {}, threading.Lock()

def db():
    c = sqlite3.connect(DB_PATH)
    c.execute("""CREATE TABLE IF NOT EXISTS leads(id INTEGER PRIMARY KEY, created TEXT, issue TEXT, brand TEXT,
      suburb TEXT, property TEXT, name TEXT, phone TEXT, email TEXT, urgency TEXT, page TEXT, ip TEXT, email_status TEXT)""")
    return c

def clean(v, n=120):
    return re.sub(r"[\r\n\t]+", " ", str(v or "")).strip()[:n]

def intl_digits(phone):
    digits = re.sub(r"\D", "", phone)
    return "27" + digits[1:] if digits.startswith("0") else digits

def wa_link(phone):
    return "https://wa.me/" + intl_digits(phone)

def tel_link(phone):
    return "tel:+" + intl_digits(phone)

# ---------- HTML email building blocks (table layout, inline styles, safe in Gmail/Outlook) ----------
FONT = "-apple-system,'Segoe UI',Helvetica,Arial,sans-serif"
LINK = "color:#0969da;text-decoration:none;"

def layout(preheader, heading, card, links, footer):
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light"></head>
<body style="margin:0;padding:0;background:#ffffff;">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:#ffffff;">{esc(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#ffffff;"><tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;font-family:{FONT};color:#1f2328;">
<tr><td align="center" style="padding-bottom:24px;font-size:22px;font-weight:800;letter-spacing:-0.01em;color:#14213d;">Direct<span style="color:#f2a900;">Quotes</span></td></tr>
<tr><td align="center" style="font-size:28px;line-height:1.3;font-weight:300;padding:0 8px 24px;">{heading}</td></tr>
<tr><td style="border:1px solid #d0d7de;border-radius:6px;padding:32px 28px;">{card}</td></tr>
<tr><td align="center" style="padding:24px 0 8px;font-size:14px;">{links}</td></tr>
<tr><td align="center" style="padding:16px 8px;font-size:12px;line-height:1.6;color:#59636e;">{footer}</td></tr>
</table></td></tr></table></body></html>"""

def icon(symbol):
    return ('<table role="presentation" align="center" cellpadding="0" cellspacing="0" style="margin:0 auto 16px;"><tr>'
            '<td align="center" width="56" height="56" style="width:56px;height:56px;border-radius:28px;background:#fff7e0;'
            f'border:2px solid #f2a900;font-size:26px;line-height:56px;font-weight:700;color:#14213d;">{symbol}</td></tr></table>')

def rows(pairs):
    """pairs: (label, html_value). Callers must esc() any user text before passing it in."""
    out = "".join(
        f'<tr><td style="padding:6px 16px 6px 0;color:#59636e;font-size:14px;vertical-align:top;white-space:nowrap;">{esc(k)}</td>'
        f'<td style="padding:6px 0;font-size:14px;font-weight:600;word-break:break-word;">{v}</td></tr>' for k, v in pairs)
    return f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%">{out}</table>'

def button(href, label):
    return ('<table role="presentation" align="center" cellpadding="0" cellspacing="0" style="margin:24px auto 0;"><tr>'
            '<td style="background:#f2a900;border-radius:6px;">'
            f'<a href="{esc(href, quote=True)}" style="display:inline-block;padding:12px 22px;font-size:15px;font-weight:700;'
            f'color:#14213d;text-decoration:none;">{esc(label)}</a></td></tr></table>')

def customer_email(d):
    name = esc(d["name"])
    text = f"""Hi {d['name']},

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
"""
    card = (icon("&#10003;")
        + '<div style="text-align:center;font-size:20px;font-weight:600;padding-bottom:8px;">Your quote is on its way</div>'
        + f'<p style="text-align:center;margin:0 0 24px;font-size:15px;line-height:1.6;">Hi {name}, we have received your request and will get a quote to you within <strong>48 hours</strong>.</p>'
        + '<div style="border-top:1px solid #d0d7de;padding-top:20px;">'
        + '<div style="font-weight:600;font-size:15px;padding-bottom:8px;">What you told us</div>'
        + rows([("Problem", esc(d["issue"])), ("Motor brand", esc(d["brand"])),
                ("Suburb", esc(d["suburb"])), ("Urgency", esc(d["urgency"]))])
        + '</div>'
        + '<p style="margin:20px 0 0;font-size:14px;line-height:1.6;color:#59636e;">If anything changes, or the gate is unsecured right now, just reply to this email.</p>')
    links = (f'<a href="mailto:{esc(MAIL_TO, quote=True)}" style="{LINK}">Reply to us</a>'
             f' &nbsp;&middot;&nbsp; <a href="{esc(SITE, quote=True)}/privacy.html" style="{LINK}">Privacy notice</a>')
    footer = ("You are receiving this email because you requested a gate motor repair quote on directquotes.co.za.<br>"
              "DirectQuotes is a Netic Technologies service.")
    html = layout(f"We have received your request and will send your quote within 48 hours.",
                  "Quote request <strong>received</strong>", card, links, footer)
    return text, html

def admin_email(lead_id, d):
    text = "\n".join([f"Lead #{lead_id}", "",
        f"Name:     {d['name']}", f"Phone:    {d['phone']}", f"Email:    {d['email']}",
        f"WhatsApp: {wa_link(d['phone'])}", "",
        f"Problem:  {d['issue']}", f"Brand:    {d['brand']}", f"Suburb:   {d['suburb']}",
        f"Property: {d['property']}", f"Urgency:  {d['urgency']}", "", f"Source:   {d['page']}"])
    card = (icon("#")
        + f'<div style="text-align:center;font-size:20px;font-weight:600;padding-bottom:8px;">{esc(d["issue"])}</div>'
        + f'<p style="text-align:center;margin:0 0 24px;font-size:15px;line-height:1.6;">{esc(d["suburb"])} &middot; {esc(d["urgency"])}</p>'
        + '<div style="border-top:1px solid #d0d7de;padding-top:20px;">'
        + '<div style="font-weight:600;font-size:15px;padding-bottom:8px;">Customer</div>'
        + rows([("Name", esc(d["name"])),
                ("Phone", f'<a href="{esc(tel_link(d["phone"]), quote=True)}" style="{LINK}">{esc(d["phone"])}</a>'),
                ("Email", f'<a href="mailto:{esc(d["email"], quote=True)}" style="{LINK}">{esc(d["email"])}</a>')])
        + '</div>'
        + '<div style="border-top:1px solid #d0d7de;margin-top:20px;padding-top:20px;">'
        + '<div style="font-weight:600;font-size:15px;padding-bottom:8px;">Job</div>'
        + rows([("Problem", esc(d["issue"])), ("Motor brand", esc(d["brand"])),
                ("Suburb", esc(d["suburb"])), ("Property", esc(d["property"])), ("Urgency", esc(d["urgency"]))])
        + '</div>'
        + button(wa_link(d["phone"]), f"WhatsApp {d['name']}")
        + f'<p style="margin:20px 0 0;font-size:12px;line-height:1.5;color:#59636e;word-break:break-all;">Source: {esc(d["page"])}</p>')
    links = (f'<a href="mailto:{esc(d["email"], quote=True)}" style="{LINK}">Email customer</a>'
             f' &nbsp;&middot;&nbsp; <a href="{esc(tel_link(d["phone"]), quote=True)}" style="{LINK}">Call customer</a>')
    footer = "Lead saved to the DirectQuotes database. Replying to this email goes straight to the customer."
    html = layout(f"{d['issue']} in {d['suburb']} ({d['urgency']})",
                  f"New gate motor lead <strong>#{lead_id}</strong>", card, links, footer)
    return text, html

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
        text, html = admin_email(lead_id, d)
        m = EmailMessage()
        m["Subject"] = f"New gate motor lead #{lead_id}: {d['suburb']} ({d['urgency']})"
        m["From"], m["To"], m["Reply-To"] = MAIL_FROM, MAIL_TO, d["email"]
        m.set_content(text); m.add_alternative(html, subtype="html")
        send(m); status.append("admin:ok")
    except Exception as e:
        status.append(f"admin:{e}")
    try:
        text, html = customer_email(d)
        m = EmailMessage()
        m["Subject"] = "We received your gate motor quote request"
        m["From"], m["To"], m["Reply-To"] = f"DirectQuotes <{MAIL_FROM}>", d["email"], MAIL_TO
        m.set_content(text); m.add_alternative(html, subtype="html")
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
        if self.path == "/api/health":
            return self.reply(200, {"ok": True})
        if self.path.split("?")[0] == "/api/config.js":
            body = ("window.DQ_CONFIG=" + json.dumps({"mapsKey": MAPS_API_KEY}) + ";").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Cache-Control", "public, max-age=3600")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers(); self.wfile.write(body)
            return
        self.reply(404, {"error": "not found"})
    def do_POST(self):
        if self.path != "/api/quote":
            return self.reply(404, {"error": "not found"})
        ip = self.headers.get("X-Forwarded-For", "").split(",")[-1].strip() or self.headers.get("X-Real-IP") or self.client_address[0]
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