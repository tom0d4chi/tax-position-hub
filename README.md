# Tax Position Hub

MVP d'outil de workflow pour documenter des positions fiscales (déductibilité des charges et produits, retraitements, impôts différés) et les faire certifier par le responsable comptable.

- Rôles : Administrateur, Tax Manager, Responsable comptable, Lecteur
- Workflow : Brouillon → Soumise → Certifiée / Rejetée (toute modification d'une position certifiée crée une nouvelle version)
- Réconciliation résultat comptable → résultat fiscal, journal d'audit, pièces jointes, export Excel

Stack : Flask + SQLite, Docker.

    docker build -t tax-app .
    docker run -d -p 8000:8000 -v $(pwd)/data:/data tax-app
    docker exec <conteneur> python seed.py   # comptes + données fictives (mots de passe affichés une fois)

Si l'app est servie en HTTP simple (hors HTTPS), ajouter -e COOKIE_SECURE=0.
