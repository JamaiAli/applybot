"""Validation des lettres de motivation en français.

Vérifie que validate_cover_letter accepte la salutation française
« Madame, Monsieur, » et rejette les clichés FR de lettre de motivation.
"""

from applypilot.scoring.validator import validate_cover_letter

LETTRE_FR = (
    "Madame, Monsieur,\n\n"
    "Votre équipe sécurité recrute un stagiaire pour renforcer l'administration "
    "de l'annuaire Active Directory et accompagner la migration vers Entra ID. "
    "L'offre insiste sur le durcissement des postes et la gestion des identités, "
    "deux sujets que j'ai travaillés en projet et en TP pendant mon cycle ingénieur.\n\n"
    "Lors d'un projet de formation, j'ai déployé une forêt Active Directory de test, "
    "configuré les stratégies de groupe et automatisé la création des comptes en "
    "PowerShell, ce qui a réduit le temps de provisionnement de moitié. J'ai aussi "
    "mis en place l'authentification multifacteur sur un tenant Microsoft 365 et "
    "rédigé la procédure pour mes camarades de promotion.\n\n"
    "Pour ce stage, je contribuerais à la fiabilisation de votre annuaire et à la "
    "bascule progressive vers Entra ID, avec des procédures claires et testées. Ce "
    "stage de six mois correspond à mon projet en intégration IT et cybersécurité, "
    "et me permettrait de progresser au contact de votre équipe.\n\n"
    "Je suis disponible à partir d'avril 2027 pour une durée de six mois et serais "
    "heureux d'échanger lors d'un entretien. Je vous prie d'agréer, Madame, Monsieur, "
    "l'expression de mes salutations distinguées.\n\n"
    "Prénom Nom"
)


def test_lettre_fr_valide_passe():
    result = validate_cover_letter(LETTRE_FR)
    assert result["passed"], result["errors"]


def test_salutation_fr_acceptee():
    result = validate_cover_letter(LETTRE_FR)
    assert not any("Madame" in e or "Dear" in e for e in result["errors"])


def test_cliche_fr_rejete():
    lettre = LETTRE_FR.replace("je contribuerais", "dynamique, je contribuerais")
    result = validate_cover_letter(lettre)
    assert not result["passed"]
    assert any("dynamique" in e for e in result["errors"])


def test_salutation_manquante_rejetee():
    lettre = "Bonjour,\n\n" + LETTRE_FR.split("\n\n", 1)[1]
    result = validate_cover_letter(lettre)
    assert any("Madame" in e for e in result["errors"])
