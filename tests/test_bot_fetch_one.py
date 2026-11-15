"""« Préparer ma candidature » passe au moteur CV la ligne renvoyée par fetch_one :
elle doit contenir la description, sinon le CV est choisi sur le seul intitulé."""
from applypilot import bot_core, cv_latex

DESCRIPTION = ("Programmer des composants du logiciel. Technologies utilisées : Python et pytest, Go, git et "
               "GitLab, Docker, Kubernetes, SQLite, Linux. Sécurité des réseaux : pare-feu, VPN, routage, "
               "réseau mobile, réseaux télécom.")


def test_fetch_one_returns_the_description_the_cv_engine_needs(tmp_db, seed_job):
    conn = tmp_db()
    url = "https://example.com/offre/dev-securite"
    seed_job(conn, url=url, title="Stage - Développement de composants pour un outil de sécurité F/H",
             description=DESCRIPTION[:80], full_description=DESCRIPTION)

    row = bot_core.fetch_one(url)
    assert row["full_description"] == DESCRIPTION and row["description"]

    # Avec la description, la branche et les éléments à ajouter sont trouvés…
    assert cv_latex.offer_branch(row) == "Reseau"
    gaps = cv_latex.offer_gaps(row, "CV sans aucune de ces technologies", cv_latex.load_experiences())
    assert {"Python", "Docker", "SQLite"} <= set(gaps)
    # …alors que l'intitulé seul ne dit rien : générique, rien à ajouter.
    title_only = {"url": url, "title": row["title"]}
    assert cv_latex.offer_branch(title_only) == "Cyber"
    assert cv_latex.offer_gaps(title_only, "CV", cv_latex.load_experiences()) == []
