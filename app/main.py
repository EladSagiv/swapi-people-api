"""
main.py - שכבת ה-API (מה שהעולם החיצוני רואה).

הקובץ הזה מגדיר את שרת ה-FastAPI ואת ה-endpoints שלו. הוא לא יודע איך מושכים
נתונים מ-SWAPI - את זה עושה swapi.py. כאן יש רק:
  1. הגדרת האפליקציה והרכיב שמחזיק cache.
  2. אימות (API key).
  3. שני endpoints:  GET /health  ו-  GET /people.

זרימה של בקשה ל-/people:
  Workato  ->  require_api_key (אימות)  ->  get_people  ->  cache  ->  swapi.fetch_all_people  ->  swapi.dev
"""
import hmac
import os

from fastapi import Depends, FastAPI, Header, HTTPException

from . import swapi

# אובייקט האפליקציה. uvicorn מריץ אותו בפקודה:  uvicorn app.main:app
# ה-title והגרסה מופיעים אוטומטית בדף התיעוד שנוצר ב-/docs.
app = FastAPI(title="SWAPI People API", version="1.0.0")

# cache אחד לכל התהליך (נוצר פעם אחת בעליית השרת). הוא שומר את רשימת האנשים
# ל-5 דקות, כך שקריאות חוזרות לא יפנו שוב ל-SWAPI (שדורש 9 קריאות לכל הרשימה).
cache = swapi.PeopleCache()


# ---------------------------------------------------------------------------
# פונקציה 1: require_api_key - אימות
# ---------------------------------------------------------------------------
def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """בודקת שהקורא שלח את מפתח ה-API הנכון ב-header בשם `X-API-Key`.

    איך זה עובד:
      - המפתח הצפוי נקרא ממשתנה הסביבה API_KEY (לא נכתב בקוד, כדי לא לחשוף סודות).
      - אם API_KEY לא הוגדר (למשל בפיתוח מקומי) - אין אימות והכול פתוח.
      - אם הוגדר ולא נשלח מפתח, או שהמפתח שגוי - מחזירים 401 Unauthorized.

    FastAPI מזריק את ה-header לפרמטר x_api_key אוטומטית (מקף הופך לקו תחתון).
    הפונקציה משמשת כ-dependency של /people: היא רצה לפני הקוד של ה-endpoint,
    וזריקת HTTPException עוצרת את הבקשה לפני שמגיעים ל-SWAPI.
    """
    expected = os.getenv("API_KEY")
    if not expected:
        return  # מצב פיתוח: אין מפתח מוגדר, לא דורשים אימות
    # hmac.compare_digest משווה בזמן קבוע, כדי שלא יהיה אפשר לנחש מפתח לפי זמן התגובה
    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


# ---------------------------------------------------------------------------
# פונקציה 2: health - בדיקת חיים
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    """מחזירה {"status": "ok"} כדי לוודא שהשרת חי.

    בכוונה בלי אימות ובלי פנייה ל-SWAPI: היא בודקת רק שהשירות שלנו רץ.
    פלטפורמות פריסה (כמו Render) משתמשות ב-endpoint כזה כדי לדעת שהשירות תקין.
    """
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# פונקציה 3: get_people - ה-endpoint המרכזי של המשימה
# ---------------------------------------------------------------------------
@app.get("/people", dependencies=[Depends(require_api_key)])
def get_people() -> list[dict]:
    """מחזירה את כל ה-people מ-swapi.dev, ולכל אחד שדה `id` מספרי שנוסף.

    תשובה: מערך JSON של 82 אובייקטים. כל אובייקט הוא הרשומה המקורית של SWAPI
    ועוד השדה `id` (לדוגמה: {"id": 1, "name": "Luke Skywalker", ...}).

    שלבים:
      1. require_api_key רץ קודם (דרך dependencies) ומאמת את הקורא.
      2. cache.get בודק אם יש רשימה טרייה בזיכרון. אם כן - מחזיר אותה מיד.
         אם לא - קורא ל-swapi.fetch_all_people, ששולף את כל העמודים ומוסיף id.
      3. אם SWAPI נכשל גם אחרי ניסיונות חוזרים - מחזירים 502 Bad Gateway
         (השגיאה היא אצל השרת שמעלינו, לא אצל הקורא), ולא רשימה חלקית.

    שימו לב: ה-loader מועבר במפורש (swapi.fetch_all_people) כדי שבבדיקות
    אפשר יהיה להחליף אותו בפונקציה מזויפת בלי לגעת באינטרנט.
    """
    try:
        return cache.get(swapi.fetch_all_people)
    except swapi.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
