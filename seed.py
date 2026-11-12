"""Seed : comptes + données fictives. Usage: python seed.py  (affiche les mots de passe générés une seule fois)"""
import secrets, sqlite3
from werkzeug.security import generate_password_hash
import app as A

c = sqlite3.connect(A.DATA + "/tax.db")
if c.execute("SELECT COUNT(*) FROM users").fetchone()[0]:
    raise SystemExit("déjà initialisé")
users = [("anis@tax.local", "Anis (Admin)", "admin"), ("taxmanager@tax.local", "Tax Manager", "tax"), ("comptable@tax.local", "Responsable Comptable", "rc")]
for em, name, role in users:
    pw = secrets.token_urlsafe(12)
    c.execute("INSERT INTO users(email,name,role,pw) VALUES(?,?,?,?)", (em, name, role, generate_password_hash(pw)))
    print(em, pw)
co = [("Alpha Industries SAS", "812345671", "31/12", "Intégration fiscale (mère)", 50000), ("Beta Services SARL", "823456782", "31/12", "IS - réel normal", 120000),
      ("Gamma Distribution SA", "834567893", "30/06", "IS - réel normal", -30000), ("Delta Logistique SAS", "845678904", "31/12", "IS - réel simplifié", 8000),
      ("Epsilon Tech SAS", "856789015", "31/12", "Intégration fiscale (filiale)", 210000), ("Zeta Immobilier SCI-IS", "867890126", "31/12", "IS - réel simplifié", 15000)]
for i, (n, s, cl, rg, res) in enumerate(co, 1):
    c.execute("INSERT INTO companies(id,name,siren,closing,regime) VALUES(?,?,?,?,?)", (i, n, s, cl, rg))
    c.execute("INSERT INTO reviews(id,company_id,year,due,accounting_result) VALUES(?,?,?,?,?)", (i, i, 2026, "2027-05-15", res))
P = [  # review, sujet, cat, compte, montant, risque, question, base légale, conclusion, deduct, adj, adj_amt, nature, statut
    (1, "Amendes et pénalités", "charge", "6712", 12000, "Faible", "Les pénalités de retard sont-elles déductibles ?", "CGI art. 39-2 : amendes et pénalités non déductibles", "Non déductible, à réintégrer.", "Non déductible", "reint", 12000, "Permanente", "certified"),
    (1, "Cadeaux clients > 73 € TTC", "charge", "6234", 8500, "Moyen", "Quelle part des cadeaux est déductible, et la TVA récupérable ?", "CGI art. 39-1-7° ; BOI-BIC-CHG-40-20-40", "Déductibilité limitée aux cadeaux justifiés par l'intérêt de l'exploitation ; registre des bénéficiaires requis.", "Partiellement déductible", "reint", 3200, "Permanente", "submitted"),
    (1, "Provision pour litige prud'homal", "charge", "6815", 90000, "Élevé", "La provision est-elle déductible à la clôture ?", "CGI art. 39-1-5° ; BOI-BIC-PROV-20", "", "À définir", "none", 0, "Temporaire", "draft"),
    (2, "Frais de réception du dirigeant", "charge", "6257", 15000, "Élevé", "Les frais de réception sont-ils exposés dans l'intérêt de l'exploitation ?", "CGI art. 39-1 ; art. 39-4 (dépenses somptuaires)", "Réintégration de la part somptuaire.", "Partiellement déductible", "reint", 6000, "Permanente", "submitted"),
    (2, "Quote-part de frais et charges sur dividendes", "produit", "7612", 40000, "Faible", "Régime mère-fille applicable ?", "CGI art. 145 et 216", "Déduction 95 %, réintégration QPFC 5 %.", "Déductible", "deduc", 38000, "Permanente", "rejected"),
    (3, "Intérêts de comptes courants d'associés", "charge", "6615", 22000, "Moyen", "Plafonnement du taux d'intérêt déductible", "CGI art. 39-1-3°", "Réintégration de l'excédent sur le taux plafond.", "Partiellement déductible", "reint", 4500, "Permanente", "draft"),
    (5, "Amortissement dérogatoire", "charge", "6872", 60000, "Moyen", "Traitement fiscal et impôt différé", "CGI art. 39 B ; art. 39 quinquies", "Retraitement et constatation d'un impôt différé passif.", "Déductible", "none", 60000, "Temporaire", "draft")]
for i, (rv, sj, cat, ac, am, rk, qu, lb, cc, dd, at, aa, dn, st) in enumerate(P, 1):
    c.execute("""INSERT INTO positions(review_id,subject,tax_type,category,account,amount,risk,question,facts,legal_basis,conclusion,deductibility,adj_type,adj_amount,diff_nature,rate,status,version,owner_id,created,updated,final_treatment,certified_by,certified_at,rc_note)
      VALUES(?,?,'IS',?,?,?,?,?, '',?,?,?,?,?,?,25,?,1,2,'2026-11-01 10:00','2026-11-01 10:00',?,?,?,?)""",
              (rv, sj, cat, ac, am, rk, qu, lb, cc, dd, at, aa, dn, st, "2058-A, ligne WE" if st == "certified" else None,
               3 if st in ("certified", "rejected") else None, "2026-11-02 09:30" if st in ("certified", "rejected") else None,
               "Merci de justifier le taux de QPFC retenu avant certification." if st == "rejected" else None))
c.commit(); c.close()
