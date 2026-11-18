"""Client for the TNGF member portal and tee-time booking app.

Read-only for now: log in and load the slots for a date. See docs/site-flow.md.
"""
import json
import re
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

PORTAL = "https://mem.tngf.in/"
BOOKING = "https://booking.tngf.in/"
BOOKING_HOST = "booking.tngf.in"
COURSE_HEADERS = {"COURSE-ID": "1", "COURSE-NAME": "TamilNadu Golf Federation"}
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
TIMEOUT = 30
DEBUG_DIR = Path(__file__).resolve().parent.parent / "debug"

# Portal links worth trying when looking for the hand-off to the booking app.
HANDOFF_HINT = re.compile(r"tee|book|slot|golf", re.I)
# Never follow these while exploring the portal.
UNSAFE_LINK = re.compile(r"logout|logoff|signout|delete|remove|cancel|pay", re.I)


class TngfError(Exception):
    pass


def save_debug(name: str, content: str) -> Path:
    """Save a captured page for inspection. debug/ is gitignored."""
    DEBUG_DIR.mkdir(exist_ok=True)
    path = DEBUG_DIR / name
    path.write_text(content, encoding="utf-8")
    return path


def _handoff_params(url: str) -> Optional[dict]:
    """Return {token, MCODE} if url is the booking app's login hand-off link."""
    parsed = urlparse(url)
    if parsed.netloc != BOOKING_HOST:
        return None
    query = parse_qs(parsed.query)
    if "token" in query and "MCODE" in query:
        return {"token": query["token"][0], "MCODE": query["MCODE"][0]}
    return None


