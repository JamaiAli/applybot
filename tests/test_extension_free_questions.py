"""Extension : les questions libres d'un formulaire (y compris en Web Components, comme les
« Questions préliminaires » de SmartRecruiters) sont remplies via /api/smart-answer."""
import json
from pathlib import Path

import pytest

from applypilot.tracking import tracer

AUTOFILL_JS = Path(__file__).resolve().parents[1] / "src" / "applypilot" / "apply" / "extension" / "autofill.js"

# Page d'essai : quatre questions, chacune dans un composant à shadow root, le libellé
# tantôt dans le shadow root, tantôt hors de lui ; plus un champ « Ville » sans question.
PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Final Year Internship - Sia</title></head>
<body><h1>Questions préliminaires</h1><form id="f"></form>
<script>
const QUESTIONS = [
  ["textarea", "Quelle est votre date de disponibilité estimée ?", 1440, "inner"],
  ["input", "Quel est votre plus haut niveau de diplôme obtenu ?", 200, "outer"],
  ["input", "Combien d'années d'expérience professionnelle avez-vous (hors stages et alternance) ?", 200, "inner"],
  ["textarea", "Comment avez-vous entendu parler de Sia ?", 1440, "outer"],
];
class SplField extends HTMLElement {
  connectedCallback() {
    const [tag, label, max, where] = JSON.parse(this.dataset.q);
    const root = this.attachShadow({ mode: "open" });
    const field = document.createElement(tag);
    if (tag === "input") field.type = "text";
    field.maxLength = max; field.id = "field";
    field.style.cssText = "display:block;width:600px;height:" + (tag === "textarea" ? 90 : 30) + "px";
    field.addEventListener("input", () => { this.dataset.value = field.value; });
    if (where === "inner") {
      const l = document.createElement("label"); l.htmlFor = "field"; l.textContent = label + " *";
      root.append(l);
    }
    const wrap = document.createElement("div"); wrap.append(field);
    const counter = document.createElement("span"); counter.textContent = "0/" + max;
    root.append(wrap, counter);
  }
}
customElements.define("spl-field", SplField);
const form = document.getElementById("f");
for (const q of QUESTIONS) {
  const block = document.createElement("div");
  if (q[3] === "outer") { const p = document.createElement("p"); p.textContent = q[1] + " *"; block.append(p); }
  const el = document.createElement("spl-field"); el.dataset.q = JSON.stringify(q); block.append(el);
  form.append(block);
}
const city = document.createElement("div");
city.innerHTML = '<label for="c">Ville</label><input id="c" type="text" value="" style="width:300px;height:30px">';
form.append(city);
// Faux « chrome » : capte l'écouteur de l'extension, et répond aux questions.
window.__asked = [];
window.chrome = { runtime: {
  onMessage: { addListener: (fn) => { window.__listener = fn; } },
  sendMessage: (msg, cb) => {
    if (msg.action === "smart_answer") {
      window.__asked.push({ question: msg.question, max: msg.max_length });
      cb({ ok: true, data: { answer: "REPONSE:" + msg.question.slice(0, 30), source: "ai" } });
    } else { cb({ ok: false }); }
  },
} };
</script></body></html>"""

CANDIDATE = {"personal": {"first_name": "Ali", "last_name": "Jamai", "full_name": "Ali Jamai",
                          "email": "a@example.com", "phone": "+33 6 00 00 00 00", "city": "Bourges"},
             "education": {"school": "INSA Centre Val de Loire"}, "qa": {}, "has_cv": False}


@pytest.fixture(scope="module")
def browser():
    sync_api = pytest.importorskip("playwright.sync_api")
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # navigateur Playwright non installé sur cette machine
            pytest.skip(f"Chromium Playwright indisponible : {exc}")
        yield b
        b.close()


def _autofill(browser, html):
    """Charge la page, injecte le script de l'extension et lance le remplissage."""
    pg = browser.new_page()
    pg.set_content(html)
    pg.add_script_tag(content=AUTOFILL_JS.read_text(encoding="utf-8"))
    result = pg.evaluate(
        "data => new Promise(resolve => window.__listener({action: 'autofill', data}, null, resolve))",
        CANDIDATE)
    return pg, result


@pytest.fixture(scope="module")
def page(browser):
    pg, _ = _autofill(browser, PAGE)
    return pg


def test_every_free_question_inside_shadow_roots_is_filled(page):
    values = page.evaluate("[...document.querySelectorAll('spl-field')].map(e => e.dataset.value || '')")
    assert len(values) == 4 and all(v.startswith("REPONSE:") for v in values), values


def test_the_question_sent_to_the_llm_is_the_clean_label(page):
    asked = page.evaluate("window.__asked")
    assert [a["question"] for a in asked] == [
        "Quelle est votre date de disponibilité estimée ?",
        "Quel est votre plus haut niveau de diplôme obtenu ?",
        "Combien d'années d'expérience professionnelle avez-vous (hors stages et alternance) ?",
        "Comment avez-vous entendu parler de Sia ?",
    ]
    assert [a["max"] for a in asked] == [1440, 200, 200, 1440]     # taille du champ transmise


