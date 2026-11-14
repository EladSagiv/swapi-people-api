"""
test_api.py - בדיקות אוטומטיות (מריצים עם:  pytest).

כל הבדיקות רצות בלי אינטרנט: במקום SWAPI אמיתי משתמשים בתשובות מזויפות
(MockTransport / monkeypatch). זה מהיר, יציב, ומאפשר לדמות תקלות כמו "SWAPI נפל".

מה נבדק:
  - חילוץ id מה-url
  - pagination, ושה-id נלקח מה-url ולא ממיקום
  - retry על שגיאה זמנית, ואי-retry על שגיאת לקוח
  - 502 כש-SWAPI לא זמין
  - תשובה תקינה מה-endpoint
  - אכיפת API key
"""
import httpx
import pytest
from fastapi.testclient import TestClient

from app import swapi
from app.main import app, cache


def person(pid: int) -> dict:
    """בונה אדם מזויף עם ה-url הנכון, בפורמט שדומה ל-SWAPI."""
    return {"name": f"Person {pid}", "url": f"https://swapi.dev/api/people/{pid}/", "films": []}


def mock_client(handler) -> httpx.Client:
    """בונה httpx.Client שלא יוצא לרשת: כל בקשה עוברת לפונקציה handler שאנחנו כותבים."""
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    """רצה אוטומטית לפני כל בדיקה, כדי שהבדיקות לא ישפיעו אחת על השנייה."""
    cache.clear()  # מתחילים עם cache ריק
    monkeypatch.setattr(swapi.time, "sleep", lambda _s: None)  # מדלגים על ההמתנה של ה-retry
    monkeypatch.delenv("API_KEY", raising=False)  # ברירת מחדל: בלי אימות


def test_extract_id():
    """מוודא שה-id נשלף נכון (עם ובלי סלאש בסוף), ושכתובת שגויה נדחית."""
    assert swapi.extract_id("https://swapi.dev/api/people/10/") == 10
    assert swapi.extract_id("https://swapi.dev/api/people/83") == 83
    with pytest.raises(swapi.UpstreamError):
        swapi.extract_id("https://swapi.dev/api/planets/1/")


def test_follows_pagination_and_id_comes_from_url_not_position():
    """שני עמודים: ids 1,2 ואז 18. מוכיח שהלולאה עוברת על שני העמודים,
    ושה-id של האדם השלישי הוא 18 (מה-url) ולא 3 (מיקום), כמו הפער האמיתי ב-SWAPI."""
    def handler(request: httpx.Request) -> httpx.Response:
        if "page=2" in str(request.url):
            return httpx.Response(200, json={"next": None, "results": [person(18)]})
        return httpx.Response(
            200, json={"next": "https://swapi.dev/api/people/?page=2", "results": [person(1), person(2)]}
        )

    people = swapi.fetch_all_people(mock_client(handler))
    assert [p["id"] for p in people] == [1, 2, 18]
    assert people[0]["name"] == "Person 1"  # השדות המקוריים נשמרים


def test_retries_transient_error_then_succeeds():
    """שגיאה 503 פעמיים ואז הצלחה: מוודא שמנסים שוב, ושנעשו בדיוק 3 קריאות."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"next": None, "results": [person(1)]})

    assert len(swapi.fetch_all_people(mock_client(handler))) == 1
    assert calls["n"] == 3


def test_does_not_retry_client_error():
    """שגיאה 404 היא קבועה: מוודא שנזרקת UpstreamError אחרי קריאה אחת בלבד, בלי retry."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404)

    with pytest.raises(swapi.UpstreamError):
        swapi.fetch_all_people(mock_client(handler))
    assert calls["n"] == 1


def test_endpoint_returns_502_when_swapi_is_down(monkeypatch):
    """מדמה SWAPI שנופל: ה-endpoint צריך להחזיר 502 ולא קריסה (500) או רשימה חלקית."""
    def boom():
        raise swapi.UpstreamError("down")

    monkeypatch.setattr(swapi, "fetch_all_people", boom)
    client = TestClient(app)
    response = client.get("/people")
    assert response.status_code == 502


def test_endpoint_returns_people_with_id(monkeypatch):
    """מסלול תקין: ה-endpoint מחזיר 200 ואת הרשימה כמו שה-loader החזיר אותה."""
    monkeypatch.setattr(swapi, "fetch_all_people", lambda: [{"id": 1, "name": "Luke"}])
    response = TestClient(app).get("/people")
    assert response.status_code == 200
    assert response.json() == [{"id": 1, "name": "Luke"}]


def test_api_key_enforced_when_configured(monkeypatch):
    """עם API_KEY מוגדר: בלי מפתח או עם מפתח שגוי -> 401, עם מפתח נכון -> 200.
    וגם: /health נשאר פתוח. (הערך dummy-key הוא ערך דמה לבדיקה בלבד.)"""
    monkeypatch.setenv("API_KEY", "dummy-key")
    monkeypatch.setattr(swapi, "fetch_all_people", lambda: [])
    client = TestClient(app)
    assert client.get("/people").status_code == 401
    assert client.get("/people", headers={"X-API-Key": "wrong"}).status_code == 401
    assert client.get("/people", headers={"X-API-Key": "dummy-key"}).status_code == 200
    assert client.get("/health").status_code == 200  # health נשאר פתוח
