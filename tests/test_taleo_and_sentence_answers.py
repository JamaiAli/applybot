import pytest
from aiohttp.test_utils import TestClient, TestServer

from applypilot import autoapply
from applypilot.tracking.tracer import create_tracer_app, get_candidate_data


def test_candidate_data_has_education_and_nationality():
    data = get_candidate_data()
    assert data["status"] == "ok"
    assert data["personal"]["nationality"] == "Marocaine"
    assert "education" in data
    assert "INSA" in data["education"]["school"]
    assert "2023" in data["education"]["start_date"]
    assert "2028" in data["education"]["end_date"]


def test_autoapply_field_values_sentences():
    p = autoapply._profile()
    pairs = autoapply._field_values(p)

    def find_match(label: str) -> str | None:
        for pat, val in pairs:
            if pat.search(label):
                return val
        return None

    # 1. Établissement
    ans1 = find_match("1. Dans quel établissement êtes-vous inscrit ? *")
    assert ans1 is not None
    assert "INSA Centre Val de Loire" in ans1

    # 2. Date début stage (phrase complète)
    ans2 = find_match("2. A partir de quand êtes-vous disponible pour le début du stage ? *")
    assert ans2 is not None
    assert "avril 2027" in ans2 and "disponible" in ans2

    # 3. Jusqu'à quand démarrage (phrase complète)
    ans3 = find_match("3. Jusqu'à quand êtes-vous disponible pour le démarrage de votre stage ? *")
    assert ans3 is not None
    assert "septembre 2027" in ans3 and "démarrage" in ans3

    # 4. Type de stage
    ans4 = find_match("4. De quel type de stage s'agit-il? *")
    assert ans4 is not None
    assert "Master 1" in ans4 or "Césure" in ans4

    # 5. Convention de stage
    ans5 = find_match("5. Disposerez-vous d'une convention pour toute la durée de votre stage ? *")
    assert ans5 is not None
    assert "Oui" in ans5 and "convention" in ans5


@pytest.mark.anyio
async def test_smart_answer_full_sentences_endpoint():
    app = create_tracer_app()
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()

    try:
        # Question 1: Établissement
        r1 = await client.post("/api/smart-answer", json={"question": "1. Dans quel établissement êtes-vous inscrit ? *"})
        d1 = await r1.json()
        assert "INSA Centre Val de Loire" in d1["answer"]

        # Question 2: À partir de quand disponible (phrase)
        r2 = await client.post("/api/smart-answer", json={"question": "2. A partir de quand êtes-vous disponible pour le début du stage ? *"})
        d2 = await r2.json()
        assert "disponible" in d2["answer"] and "avril 2027" in d2["answer"]

        # Question 3: Jusqu'à quand disponible (phrase)
        r3 = await client.post("/api/smart-answer", json={"question": "3. Jusqu'à quand êtes-vous disponible pour le démarrage de votre stage ? *"})
        d3 = await r3.json()
        assert "septembre 2027" in d3["answer"] and "démarrage" in d3["answer"]

        # Question 4: Type de stage
        r4 = await client.post("/api/smart-answer", json={"question": "4. De quel type de stage s'agit-il? *"})
        d4 = await r4.json()
        assert "Master 1" in d4["answer"] or "Césure" in d4["answer"]

        # Question 5: Convention de stage (phrase)
        r5 = await client.post("/api/smart-answer", json={"question": "5. Disposerez-vous d'une convention pour toute la durée de votre stage ? *"})
        d5 = await r5.json()
        assert "Oui" in d5["answer"] and "convention" in d5["answer"]

    finally:
        await client.close()