class TngfClient:
    def __init__(self) -> None:
        self.http = requests.Session()
        self.http.headers["User-Agent"] = USER_AGENT
        self.session_token: Optional[str] = None
        self.user: dict = {}

    # ---- member portal -------------------------------------------------

    def portal_login(self, member_id: str, password: str) -> str:
        """Log in to the member portal. Returns the HTML of the landing page."""
        page = self.http.get(PORTAL, timeout=TIMEOUT)
        page.raise_for_status()
        form = BeautifulSoup(page.text, "html.parser").find("form")
        if form is None:
            save_debug("portal_login_page.html", page.text)
            raise TngfError("No login form on the portal page (saved to debug/).")

        fields = {
            tag["name"]: tag.get("value", "")
            for tag in form.find_all("input", attrs={"type": "hidden"})
            if tag.get("name")
        }
        fields.update({"UserId": member_id, "Password": password, "RememberMe": "false"})

        resp = self.http.post(
            urljoin(PORTAL, form.get("action") or "/"),
            data=fields,
            headers={"Referer": PORTAL},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        save_debug("portal_after_login.html", resp.text)

        soup = BeautifulSoup(resp.text, "html.parser")
        if soup.find("input", attrs={"name": "Password"}) is not None:
            message = " ".join(
                el.get_text(" ", strip=True)
                for el in soup.select(".validation-summary-errors, .text-danger, #myMsg, .label-danger")
            ).strip()
            raise TngfError(
                "Portal login was not accepted"
                + (f": {message}" if message else " (still on the login page).")
            )
        return resp.text

    def find_handoff(self, landing_html: str) -> dict:
        """Find the booking app hand-off link (token + MCODE) in the portal."""
        soup = BeautifulSoup(landing_html, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            href = urljoin(PORTAL, a["href"])
            links.append((a.get_text(" ", strip=True), href))

        # 1. Direct link in the page, including inside scripts.
        for match in re.finditer(r"https?://booking\.tngf\.in/[^\s\"'<>]+", landing_html):
            params = _handoff_params(match.group(0).replace("&amp;", "&"))
            if params:
                return params

        # 2. A portal link that redirects to the booking app.
        candidates = [
            (text, href)
            for text, href in links
            if HANDOFF_HINT.search(text + " " + href)
            and not UNSAFE_LINK.search(text + " " + href)
            and urlparse(href).scheme in ("http", "https")
        ]
        for text, href in candidates:
            url = href
            for _ in range(6):  # follow redirects by hand so the token is never spent early
                params = _handoff_params(url)
                if params:
                    return params
                if urlparse(url).netloc != urlparse(PORTAL).netloc:
                    break
                resp = self.http.get(url, allow_redirects=False, timeout=TIMEOUT)
                if resp.is_redirect and resp.headers.get("Location"):
                    url = urljoin(url, resp.headers["Location"])
                    continue
                for match in re.finditer(r"https?://booking\.tngf\.in/[^\s\"'<>]+", resp.text):
                    params = _handoff_params(match.group(0).replace("&amp;", "&"))
                    if params:
                        return params
                break

        listing = "\n".join(f"{text!r:40} {href}" for text, href in links)
        save_debug("portal_links.txt", listing)
        raise TngfError(
            "Logged in, but could not find the link to the booking app. "
            "The portal's links are in debug/portal_links.txt."
        )

    # ---- booking app ---------------------------------------------------

    def booking_login(self, handoff: dict) -> None:
        """Exchange the portal's one-time token for a booking session."""
        resp = self.http.post(
            urljoin(BOOKING, "api/login/"),
            json={},
            params=handoff,
            headers={"Referer": urljoin(BOOKING, "login")},
            timeout=TIMEOUT,
        )
        # The API answers 202 on success.
        if resp.status_code not in (200, 202):
            raise TngfError(f"Booking login failed: HTTP {resp.status_code}")
        data = resp.json()
        if "session_token" not in data:
            raise TngfError(f"Booking login reply had no session token (keys: {sorted(data)}).")
        self.session_token = data["session_token"]
        self.user = data.get("user", {})

        # The web app keeps these as cookies; the slot page is rendered from them.
        user = self.user
        cookies = {
            "session_token": self.session_token,
            "user_name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip(),
            "user_id": str(user.get("id", "")),
            "user_type": str(user.get("user_type", "")),
            "user_permissions": str(user.get("permissions", "")),
            "user_member_id": str(user.get("username", "")),
        }
        for name, value in cookies.items():
            self.http.cookies.set(name, requests.utils.quote(value, safe=""), domain=BOOKING_HOST, path="/")

    def login(self, member_id: str, password: str) -> None:
        landing = self.portal_login(member_id, password)
        self.booking_login(self.find_handoff(landing))

    def api_headers(self) -> dict:
        if not self.session_token:
            raise TngfError("Not logged in.")
        return dict(COURSE_HEADERS, Authorization=self.session_token, Accept="application/json")

    def slots_for_date(self, date_ddmmyyyy: str) -> dict:
        """Load the slot page for a date (DD-MM-YYYY) and return its data."""
        if not self.session_token:
            raise TngfError("Not logged in.")
        resp = self.http.get(
            urljoin(BOOKING, "teeslotbooking"),
            params={"date": date_ddmmyyyy},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        tag = BeautifulSoup(resp.text, "html.parser").find("script", id="__NEXT_DATA__")
        if tag is None or not tag.string:
            save_debug("teeslotbooking_page.html", resp.text)
            raise TngfError("Slot page had no data block (saved to debug/).")
        props = json.loads(tag.string).get("props", {}).get("pageProps", {})
        if not props.get("data"):
            reason = props.get("error_msg") or "the session is no longer valid (logged in somewhere else?)"
            raise TngfError(f"No slot data for {date_ddmmyyyy}: {reason}")
        save_debug(f"slots_{date_ddmmyyyy}.json", json.dumps(props, indent=2))
        return props

    def members(self) -> list:
        """Members who can be added as players (same list the site's player picker loads)."""
        resp = self.http.get(
            urljoin(BOOKING, "api/member/"),
            params={
                "reduce_fields": "true",
                "page_size": 50000,
                "permissions": "tee_slot_master_booking_access",
                "user_type": "Member",
            },
            headers=self.api_headers(),
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            raise TngfError(f"Member list failed: HTTP {resp.status_code}")
        return resp.json().get("results", [])

    def my_bookings(self, date_ddmmyyyy: str) -> list:
        """Bookings on a date that include the logged-in member (the site's Booking List query)."""
        resp = self.http.get(
            urljoin(BOOKING, "api/teetime/slots/"),
            params={"players__id": self.user.get("id"), "date": date_ddmmyyyy,
                    "status": "Booked", "sort_by": "date"},
            headers=self.api_headers(),
            timeout=TIMEOUT,
        )
        if resp.status_code != 200:
            raise TngfError(f"Booking list failed: HTTP {resp.status_code}")
        data = resp.json()
        return data.get("results", []) if isinstance(data, dict) else data

    # ---- booking (these calls change things on the site) ----------------

    def hold_slot(self, date_ddmmyyyy: str, hole_no: int, time_label: str) -> requests.Response:
        """Ask the site to hold a slot for us. Returns the raw response."""
        return self.http.post(
            urljoin(BOOKING, "api/teetime/slots/"),
            json={"date": date_ddmmyyyy, "hole_no": hole_no, "time": time_label, "status": "Blocked"},
            headers=self.api_headers(),
            timeout=TIMEOUT,
        )

    def _patch_slot(self, slot_url: str, body: dict, extra_headers: Optional[dict] = None) -> requests.Response:
        slot_id = slot_url.rstrip("/").split("/")[-1]
        headers = self.api_headers()
        headers.update(extra_headers or {})
        return self.http.patch(
            urljoin(BOOKING, f"api/teetime/slots/{slot_id}/"), json=body, headers=headers, timeout=TIMEOUT
        )

    def confirm_slot(self, slot_url: str, player_ids: list, guests: Optional[list] = None) -> requests.Response:
        """Turn a held slot into a booking. Success is HTTP 202."""
        return self._patch_slot(
            slot_url,
            {"status": "Booked", "players_ids": player_ids, "guests": guests or []},
            # Same two headers the site's own booking form sends. The site has its
            # captcha switched off and sends this fixed value; if the club turns the
            # captcha on, this call will be refused and a person must book by hand.
            {"x-amz-waf-token": "development-bypass-token", "X-Request-Source": "NextJSForm"},
        )

    def release_slot(self, slot_url: str) -> requests.Response:
        """Give up a held slot (the site's own Cancel button)."""
        return self._patch_slot(slot_url, {"status": "Cancelled"})
