"""
swapi.py - שכבת הנתונים (כל ההתעסקות מול swapi.dev).

הקובץ הזה לא מכיר את FastAPI. הוא אחראי על:
  1. למשוך את כל ה-people מ-SWAPI, עמוד אחרי עמוד (pagination).
  2. להוסיף לכל אדם שדה `id`.
  3. להתמודד עם תקלות (ניסיונות חוזרים, הגנה מלולאה אינסופית).
  4. לשמור את התוצאה בזיכרון זמן קצר (cache).

הפרדה בין main.py (HTTP, אימות) ל-swapi.py (לוגיקת נתונים) מאפשרת לבדוק כל חלק לחוד.
"""
import re
import threading
import time

import httpx

# ---------------------------------------------------------------------------
# קבועים
# ---------------------------------------------------------------------------
SWAPI_PEOPLE_URL = "https://swapi.dev/api/people/"  # כתובת העמוד הראשון
CACHE_TTL_SECONDS = 300  # כמה זמן הרשימה נשמרת בזיכרון: 5 דקות
MAX_PAGES = 50  # הגנה: אם `next` מצביע בלולאה, לא נרוץ לנצח (בפועל יש 9 עמודים)
MAX_ATTEMPTS = 3  # כמה פעמים מנסים כל בקשה לפני שמוותרים

# ביטוי רגולרי שתופס את המספר בסוף כתובת של אדם:  .../people/10/  ->  "10"
_ID_RE = re.compile(r"/people/(\d+)/?$")


class UpstreamError(Exception):
    """שגיאה מוגדרת משלנו: SWAPI לא נגיש או החזיר משהו שאי אפשר להשתמש בו.

    main.py תופס אותה ומתרגם ל-HTTP 502. שימוש בשגיאה ייעודית מבדיל בין
    "SWAPI נפל" לבין באג רגיל בקוד שלנו.
    """


# ---------------------------------------------------------------------------
# פונקציה: extract_id - חילוץ ה-id
# ---------------------------------------------------------------------------
def extract_id(url: str) -> int:
    """'https://swapi.dev/api/people/10/'  ->  10

    ל-SWAPI אין שדה id, אבל המספר נמצא בסוף ה-url של כל אדם.
    לוקחים אותו משם ולא ממיקום ברשימה, כי ה-ids לא רציפים: ב-SWAPI אין אדם
    עם id 17, אז אדם שני-אחרי-16 הוא 18 ולא 17. (נבדק מול ה-API האמיתי.)
    אם ה-url לא בפורמט הצפוי - זורקים UpstreamError ולא ממציאים id.
    """
    match = _ID_RE.search(url or "")
    if not match:
        raise UpstreamError(f"Cannot extract id from person url: {url!r}")
    return int(match.group(1))


# ---------------------------------------------------------------------------
# פונקציה: _get_page - שליפת עמוד אחד, עם retry (פנימית, מסומנת בקו תחתון)
# ---------------------------------------------------------------------------
def _get_page(client: httpx.Client, url: str) -> dict:
    """מביאה עמוד JSON אחד מ-SWAPI ומנסה שוב אם התקלה זמנית.

    ההבחנה העיקרית:
      - שגיאה זמנית (5xx, 429 "יותר מדי בקשות", timeout, בעיית רשת) -> מנסים שוב,
        עם המתנה שגדלה (0.5 שנייה, ואז 1 שנייה). יש סיכוי שזה יעבור.
      - שגיאת לקוח (למשל 404) -> לא מנסים שוב. הכתובת לא תתחיל להתקיים,
        וניסיון חוזר רק מבזבז זמן.
    אחרי MAX_ATTEMPTS ניסיונות כושלים זורקים UpstreamError.
    """
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.get(url)
            # 5xx / 429 הן שגיאות זמניות: מסמנים אותן כשגיאה כדי שייכנסו למנגנון הניסיון החוזר
            if response.status_code >= 500 or response.status_code == 429:
                raise httpx.HTTPStatusError(
                    f"transient status {response.status_code}", request=response.request, response=response
                )
            response.raise_for_status()  # שאר שגיאות 4xx נזרקות כאן
            return response.json()
        except httpx.HTTPStatusError as exc:
            last_error = exc
            # שגיאת לקוח קבועה (4xx חוץ מ-429): יוצאים מהלולאה מיד, בלי retry
            if exc.response.status_code < 500 and exc.response.status_code != 429:
                break
        except (httpx.TransportError, ValueError) as exc:
            # TransportError = timeout / אין חיבור; ValueError = התשובה אינה JSON תקין
            last_error = exc
        if attempt < MAX_ATTEMPTS:
            time.sleep(0.5 * attempt)  # backoff: ממתינים יותר בכל ניסיון
    raise UpstreamError(f"SWAPI request failed for {url}: {last_error}")