def test_a_plain_field_without_question_is_not_sent_to_the_llm(page):
    # « Ville » est rempli par les règles classiques, jamais par une phrase du LLM.
    assert page.evaluate("document.getElementById('c').value") == "Bourges"


# Première page d'un formulaire SmartRecruiters : identité, chaque champ dans son composant.
IDENTITY_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>Candidature</title></head><body>
<form id="f"></form><script>
class SplInput extends HTMLElement {
  connectedCallback() {
    const root = this.attachShadow({ mode: "open" });
    const l = document.createElement("label"); l.htmlFor = "i"; l.textContent = this.getAttribute("label") + "*";
    const i = document.createElement("input"); i.id = "i"; i.type = this.getAttribute("kind") || "text";
    i.style.cssText = "display:block;width:400px;height:30px";
    i.addEventListener("input", () => { this.dataset.value = i.value; });
    root.append(l, i);
  }
}
customElements.define("spl-input", SplInput);
for (const [label, kind] of [["Prénom", "text"], ["Nom", "text"], ["E-mail", "email"],
                             ["Confirmez votre e-mail", "email"], ["Numéro de téléphone", "tel"]]) {
  const el = document.createElement("spl-input"); el.setAttribute("label", label); el.setAttribute("kind", kind);
  document.getElementById("f").append(el);
}
window.chrome = { runtime: { onMessage: { addListener: (fn) => { window.__listener = fn; } },
                             sendMessage: (msg, cb) => cb({ ok: false }) } };
</script></body></html>"""


def test_identity_fields_inside_shadow_roots_are_filled(browser):
    pg, result = _autofill(browser, IDENTITY_PAGE)
    values = pg.evaluate("[...document.querySelectorAll('spl-input')].map(e => e.dataset.value || '')")
    # Les valeurs viennent du profil (celui passé ici, ou celui du bot local s'il tourne).
    first, last, email, confirm, phone = values
    assert (first, last) == ("Ali", "Jamai"), values
    assert "@" in email and confirm == email, values
    assert phone.startswith("+33"), values
    assert result["filledCount"] == 5


# --- Serveur : réponses aux questions préliminaires ---------------------------

@pytest.fixture
def no_llm(monkeypatch):
    from applypilot import autoapply
    monkeypatch.setattr(autoapply, "_qa_knowledge", lambda: {})

    def boom(*a, **k):
        raise AssertionError("le LLM ne doit pas être appelé pour une question de la banque")
    monkeypatch.setattr("applypilot.llm.get_client", boom)


@pytest.mark.parametrize("question, expected", [
    ("Quelle est votre date de disponibilité estimée ?", "avril 2027"),
    ("Quel est votre plus haut niveau de diplôme obtenu ?", "Baccalauréat"),
    ("Combien d'années d'expérience professionnelle avez-vous (hors stages et alternance) ?", "0"),
    ("Comment avez-vous entendu parler de Sia ?", "plateforme"),
])
def test_preliminary_questions_are_answered_from_the_bank(no_llm, question, expected):
    answer, source = tracer.smart_answer(question)
    assert source == "qa_bank" and expected in answer


def test_answer_never_exceeds_the_field_length(no_llm):
    answer, _ = tracer.smart_answer("Quel est votre plus haut niveau de diplôme obtenu ?", max_length=40)
    assert 0 < len(answer) <= 40 and not answer.endswith(" ")


def test_motivation_question_is_not_answered_with_an_unrelated_bank_entry(monkeypatch):
    # Une clé de la banque partage le mot « stage » avec la question : ce n'est pas une raison
    # pour répondre par la disponibilité à une question de motivation.
    from applypilot import autoapply
    monkeypatch.setattr(autoapply, "_qa_knowledge", lambda: {"disponibilite_stage": "Disponible le 5 avril 2027.",
                                                              "duree_stage": "4 à 6 mois"})

    class FakeClient:
        def chat(self, messages, max_tokens=0):
            return "Réponse de motivation."
    monkeypatch.setattr("applypilot.llm.get_client", lambda *a, **k: FakeClient())
    answer, source = tracer.smart_answer("Pourquoi souhaitez-vous rejoindre Sia pour ce stage en cybersécurité ?")
    assert (answer, source) == ("Réponse de motivation.", "ai")


def test_open_question_goes_to_the_llm_with_real_facts_only(monkeypatch):
    from applypilot import autoapply
    monkeypatch.setattr(autoapply, "_qa_knowledge", lambda: {})
    seen = {}

    class FakeClient:
        def chat(self, messages, max_tokens=0):
            seen["system"], seen["user"] = messages[0]["content"], messages[1]["content"]
            return ' « Réponse rédigée. » '
    monkeypatch.setattr("applypilot.llm.get_client", lambda *a, **k: FakeClient())

    answer, source = tracer.smart_answer("Pourquoi souhaitez-vous rejoindre notre cabinet ?", "Consultant", "Sia", 300)
    assert (answer, source) == ("Réponse rédigée.", "ai")
    assert "N'invente" in seen["system"] and "300 caractères" in seen["system"]
    assert "FAITS RÉELS DU CANDIDAT" in seen["system"] and "INSA" in seen["system"]
    assert "Sia" in seen["user"]
