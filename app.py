import os, sqlite3, json, secrets, io, datetime as dt
from functools import wraps
from flask import (Flask, g, request, session, redirect, url_for, render_template,
                   flash, abort, send_file, send_from_directory)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

DATA = os.environ.get("DATA_DIR", "./data")
os.makedirs(DATA + "/uploads", exist_ok=True)
app = Flask(__name__)
keyf = DATA + "/secret.key"
if not os.path.exists(keyf):
    open(keyf, "w").write(secrets.token_hex(32))
app.secret_key = open(keyf).read()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") == "1",
                  MAX_CONTENT_LENGTH=15 * 1024 * 1024)

ROLES = {"admin": "Administrateur", "tax": "Tax Manager", "rc": "Responsable comptable / Expert-comptable", "viewer": "Lecteur"}
RISKS = ["Faible", "Moyen", "Élevé"]
TAXES = ["IS", "TVA", "CVAE", "CFE", "Retenue à la source", "Autre"]
STATUS = {"draft": "Brouillon", "submitted": "Soumise", "certified": "Certifiée", "rejected": "Rejetée"}
DEDUCT = ["Déductible", "Non déductible", "Partiellement déductible", "À définir"]
ADJ = [("none", "Aucun retraitement"), ("reint", "Réintégration (+)"), ("deduc", "Déduction (−)")]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT, role TEXT, pw TEXT, active INT DEFAULT 1);
CREATE TABLE IF NOT EXISTS companies(id INTEGER PRIMARY KEY, name TEXT, siren TEXT, closing TEXT, regime TEXT);
CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, company_id INT, year INT, due TEXT, accounting_result REAL DEFAULT 0, closed INT DEFAULT 0, UNIQUE(company_id,year));
CREATE TABLE IF NOT EXISTS positions(id INTEGER PRIMARY KEY, review_id INT, subject TEXT, tax_type TEXT, category TEXT, account TEXT,
 amount REAL DEFAULT 0, risk TEXT, question TEXT, facts TEXT, legal_basis TEXT, conclusion TEXT, deductibility TEXT,
 adj_type TEXT DEFAULT 'none', adj_amount REAL DEFAULT 0, diff_nature TEXT, rate REAL DEFAULT 25,
 status TEXT DEFAULT 'draft', version INT DEFAULT 1, final_treatment TEXT, rc_note TEXT, certified_by INT, certified_at TEXT,
 owner_id INT, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS comments(id INTEGER PRIMARY KEY, position_id INT, user_id INT, body TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, position_id INT, name TEXT, stored TEXT, user_id INT, ts TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, ts TEXT, user_id INT, position_id INT, review_id INT, action TEXT, detail TEXT);
"""

def db():
    if "db" not in g:
        g.db = sqlite3.connect(DATA + "/tax.db")
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close(e):
    d = g.pop("db", None)
    if d: d.close()

def now(): return dt.datetime.now().strftime("%Y-%m-%d %H:%M")
def q(sql, *a): return db().execute(sql, a).fetchall()
def q1(sql, *a): return db().execute(sql, a).fetchone()
def ex(sql, *a):
    c = db().execute(sql, a); db().commit(); return c.lastrowid
def log(action, detail="", pid=None, rid=None):
    ex("INSERT INTO audit(ts,user_id,position_id,review_id,action,detail) VALUES(?,?,?,?,?,?)",
       now(), g.user["id"] if g.get("user") else None, pid, rid, action, detail)

def fnum(v):
    try: return float(str(v).replace(" ", "").replace(",", ".") or 0)
    except ValueError: return 0.0

def deferred(p):
    base = p["adj_amount"] if p["adj_type"] != "none" else 0
    return round(base * (p["rate"] or 0) / 100, 2) if p["diff_nature"] == "Temporaire" else 0.0

def missing(p):
    m = []
    if not (p["question"] or "").strip(): m.append("question")
    if not (p["conclusion"] or "").strip(): m.append("conclusion")
    if p["deductibility"] in (None, "", "À définir"): m.append("déductibilité")
    if not (p["legal_basis"] or "").strip(): m.append("base légale")
    return m

@app.template_filter("eur")
def eur(v):
    try: return f"{float(v):,.0f}".replace(",", " ") + " €"
    except (TypeError, ValueError): return "—"

app.jinja_env.globals.update(ROLES=ROLES, STATUS=STATUS, RISKS=RISKS, deferred=deferred, missing=missing)

@app.before_request
def load():
    g.user = q1("SELECT * FROM users WHERE id=? AND active=1", session["uid"]) if "uid" in session else None
    if request.endpoint in ("login", "static", "health"): return
    if not g.user: return redirect(url_for("login"))
    if request.method == "POST":
        if not secrets.compare_digest(request.form.get("_csrf", ""), session.get("csrf", "x")): abort(400)

@app.context_processor
def ctx():
    session.setdefault("csrf", secrets.token_hex(16))
    return dict(csrf=session["csrf"], me=g.get("user"))

def need(*roles):
    def d(f):
        @wraps(f)
        def w(*a, **k):
            if g.user["role"] not in roles and g.user["role"] != "admin": abort(403)
            return f(*a, **k)
        return w
    return d

@app.route("/health")
def health(): return "ok"

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = q1("SELECT * FROM users WHERE email=? AND active=1", request.form["email"].strip().lower())
        if u and check_password_hash(u["pw"], request.form["password"]):
            session.clear(); session["uid"] = u["id"]; session["csrf"] = secrets.token_hex(16)
            return redirect(url_for("dashboard"))
        flash("Identifiants invalides.")
    return render_template("login.html")

@app.route("/logout", methods=["POST"])
def logout():
    session.clear(); return redirect(url_for("login"))

@app.route("/")
def dashboard():
    pos = q("""SELECT p.*, c.name cname, r.year, r.company_id FROM positions p JOIN reviews r ON r.id=p.review_id
               JOIN companies c ON c.id=r.company_id ORDER BY p.updated DESC""")
    st = {k: sum(1 for p in pos if p["status"] == k) for k in STATUS}
    high = [p for p in pos if p["risk"] == "Élevé" and p["status"] != "certified"]
    incomplete = [p for p in pos if p["status"] in ("draft", "rejected") and missing(p)]
    toc = [p for p in pos if p["status"] == "submitted"]
    rejected = [p for p in pos if p["status"] == "rejected"]
    ncomp = q1("SELECT COUNT(*) n FROM companies")["n"]
    nrev = q1("SELECT COUNT(*) n FROM reviews WHERE closed=0")["n"]
    return render_template("dashboard.html", st=st, high=high, incomplete=incomplete, toc=toc, rejected=rejected,
                           ncomp=ncomp, nrev=nrev, total=len(pos), recent=pos[:8])

@app.route("/companies", methods=["GET", "POST"])
def companies():
    if request.method == "POST":
        if g.user["role"] not in ("admin", "tax"): abort(403)
        f = request.form
        if not f["name"].strip(): flash("Nom obligatoire."); return redirect(url_for("companies"))
        cid = ex("INSERT INTO companies(name,siren,closing,regime) VALUES(?,?,?,?)", f["name"].strip(), f["siren"], f["closing"], f["regime"])
        log("Société créée", f["name"]); return redirect(url_for("company", cid=cid))
    rows = q("""SELECT c.*, (SELECT COUNT(*) FROM reviews WHERE company_id=c.id) nrev,
      (SELECT COUNT(*) FROM positions p JOIN reviews r ON r.id=p.review_id WHERE r.company_id=c.id) npos,
      (SELECT COUNT(*) FROM positions p JOIN reviews r ON r.id=p.review_id WHERE r.company_id=c.id AND p.risk='Élevé' AND p.status!='certified') nhigh,
      (SELECT COUNT(*) FROM positions p JOIN reviews r ON r.id=p.review_id WHERE r.company_id=c.id AND p.status='certified') ncert
      FROM companies c ORDER BY c.name""")
    return render_template("companies.html", rows=rows)

@app.route("/companies/<int:cid>", methods=["GET", "POST"])
def company(cid):
    c = q1("SELECT * FROM companies WHERE id=?", cid) or abort(404)
    if request.method == "POST":
        if g.user["role"] not in ("admin", "tax"): abort(403)
        y = int(request.form["year"])
        if q1("SELECT 1 FROM reviews WHERE company_id=? AND year=?", cid, y): flash("Revue déjà existante.")
        else:
            rid = ex("INSERT INTO reviews(company_id,year,due,accounting_result) VALUES(?,?,?,?)", cid, y, request.form["due"], fnum(request.form["res"]))
            log("Revue créée", f"{c['name']} {y}", rid=rid); return redirect(url_for("review", rid=rid))
    revs = q("""SELECT r.*, (SELECT COUNT(*) FROM positions WHERE review_id=r.id) n,
       (SELECT COUNT(*) FROM positions WHERE review_id=r.id AND status='certified') ncert FROM reviews r WHERE company_id=? ORDER BY year DESC""", cid)
    return render_template("company.html", c=c, revs=revs, year=dt.date.today().year)

def recon(r):
    pos = q("SELECT * FROM positions WHERE review_id=? AND tax_type='IS'", r["id"])
    reint = sum(p["adj_amount"] for p in pos if p["adj_type"] == "reint")
    deduc = sum(p["adj_amount"] for p in pos if p["adj_type"] == "deduc")
    ids = sum(deferred(p) for p in pos)
    return dict(res=r["accounting_result"], reint=reint, deduc=deduc, fiscal=r["accounting_result"] + reint - deduc, ids=ids)

@app.route("/reviews/<int:rid>")
def review(rid):
    r = q1("SELECT r.*, c.name cname FROM reviews r JOIN companies c ON c.id=r.company_id WHERE r.id=?", rid) or abort(404)
    flt = {k: request.args.get(k, "") for k in ("status", "risk", "cat")}
    sql, a = "SELECT p.*, u.name owner FROM positions p LEFT JOIN users u ON u.id=p.owner_id WHERE review_id=?", [rid]
    for col, k in (("status", "status"), ("risk", "risk"), ("category", "cat")):
        if flt[k]: sql += f" AND p.{col}=?"; a.append(flt[k])
    pos = q(sql + " ORDER BY p.id", *a)
    allp = q("SELECT status FROM positions WHERE review_id=?", rid)
    can_close = bool(allp) and all(p["status"] == "certified" for p in allp) and not r["closed"]
    return render_template("review.html", r=r, pos=pos, flt=flt, rc=recon(r), can_close=can_close, total=len(allp))

@app.route("/reviews/<int:rid>/result", methods=["POST"])
@need("tax")
def review_result(rid):
    r = q1("SELECT * FROM reviews WHERE id=?", rid) or abort(404)
    if r["closed"]: abort(400)
    ex("UPDATE reviews SET accounting_result=? WHERE id=?", fnum(request.form["res"]), rid)
    log("Résultat comptable modifié", f"{r['accounting_result']} → {fnum(request.form['res'])}", rid=rid)
    return redirect(url_for("review", rid=rid))

@app.route("/reviews/<int:rid>/close", methods=["POST"])
@need("tax")
def review_close(rid):
    if q1("SELECT 1 FROM positions WHERE review_id=? AND status!='certified'", rid): flash("Toutes les positions doivent être certifiées."); return redirect(url_for("review", rid=rid))
    ex("UPDATE reviews SET closed=1 WHERE id=?", rid); log("Revue clôturée", rid=rid)
    return redirect(url_for("review", rid=rid))

FIELDS = ["subject", "tax_type", "category", "account", "amount", "risk", "question", "facts", "legal_basis", "conclusion",
          "deductibility", "adj_type", "adj_amount", "diff_nature", "rate"]
NUMS = {"amount", "adj_amount", "rate"}

def read_form():
    return {k: (fnum(request.form.get(k)) if k in NUMS else request.form.get(k, "").strip()) for k in FIELDS}

def opts(): return dict(TAXES=TAXES, DEDUCT=DEDUCT, ADJ=ADJ)

@app.route("/reviews/<int:rid>/positions/new", methods=["GET", "POST"])
@need("tax")
def position_new(rid):
    r = q1("SELECT r.*, c.name cname FROM reviews r JOIN companies c ON c.id=r.company_id WHERE r.id=?", rid) or abort(404)
    if r["closed"]: abort(400)
    if request.method == "POST":
        d = read_form()
        if not d["subject"]:
            flash("Le sujet est obligatoire."); return render_template("position_form.html", r=r, p=d, new=True, **opts())
        t = now()
        pid = ex(f"INSERT INTO positions(review_id,{','.join(FIELDS)},owner_id,created,updated) VALUES(?,{','.join('?'*len(FIELDS))},?,?,?)",
                 rid, *[d[k] for k in FIELDS], g.user["id"], t, t)
        log("Position créée", d["subject"], pid=pid, rid=rid)
        return redirect(url_for("position", pid=pid))
    return render_template("position_form.html", r=r, p=dict(tax_type="IS", category="charge", risk="Faible", deductibility="À définir",
                           adj_type="none", diff_nature="Permanente", rate=25, amount=0, adj_amount=0), new=True, **opts())

def load_pos(pid):
    p = q1("""SELECT p.*, r.year, r.company_id, r.closed, r.id rid, c.name cname, u.name owner, cu.name certifier
      FROM positions p JOIN reviews r ON r.id=p.review_id JOIN companies c ON c.id=r.company_id
      LEFT JOIN users u ON u.id=p.owner_id LEFT JOIN users cu ON cu.id=p.certified_by WHERE p.id=?""", pid)
    return p or abort(404)

@app.route("/positions/<int:pid>")
def position(pid):
    p = load_pos(pid)
    com = q("SELECT c.*, u.name, u.role FROM comments c JOIN users u ON u.id=c.user_id WHERE position_id=? ORDER BY c.id", pid)
    files = q("SELECT f.*, u.name uname FROM files f JOIN users u ON u.id=f.user_id WHERE position_id=?", pid)
    hist = q("SELECT a.*, u.name FROM audit a LEFT JOIN users u ON u.id=a.user_id WHERE position_id=? ORDER BY a.id DESC", pid)
    return render_template("position.html", p=p, com=com, files=files, hist=hist, miss=missing(p), idv=deferred(p),
                           adjl=dict(ADJ), can_edit=g.user["role"] in ("admin", "tax") and not p["closed"] and p["status"] != "submitted")

@app.route("/positions/<int:pid>/edit", methods=["GET", "POST"])
@need("tax")
def position_edit(pid):
    p = load_pos(pid)
    if p["closed"] or p["status"] == "submitted":
        flash("Position verrouillée (soumise ou revue clôturée)."); return redirect(url_for("position", pid=pid))
    if request.method == "POST":
        d = read_form()
        if not d["subject"]: flash("Le sujet est obligatoire.")
        else:
            ch = {k: [p[k], d[k]] for k in FIELDS if (p[k] or "") != d[k]}
            ver, st = p["version"], p["status"]
            if st == "certified" and ch: ver, st = ver + 1, "draft"
            ex(f"UPDATE positions SET {','.join(k+'=?' for k in FIELDS)},version=?,status=?,updated=?,certified_by=?,certified_at=?,rc_note=? WHERE id=?",
               *[d[k] for k in FIELDS], ver, st, now(),
               None if ver != p["version"] else p["certified_by"], None if ver != p["version"] else p["certified_at"],
               None if ver != p["version"] else p["rc_note"], pid)
            log("Position modifiée" + (" → nouvelle version, certification retirée" if ver != p["version"] else ""),
                json.dumps(ch, ensure_ascii=False), pid=pid, rid=p["rid"])
            return redirect(url_for("position", pid=pid))
    return render_template("position_form.html", r=dict(cname=p["cname"], year=p["year"], id=p["rid"]), p=p, new=False, pid=pid, **opts())

@app.route("/positions/<int:pid>/submit", methods=["POST"])
@need("tax")
def position_submit(pid):
    p = load_pos(pid)
    if p["status"] not in ("draft", "rejected") or p["closed"]: abort(400)
    m = missing(p)
    if m: flash("Champs manquants : " + ", ".join(m)); return redirect(url_for("position", pid=pid))
    ex("UPDATE positions SET status='submitted',updated=? WHERE id=?", now(), pid)
    log("Soumise au comptable", f"v{p['version']}", pid=pid, rid=p["rid"])
    return redirect(url_for("position", pid=pid))

@app.route("/positions/<int:pid>/decide", methods=["POST"])
@need("rc")
def position_decide(pid):
    p = load_pos(pid)
    if p["status"] != "submitted": abort(400)
    ok = request.form["decision"] == "certify"
    ft, note = request.form.get("final_treatment", "").strip(), request.form.get("rc_note", "").strip()
    if ok and not ft: flash("Indique le traitement final en liasse (ex. 2058-A, ligne WE)."); return redirect(url_for("position", pid=pid))
    if not ok and not note: flash("Un motif de rejet est obligatoire."); return redirect(url_for("position", pid=pid))
    ex("UPDATE positions SET status=?,final_treatment=?,rc_note=?,certified_by=?,certified_at=?,updated=? WHERE id=?",
       "certified" if ok else "rejected", ft, note, g.user["id"], now(), now(), pid)
    log("Certifiée" if ok else "Rejetée", f"{ft} {note}".strip(), pid=pid, rid=p["rid"])
    return redirect(url_for("position", pid=pid))

@app.route("/positions/<int:pid>/comment", methods=["POST"])
def comment(pid):
    p = load_pos(pid)
    if g.user["role"] == "viewer": abort(403)
    b = request.form["body"].strip()
    if b:
        ex("INSERT INTO comments(position_id,user_id,body,ts) VALUES(?,?,?,?)", pid, g.user["id"], b, now())
        log("Commentaire", b[:80], pid=pid, rid=p["rid"])
    return redirect(url_for("position", pid=pid) + "#comments")

@app.route("/positions/<int:pid>/upload", methods=["POST"])
def upload(pid):
    load_pos(pid)
    if g.user["role"] == "viewer": abort(403)
    f = request.files.get("file")
    if f and f.filename:
        stored = secrets.token_hex(8) + "_" + secure_filename(f.filename)
        f.save(os.path.join(DATA, "uploads", stored))
        ex("INSERT INTO files(position_id,name,stored,user_id,ts) VALUES(?,?,?,?,?)", pid, f.filename, stored, g.user["id"], now())
        log("Pièce jointe", f.filename, pid=pid)
    return redirect(url_for("position", pid=pid))

@app.route("/files/<int:fid>")
def download(fid):
    f = q1("SELECT * FROM files WHERE id=?", fid) or abort(404)
    return send_from_directory(os.path.join(DATA, "uploads"), f["stored"], as_attachment=True, download_name=f["name"])

@app.route("/certifications")
def certifications():
    rows = q("""SELECT p.*, c.name cname, r.year FROM positions p JOIN reviews r ON r.id=p.review_id JOIN companies c ON c.id=r.company_id
                WHERE p.status='submitted' ORDER BY p.updated""")
    return render_template("certifications.html", rows=rows)

@app.route("/reviews/<int:rid>/export.xlsx")
def export(rid):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    r = q1("SELECT r.*, c.name cname FROM reviews r JOIN companies c ON c.id=r.company_id WHERE r.id=?", rid) or abort(404)
    wb = Workbook(); ws = wb.active; ws.title = "Positions"
    H = ["#", "Sujet", "Impôt", "Nature", "Compte", "Montant", "Risque", "Question fiscale", "Faits", "Base légale", "Conclusion", "Déductibilité",
         "Retraitement", "Montant retraité", "Différence", "Taux ID %", "ID calculé", "Statut", "Version", "Traitement en liasse", "Certifié par", "Date", "Note RC"]
    ws.append(H)
    for c in ws[1]: c.font = Font(bold=True, color="FFFFFF"); c.fill = PatternFill("solid", fgColor="1F3A5F")
    adjl = dict(ADJ)
    for p in q("SELECT p.*, u.name cu FROM positions p LEFT JOIN users u ON u.id=p.certified_by WHERE review_id=? ORDER BY p.id", rid):
        ws.append([p["id"], p["subject"], p["tax_type"], p["category"], p["account"], p["amount"], p["risk"], p["question"], p["facts"], p["legal_basis"],
                   p["conclusion"], p["deductibility"], adjl.get(p["adj_type"]), p["adj_amount"], p["diff_nature"], p["rate"], deferred(p),
                   STATUS[p["status"]], p["version"], p["final_treatment"], p["cu"], p["certified_at"], p["rc_note"]])
    for col, w in zip("ABCDEFGHIJKLMNOPQRSTUVW", [5, 34, 8, 9, 10, 12, 9, 40, 40, 30, 40, 22, 20, 14, 12, 9, 12, 11, 8, 22, 18, 17, 30]):
        ws.column_dimensions[col].width = w
    w2 = wb.create_sheet("Réconciliation"); x = recon(r)
    for row in [("Société", r["cname"]), ("Exercice", r["year"]), ("Résultat comptable", x["res"]), ("+ Réintégrations", x["reint"]),
                ("− Déductions", x["deduc"]), ("= Résultat fiscal", x["fiscal"]), ("Impôts différés", x["ids"])]: w2.append(row)
    w2.column_dimensions["A"].width = 24; w2.column_dimensions["B"].width = 22
    w3 = wb.create_sheet("Journal d'audit")
    w3.append(["Date", "Utilisateur", "Position", "Action", "Détail"])
    for a in q("SELECT a.*, u.name FROM audit a LEFT JOIN users u ON u.id=a.user_id WHERE review_id=? ORDER BY a.id", rid):
        w3.append([a["ts"], a["name"], a["position_id"], a["action"], a["detail"]])
    b = io.BytesIO(); wb.save(b); b.seek(0)
    log("Export Excel", rid=rid)
    return send_file(b, as_attachment=True, download_name=f"revue_{r['cname']}_{r['year']}.xlsx".replace(" ", "_"))

@app.route("/users", methods=["GET", "POST"])
@need()
def users():
    if request.method == "POST":
        f = request.form
        em = f["email"].strip().lower()
        if q1("SELECT 1 FROM users WHERE email=?", em) or len(f["password"]) < 10 or f["role"] not in ROLES:
            flash("Email déjà utilisé, rôle invalide ou mot de passe < 10 caractères.")
        else:
            ex("INSERT INTO users(email,name,role,pw) VALUES(?,?,?,?)", em, f["name"], f["role"], generate_password_hash(f["password"]))
            log("Utilisateur créé", em)
    return render_template("users.html", rows=q("SELECT * FROM users ORDER BY name"))

@app.route("/users/<int:uid>/toggle", methods=["POST"])
@need()
def user_toggle(uid):
    if uid != g.user["id"]: ex("UPDATE users SET active=1-active WHERE id=?", uid)
    return redirect(url_for("users"))

@app.route("/password", methods=["GET", "POST"])
def password():
    if request.method == "POST":
        if check_password_hash(g.user["pw"], request.form["old"]) and len(request.form["new"]) >= 10:
            ex("UPDATE users SET pw=? WHERE id=?", generate_password_hash(request.form["new"]), g.user["id"])
            flash("Mot de passe modifié."); return redirect(url_for("dashboard"))
        flash("Ancien mot de passe faux ou nouveau < 10 caractères.")
    return render_template("password.html")

with sqlite3.connect(DATA + "/tax.db") as _c:
    _c.executescript(SCHEMA)