# ---------------------------------------------------------------------------
# פונקציה: fetch_all_people - הלב של הלוגיקה
# ---------------------------------------------------------------------------
def fetch_all_people(client: httpx.Client | None = None) -> list[dict]:
    """מחזירה רשימה של כל האנשים (82), כל אחד עם `id`.

    Pagination: SWAPI מחזיר 10 אנשים לעמוד, ובכל עמוד שדה `next` עם הכתובת של
    העמוד הבא (או null בעמוד האחרון). הלולאה הולכת אחרי `next` עד שהוא null,
    ולכן לא מניחים מספר עמודים קבוע.

    פרמטר client: בדרך כלל לא מעבירים. בבדיקות מעבירים client מזויף (MockTransport)
    כדי לא לפנות לאינטרנט. אם לא הועבר - יוצרים client משלנו וסוגרים אותו בסוף
    (owns_client מסמן אם הוא שלנו).

    {"id": ..., **person} שם את id ראשון ושומר את כל שאר השדות המקוריים כמו שהם.
    """
    owns_client = client is None
    client = client or httpx.Client(timeout=10.0, follow_redirects=True)
    try:
        people: list[dict] = []
        url: str | None = SWAPI_PEOPLE_URL
        pages = 0
        while url:
            pages += 1
            if pages > MAX_PAGES:
                raise UpstreamError("Too many pages from SWAPI; aborting")
            page = _get_page(client, url)
            for person in page.get("results", []):
                people.append({"id": extract_id(person.get("url", "")), **person})
            url = page.get("next")  # None בעמוד האחרון -> הלולאה נגמרת
        return people
    finally:
        # ה-finally מבטיח שה-client ייסגר גם אם הייתה שגיאה באמצע
        if owns_client:
            client.close()


# ---------------------------------------------------------------------------
# מחלקה: PeopleCache - זיכרון זמני
# ---------------------------------------------------------------------------
class PeopleCache:
    """cache פשוט בזיכרון עם תפוגה (TTL), כדי לא לפנות ל-SWAPI בכל בקשה.

    בלי cache כל קריאה ל-/people הייתה גוררת 9 קריאות ל-SWAPI. עם cache, רק
    הקריאה הראשונה (ואחת כל 5 דקות) יקרה. הנתונים של SWAPI כמעט לא משתנים.
    הוא חי בזיכרון של התהליך, ולכן מתאפס בכל הפעלה מחדש של השרת.
    """

    def __init__(self, ttl: float = CACHE_TTL_SECONDS):
        self._ttl = ttl  # כמה שניות הערך תקף
        # lock: השרת מטפל בכמה בקשות במקביל. הנעילה מונעת ששתי בקשות
        # ימשכו מ-SWAPI באותו זמן וידרסו אחת את השנייה.
        self._lock = threading.Lock()
        self._value: list[dict] | None = None  # הרשימה השמורה (None = ריק)
        self._expires = 0.0  # מתי הערך פג תוקף

    def get(self, loader=fetch_all_people) -> list[dict]:
        """מחזירה את הרשימה מהזיכרון, או טוענת אותה מחדש אם ריק או פג תוקף.

        loader הוא הפונקציה שיודעת לטעון את הנתונים (ברירת מחדל: fetch_all_people).
        אם הטעינה נכשלת, השגיאה עולה החוצה ושום דבר לא נשמר, כך שהקריאה הבאה תנסה שוב.
        time.monotonic שעון שלא קופץ אחורה, מתאים למדידת משך זמן.
        """
        with self._lock:
            now = time.monotonic()
            if self._value is None or now >= self._expires:
                self._value = loader()
                self._expires = now + self._ttl
            return self._value

    def clear(self) -> None:
        """מרוקנת את ה-cache. משמשת בעיקר בבדיקות, כדי שכל בדיקה תתחיל נקי."""
        with self._lock:
            self._value = None
            self._expires = 0.0
