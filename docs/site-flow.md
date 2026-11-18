# TNGF tee-time booking: site flow

Mapped on 2026-11-18 from the public pages and the site's own JavaScript, without logging in.
Anything marked "unconfirmed" needs a logged-in run to verify.

## Two sites

| Site | Tech | Role |
|---|---|---|
| `https://mem.tngf.in/` | ASP.NET (IIS) | Member portal. Username/password login lives here. |
| `https://booking.tngf.in/` | Next.js behind CloudFront | Tee-time booking app. No login form of its own. |

## Step 1: log in on the member portal

- `GET https://mem.tngf.in/` returns the login form and sets a `__RequestVerificationToken` cookie.
- `POST https://mem.tngf.in/` with form fields:
  - `__RequestVerificationToken` (hidden field from the form)
  - `returnUrl` (`/ViewAndMonthBill/Index`)
  - `UserId` (membership ID)
  - `Password`
  - `RememberMe` (`false`)
- The page contains a text captcha and an OTP step, but both are switched off in the page's own script at the moment.

## Step 2: hand-off to the booking app

- The portal links to `https://booking.tngf.in/login?token=...&MCODE=...` (unconfirmed where this link appears after login).
- The booking app then calls `POST api/login/?token=...&MCODE=...` and receives `session_token` plus `user.id`, `user.username`, `user.user_type`, `user.permissions`.
- These are stored as cookies: `session_token`, `user_id`, `user_name`, `user_type`, `user_permissions`, `user_member_id`.

## Step 3: API calls

All calls send these headers:

```
COURSE-ID: 1
COURSE-NAME: TamilNadu Golf Federation
Authorization: <session_token>
```

API paths are relative (`api/...`) to `booking.tngf.in`. Resource URLs inside responses use
`https://63v082fjye.execute-api.ap-south-1.amazonaws.com/production/api/...`.

| Action | Call |
|---|---|
| Load slots for a date | Page `/teeslotbooking?date=DD-MM-YYYY`. Data has `sessions`, `tee_holes`, `templates`, `available_slots`, `booked_slots`, `blocked_slots`, `not_available_slots`, `booking_opening_day`, and per-template `booking_opening_time`. Exact request unconfirmed. |
| Hold a slot | `POST api/teetime/slots/` body `{date, hole_no, time, status: "Blocked"}`. Returns the slot with its `url`. Error message `Blocked` or `Booked` means someone else has it. |
| Confirm the booking | `PATCH api/teetime/slots/<id>/` body `{status: "Booked", players_ids: [...], guests: [...]}` with extra headers `x-amz-waf-token` and `X-Request-Source: NextJSForm`. Success is HTTP 202. |
| Release a held slot | `PATCH api/teetime/slots/<id>/` body `{status: "Cancelled"}` |
| Preferred group | `GET/POST/PATCH api/teetime/preferredgroup/` body `{member: <member url>, players: [...]}` |
| Member list for player pickers | `GET api/member/?reduce_fields=true&page_size=50000&permissions=tee_slot_master_booking_access` |
| My bookings | `GET api/teetime/slots/...&status=Booked&sort_by=date` |

Notes:

- Player 1 is always the logged-in member (`user_id`). Players 2 to 4 are member IDs or guests (`"Guest 1"`, `"Guest 2"`, `"Guest 3"`).
- A held slot times out ("Slot Booking Timed Out") if not confirmed; the time limit is unconfirmed.
- The site works in `Asia/Kolkata` time.

## Captcha at the confirm step

- The booking page loads the AWS WAF captcha SDK, and the confirm request carries an `x-amz-waf-token` header.
- The site's own code currently skips the captcha and sends a fixed placeholder token.
- If the club switches the captcha on, a person will have to solve it; the bot cannot.

## Confirmed by a logged-in run (2026-11-18)

- Portal login, hand-off and `api/login/` work from a script. `api/login/` answers HTTP 202 on success.
- Slots load from the server-rendered page `/teeslotbooking?date=DD-MM-YYYY`, read from its `__NEXT_DATA__` block (`props.pageProps.data`), using the session cookies.
- Slot data shape:
  - `booking_opening_day`: `DD-MM-YYYY`.
  - `templates.template_details[]`: one block per session, hole and slot type, with `slots` (times like `06:04 AM`), `hole_no`, `guest_only`, `min_players`, `max_players`, `min_members`, `max_members`, `booking_opening_time`.
  - `available_slots`, `booked_slots`, `blocked_slots`, `not_available_slots`: keyed by hole (`"1"`, `"10"`).
- Sunday 22-11-2026 as an example: opens Thursday 19-11-2026; morning blocks at 08:45 AM, afternoon blocks at 09:00 AM. Slots are 7 minutes apart from holes 1 and 10.
- Member slots need 3 to 4 players, at least 3 of them members. Guest-only slots take exactly 1 member plus guests, 3 to 4 players in total.

- Only one session per membership: a new login makes the earlier session's calls fail with HTTP 403. Two runs with the same membership cannot work side by side, and logging in through a browser ends a running bot's session.
- A member's bookings for a date: `GET api/teetime/slots/?players__id=<id>&date=DD-MM-YYYY&status=Booked&sort_by=date` (paginated, `results`).

## Still to confirm

1. Whether the hold and confirm calls are accepted outside a browser.
2. How long a held slot lasts and how long a session token lasts.
3. Booking limits per member (per day or per week).
