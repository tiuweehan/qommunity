#!/usr/bin/env python3
"""
Book Valley Park Tennis Court 3 as soon as a preferred Sunday slot opens.

Default mode is read-only. Use --book to actually create a booking.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests
import certifi
from mitmproxy import http
from mitmproxy.io import FlowReader


API_BASE = "https://prod-qomm-api-hmhggeagdbfycvd0.southeastasia-01.azurewebsites.net//api/v1.0"
PROPERTY_ID = "b2f41199-ef02-4bc6-a90e-7604d24c2c14"
UNIT_ID = "9779247a-b08f-4b7b-b497-fd5b65119a6a"
TENNIS_COURT_3_ID = "55868819-d547-4ac5-be87-6882310b90de"
DEFAULT_FLOW_FILE = "/tmp/qommunity-ipmitm-flows.mitm"
DEFAULT_AUTH_FILE = "qommunity_auth.json"
DEFAULT_AUTH_CONFIG_FILE = "auth_config.json"
DEFAULT_BASE_CONFIG_FILE = "booking_base_config.json"
DEFAULT_CLIENT_ID = "fbc7149c8b3244ddb754c090918b7621.mtwpublicapp.com.ibase"
DEFAULT_CA_BUNDLE = certifi.where()
DEFAULT_MITMPROXY_CA = str(Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem")
DEFAULT_COMBINED_CA_BUNDLE = str(Path.home() / ".qommunity-ca-bundle.pem")
DEFAULT_PREFERRED_STARTS = ("08:00:00", "07:00:00")
TOKEN_REFRESH_SKEW_SECONDS = 300
DEFAULT_ADVANCE_DAYS = 30
DEFAULT_OPEN_TIME = "00:00:00"
DEFAULT_LEAD_SECONDS = 1.0
DEFAULT_LOG_FILE = "tennis_booker.log"
DEFAULT_OTP_REGEX = r"\b\d{4,8}\b"
DEFAULT_ENV_FILE = ".env"
DEFAULT_DUE_WINDOW_SECONDS = 120


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class Style:
    RESET = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    GRAY = "\033[90m"


class Tee:
    def __init__(self, stream: Any, log_file: Any):
        self.stream = stream
        self.log_file = log_file

    def write(self, text: str) -> int:
        self.stream.write(text)
        self.log_file.write(ANSI_RE.sub("", text))
        return len(text)

    def flush(self) -> None:
        self.stream.flush()
        self.log_file.flush()


def load_env_file(path: str = DEFAULT_ENV_FILE) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def colorize(text: str, color: str, enabled: bool = True) -> str:
    if not enabled:
        return text
    return f"{color}{text}{Style.RESET}"


def setup_output_logging(log_file: str, no_color: bool) -> None:
    if not log_file:
        return
    path = Path(log_file)
    handle = path.open("a", buffering=1)
    timestamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    handle.write(f"\n===== tennis_booker start {timestamp} =====\n")
    sys.stdout = Tee(sys.stdout, handle)
    sys.stderr = Tee(sys.stderr, handle)
    print(colorize(f"Logging stdout/stderr to {path}", Style.GRAY, not no_color), flush=True)


class ApiError(RuntimeError):
    def __init__(self, message: str, status_code: int, body: Any):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


def facility_url(facility_id: str, booking_date: str) -> str:
    return (
        f"{API_BASE}/portfolio/{PROPERTY_ID}/unit/{UNIT_ID}"
        f"/facility/{facility_id}/?bookingdate={booking_date}"
    )


def booking_url(booking_id: str) -> str:
    return f"{API_BASE}/portfolio/{PROPERTY_ID}/unit/{UNIT_ID}/booking/{booking_id}"


def facility_list_url() -> str:
    return f"{API_BASE}/portfolio/{PROPERTY_ID}/unit/{UNIT_ID}/facility"


def auth_url(path: str) -> str:
    return f"{API_BASE}/auth/{path}"


def extract_latest_bearer(flow_file: str) -> str:
    token = ""
    path = Path(flow_file)
    if not path.exists():
        raise SystemExit(f"Flow file not found: {flow_file}")

    with path.open("rb") as f:
        for flow in FlowReader(f).stream():
            if not isinstance(flow, http.HTTPFlow):
                continue
            auth = flow.request.headers.get("authorization", "")
            if auth.lower().startswith("bearer "):
                token = auth.removeprefix("Bearer ").removeprefix("bearer ").strip()
            if flow.response:
                try:
                    body = json.loads(flow.response.get_text(strict=False) or "{}")
                except Exception:
                    body = {}
                login_output = body.get("loginOTPOutput") if isinstance(body, dict) else None
                access_token = login_output.get("accessToken") if isinstance(login_output, dict) else None
                if access_token:
                    token = access_token

    if not token:
        raise SystemExit(f"No Bearer token found in {flow_file}")
    return token


def make_base_session() -> requests.Session:
    session = requests.Session()
    session.verify = resolve_ca_bundle()
    session.headers.update(
        {
            "user-agent": "Dart/3.9 (dart:io)",
            "accept": "application/json",
            "accept-encoding": "gzip",
            "content-type": "application/json",
        }
    )
    return session


def resolve_ca_bundle() -> str:
    override = os.environ.get("QOMMUNITY_CA_BUNDLE")
    if override:
        return override

    mitm_ca = Path(DEFAULT_MITMPROXY_CA)
    if not mitm_ca.exists():
        return DEFAULT_CA_BUNDLE

    combined = Path(DEFAULT_COMBINED_CA_BUNDLE)
    certifi_text = Path(DEFAULT_CA_BUNDLE).read_text()
    mitm_text = mitm_ca.read_text()
    desired = certifi_text.rstrip() + "\n" + mitm_text.strip() + "\n"
    if not combined.exists() or combined.read_text() != desired:
        combined.write_text(desired)
        os.chmod(combined, 0o600)
    return str(combined)


def make_session(token: str) -> requests.Session:
    session = make_base_session()
    session.headers["authorization"] = f"Bearer {token}"
    return session


def set_session_token(session: requests.Session, token: str) -> None:
    session.headers["authorization"] = f"Bearer {token}"


def token_expiry(token: str) -> dt.datetime | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        exp = claims.get("exp")
        if not exp:
            return None
        return dt.datetime.fromtimestamp(int(exp), dt.timezone.utc).astimezone()
    except Exception:
        return None


def should_reload_token(token: str, skew_seconds: int = TOKEN_REFRESH_SKEW_SECONDS) -> bool:
    exp = token_expiry(token)
    if not exp:
        return False
    return exp <= dt.datetime.now(exp.tzinfo) + dt.timedelta(seconds=skew_seconds)


def auth_payload(args: argparse.Namespace, otp: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "contactType": args.auth_contact_type,
        "contact": args.auth_contact,
        "client_id": args.auth_client_id,
    }
    if args.auth_mobile_country_code:
        payload["mobileCountryCode"] = args.auth_mobile_country_code
    if otp is not None:
        payload["otp"] = otp
        if args.auth_accept_tc:
            payload["TCAccepted"] = True
    return payload


def auth_config_candidates(args: argparse.Namespace) -> list[Path]:
    candidates = []
    if args.auth_config:
        candidates.append(Path(args.auth_config))
    else:
        candidates.append(Path(DEFAULT_AUTH_CONFIG_FILE))
        if args.config:
            candidates.append(Path(args.config))
        else:
            candidates.append(Path(DEFAULT_BASE_CONFIG_FILE))
    return candidates


def auth_config_from_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = load_config(str(path))
    auth = data.get("auth")
    if isinstance(auth, dict):
        return auth
    defaults = data.get("defaults")
    if isinstance(defaults, dict) and isinstance(defaults.get("auth"), dict):
        return defaults["auth"]
    return {}


def selected_auth_config(auth: dict[str, Any], mode: str) -> dict[str, Any]:
    selected = dict(auth)
    profiles = auth.get("profiles")
    if isinstance(profiles, dict) and isinstance(profiles.get(mode), dict):
        selected.update(profiles[mode])
    mode_config = auth.get(mode)
    if isinstance(mode_config, dict):
        selected.update(mode_config)
    selected.pop("profiles", None)
    selected.pop("email", None)
    selected.pop("mobile", None)
    return selected


def apply_auth_config(args: argparse.Namespace) -> None:
    merged: dict[str, Any] = {}
    loaded_paths = []
    mode = args.login or "email"
    for path in auth_config_candidates(args):
        auth = auth_config_from_file(path)
        if auth:
            merged.update(selected_auth_config(auth, mode))
            loaded_paths.append(str(path))

    if args.auth_contact_type == "":
        args.auth_contact_type = "2" if mode == "email" else "1"
    if args.auth_mobile_country_code == "":
        args.auth_mobile_country_code = "+65" if mode == "mobile" else ""

    if not args.auth_contact:
        args.auth_contact = str(merged.get("contact") or merged.get("auth_contact") or "")
    if (mode == "mobile" or not args.auth_mobile_country_code) and (merged.get("mobileCountryCode") or merged.get("mobile_country_code")):
        args.auth_mobile_country_code = str(merged.get("mobileCountryCode") or merged.get("mobile_country_code"))
    if (merged.get("contactType") or merged.get("contact_type")):
        args.auth_contact_type = str(merged.get("contactType") or merged.get("contact_type"))
    if args.auth_client_id == DEFAULT_CLIENT_ID and (merged.get("client_id") or merged.get("clientId")):
        args.auth_client_id = str(merged.get("client_id") or merged.get("clientId"))
    if loaded_paths:
        print(
            colorize("Loaded auth config", Style.CYAN, not args.no_color)
            + f" paths={loaded_paths}",
            flush=True,
        )


def load_otp_config(args: argparse.Namespace) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for path in auth_config_candidates(args):
        if not path.exists():
            continue
        data = load_config(str(path))
        otp_config = data.get("otp")
        if isinstance(otp_config, dict):
            merged.update(otp_config)
        auth = data.get("auth")
        if isinstance(auth, dict) and isinstance(auth.get("otp"), dict):
            merged.update(auth["otp"])
    return merged


def expand_user_path(value: str) -> str:
    return str(Path(value).expanduser()) if value else ""


def read_secret(value: str, file_value: str) -> str:
    if value:
        return value.strip()
    path = expand_user_path(file_value)
    if not path:
        return ""
    return Path(path).read_text().strip()


def apply_otp_config(args: argparse.Namespace) -> None:
    config = load_otp_config(args)
    if not args.otp_source:
        args.otp_source = str(config.get("source") or "prompt")
    if not args.otp_worker_url:
        args.otp_worker_url = str(config.get("worker_url") or config.get("url") or "")
    if not args.otp_secret_file:
        args.otp_secret_file = str(config.get("secret_file") or "")
    if not args.otp_secret:
        args.otp_secret = str(config.get("secret") or "")
    if args.otp_timeout_seconds is None:
        args.otp_timeout_seconds = int(config.get("timeout_seconds", 180))
    if args.otp_poll_interval is None:
        args.otp_poll_interval = float(config.get("poll_interval", 2.0))
    if not args.otp_regex:
        args.otp_regex = str(config.get("regex") or DEFAULT_OTP_REGEX)


def poll_worker_otp(args: argparse.Namespace, requested_at: dt.datetime) -> str:
    if not args.otp_worker_url:
        raise SystemExit("OTP worker source requires otp.worker_url in auth_config.json or --otp-worker-url")
    secret = read_secret(args.otp_secret, args.otp_secret_file)
    if not secret:
        raise SystemExit("OTP worker source requires otp.secret_file/otp.secret or --otp-secret-file/--otp-secret")

    session = make_base_session()
    session.no_color = args.no_color
    deadline = time.monotonic() + float(args.otp_timeout_seconds)
    pattern = re.compile(args.otp_regex)
    after = int(requested_at.timestamp())
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        print(
            colorize("OTP poll start", Style.BLUE, not args.no_color)
            + f" source=worker attempt={attempt} url={args.otp_worker_url}",
            flush=True,
        )
        try:
            response = session.get(
                args.otp_worker_url,
                params={"after": after, "contact": args.auth_contact, "mode": args.login},
                headers={"authorization": f"Bearer {secret}"},
                timeout=10,
            )
            if response.status_code == 200:
                body = response.json()
                candidate = str(body.get("otp") or "")
                if candidate and pattern.search(candidate):
                    print(
                        colorize("OTP received", Style.GREEN, not args.no_color)
                        + f" source=worker attempt={attempt} received_at={body.get('receivedAt', '')}",
                        flush=True,
                    )
                    return candidate
            elif response.status_code not in {202, 204, 404}:
                print(
                    colorize("OTP poll unexpected response", Style.YELLOW, not args.no_color)
                    + f" status={response.status_code} body={response.text[:300]!r}",
                    flush=True,
                )
        except requests.RequestException as exc:
            print(
                colorize("OTP poll failed", Style.YELLOW, not args.no_color)
                + f" attempt={attempt} error={type(exc).__name__}: {exc}",
                flush=True,
            )
        time.sleep(float(args.otp_poll_interval))
    raise SystemExit(f"Timed out waiting for OTP from worker after {args.otp_timeout_seconds}s")


def obtain_otp(args: argparse.Namespace, requested_at: dt.datetime) -> str:
    if args.otp:
        return args.otp.strip()
    apply_otp_config(args)
    if args.otp_source == "worker":
        return poll_worker_otp(args, requested_at)
    return input("Enter OTP: ").strip()


def extract_auth_token(auth_data: dict[str, Any]) -> str:
    login_output = auth_data.get("loginOTPOutput")
    if isinstance(login_output, dict):
        token = login_output.get("accessToken")
        if token:
            return str(token)
    token = auth_data.get("accessToken")
    if token:
        return str(token)
    raise SystemExit("Auth file does not contain loginOTPOutput.accessToken")


def load_auth_file(path: str) -> dict[str, Any]:
    with Path(path).open() as f:
        auth_data = json.load(f)
    if not isinstance(auth_data, dict):
        raise SystemExit(f"Auth file must contain a JSON object: {path}")
    return auth_data


def load_auth_file_token(path: str) -> str:
    return extract_auth_token(load_auth_file(path))


def write_auth_file(path: str, auth_data: dict[str, Any], token: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    exp = token_expiry(token)
    output = {
        "generatedAt": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "expiresAt": exp.isoformat(timespec="seconds") if exp else None,
        **auth_data,
    }
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(json.dumps(output, indent=2) + "\n")
    os.chmod(tmp, 0o600)
    tmp.replace(target)
    os.chmod(target, 0o600)


def run_login(args: argparse.Namespace) -> int:
    apply_auth_config(args)
    if not args.auth_contact:
        raise SystemExit(
            f"Login {args.login} requires --auth-contact, QOMMUNITY_AUTH_CONTACT, or auth.{args.login}.contact in auth_config.json"
        )
    session = make_base_session()
    session.no_color = args.no_color
    print(
        colorize("Auth OTP request start", Style.BLUE, not args.no_color)
        + f" mode={args.login} contact_type={args.auth_contact_type} contact={args.auth_contact}"
        f"{' mobile_country_code=' + args.auth_mobile_country_code if args.auth_mobile_country_code else ''}",
        flush=True,
    )
    otp_requested_at = dt.datetime.now().astimezone()
    request_result = request_json(session, "POST", auth_url("requesttoken"), json=auth_payload(args))
    print(
        colorize("Auth OTP request sent", Style.GREEN, not args.no_color)
        + f" userName={request_result.get('userName')} userId={request_result.get('userId')}",
        flush=True,
    )

    otp = obtain_otp(args, otp_requested_at)
    if not otp:
        raise SystemExit("OTP is required")

    print(colorize("Auth token exchange start", Style.BLUE, not args.no_color), flush=True)
    auth_data = request_json(session, "POST", auth_url("generatetoken"), json=auth_payload(args, otp=otp))
    token = extract_auth_token(auth_data)
    write_auth_file(args.auth_file, auth_data, token)
    exp = token_expiry(token)
    print(
        colorize("Auth token saved", Style.GREEN + Style.BOLD, not args.no_color)
        + f" path={args.auth_file} mode=600"
        f"{' expires_at=' + exp.isoformat(timespec='seconds') if exp else ''}",
        flush=True,
    )
    return 0


def summarize_payload(payload: Any) -> str:
    if payload is None:
        return ""
    if isinstance(payload, dict):
        keys = ",".join(sorted(payload.keys()))
        details = []
        if "isConfirmed" in payload:
            details.append(f"isConfirmed={payload.get('isConfirmed')}")
        if "paymentMethod" in payload:
            details.append(f"paymentMethod={payload.get('paymentMethod')}")
        slots = payload.get("timeSlots")
        if isinstance(slots, list):
            slot_ids = [str(slot.get("timeSlotId")) for slot in slots if isinstance(slot, dict)]
            details.append(f"timeSlots={slot_ids}")
        return f"keys={keys}" + (f" {' '.join(details)}" if details else "")
    return f"type={type(payload).__name__}"


def summarize_response(body: Any) -> str:
    if not isinstance(body, dict):
        return f"type={type(body).__name__}"

    parts = []
    if "status" in body:
        parts.append(f"apiStatus={body.get('status')}")
    if body.get("message"):
        parts.append(f"message={body.get('message')!r}")

    payload = body.get("data") if isinstance(body.get("data"), dict) else body
    for key in ("id", "facilityName", "bookingDate", "bookingStatus"):
        if payload.get(key) is not None:
            parts.append(f"{key}={payload.get(key)!r}")

    slots = payload.get("timeSlots")
    if isinstance(slots, list):
        slot_text = ",".join(
            f"{slot.get('startTime')}-{slot.get('endTime')}" for slot in slots if isinstance(slot, dict)
        )
        if slot_text:
            parts.append(f"slots={slot_text}")

    return " ".join(parts) if parts else f"keys={','.join(sorted(body.keys()))}"


def request_json(session: requests.Session, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
    use_color = not getattr(session, "no_color", False)
    request_started = dt.datetime.now().astimezone()
    start = time.perf_counter()
    payload_summary = summarize_payload(kwargs.get("json"))
    print(
        colorize("HTTP request start", Style.BLUE, use_color)
        + f" method={method} url={url} "
        f"at={request_started.isoformat(timespec='milliseconds')}"
        f"{' payload=' + payload_summary if payload_summary else ''}",
        flush=True,
    )

    try:
        response = session.request(method, url, timeout=10, **kwargs)
    except requests.RequestException as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        print(
            colorize("HTTP request failed", Style.RED, use_color)
            + f" method={method} url={url} "
            f"elapsed_ms={elapsed_ms:.1f} error={type(exc).__name__}: {exc}",
            flush=True,
        )
        raise

    elapsed_ms = (time.perf_counter() - start) * 1000
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text}
    response_bytes = len(response.content or b"")
    request_id = (
        response.headers.get("x-ms-request-id")
        or response.headers.get("request-id")
        or response.headers.get("Request-Id")
        or ""
    )
    status_color = Style.GREEN if response.status_code < 400 else Style.RED
    print(
        colorize("HTTP response end", status_color, use_color)
        + f" method={method} url={url} status={response.status_code} "
        f"elapsed_ms={elapsed_ms:.1f} bytes={response_bytes}"
        f"{' request_id=' + request_id if request_id else ''} "
        f"summary={summarize_response(body)}",
        flush=True,
    )
    if response.status_code >= 400:
        raise ApiError(
            f"{method} {url} failed: HTTP {response.status_code}: {body}",
            response.status_code,
            body,
        )
    return body


def get_date_entry(data: dict[str, Any], target_date: str) -> dict[str, Any] | None:
    availability = data.get("availability") or {}
    for day in availability.get("availableDates") or []:
        if day.get("date") == target_date:
            return day
    return None


def find_preferred_slot(day: dict[str, Any], preferred_starts: tuple[str, ...]) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    slots = day.get("timeSlots") or []
    by_start = {slot.get("startTime"): slot for slot in slots}
    for start in preferred_starts:
        slot = by_start.get(start)
        if slot and slot.get("status") == "Available":
            return slot, slots
    return None, slots


def summarize_slots(slots: list[dict[str, Any]], preferred_starts: tuple[str, ...]) -> str:
    lines = []
    for slot in slots:
        if slot.get("startTime") in preferred_starts:
            lines.append(
                f"{slot.get('startTime')}-{slot.get('endTime')} "
                f"status={slot.get('status')} id={slot.get('id')}"
            )
    return "; ".join(lines) if lines else "preferred slots not present"


def validate_slot(
    session: requests.Session,
    booking_date: str,
    facility_id: str,
    slot_id: str,
) -> dict[str, Any]:
    payload = {
        "isConfirmed": False,
        "timeSlots": [{"timeSlotId": slot_id}],
    }
    return request_json(session, "POST", facility_url(facility_id, booking_date), json=payload)


def confirm_booking(
    session: requests.Session,
    booking_date: str,
    facility_id: str,
    slot_id: str,
    payment_method: str,
) -> dict[str, Any]:
    payload = {
        "Id": facility_id,
        "isConfirmed": True,
        "paymentMethod": payment_method,
        "timeSlots": [{"timeSlotId": slot_id}],
    }
    return request_json(session, "POST", facility_url(facility_id, booking_date), json=payload)


def get_booking(session: requests.Session, booking_id: str) -> dict[str, Any]:
    return request_json(session, "GET", booking_url(booking_id))


def cancel_booking(session: requests.Session, booking_id: str, reason: str) -> dict[str, Any]:
    return request_json(session, "POST", f"{booking_url(booking_id)}/cancel", json={"reason": reason})


def fetch_facilities(session: requests.Session) -> dict[str, Any]:
    return request_json(session, "GET", facility_list_url())


def print_facilities(facilities: list[dict[str, Any]]) -> None:
    for facility in facilities:
        print(
            f"{facility.get('key'):<22} {facility.get('facility_id')} "
            f"{facility.get('name')} category={facility.get('category', '')}",
            flush=True,
        )


def print_booking_summary(label: str, data: dict[str, Any]) -> None:
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    slots = payload.get("timeSlots") or []
    slot_text = ", ".join(f"{slot.get('startTime')}-{slot.get('endTime')}" for slot in slots) or "no slots"
    print(
        f"{label}: id={payload.get('id')} facility={payload.get('facilityName')} "
        f"date={payload.get('bookingDate')} slots={slot_text} "
        f"status={payload.get('bookingStatus')}",
        flush=True,
    )


def parse_wait_until(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.datetime.now().astimezone().tzinfo)
    return parsed


def sleep_until(target: dt.datetime, no_color: bool = False) -> None:
    use_color = not no_color
    last_bucket = None
    while True:
        now = dt.datetime.now(target.tzinfo)
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            print(
                colorize("Countdown complete", Style.GREEN, use_color)
                + f" target={target.isoformat(timespec='milliseconds')} "
                f"now={now.isoformat(timespec='milliseconds')}",
                flush=True,
            )
            return

        if remaining <= 1:
            time.sleep(remaining)
            continue
        elif remaining <= 60:
            bucket = int(remaining)
            sleep_for = min(remaining, 1)
        elif remaining <= 3600:
            bucket = int(remaining // 60)
            sleep_for = min(remaining, 60)
        elif remaining <= 86400:
            bucket = int(remaining // 3600)
            sleep_for = min(remaining, 3600)
        else:
            bucket = int(remaining // 86400)
            sleep_for = min(remaining, 86400)

        if bucket != last_bucket:
            print(
                colorize("Countdown", Style.CYAN, use_color)
                + f" target={target.isoformat(timespec='seconds')} "
                f"now={now.isoformat(timespec='seconds')} remaining={format_duration(remaining)}",
                flush=True,
            )
            last_bucket = bucket
        time.sleep(sleep_for)


def format_duration(seconds: float) -> str:
    whole = max(0, int(seconds))
    days, rem = divmod(whole, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    prefix = f"{days}d " if days else ""
    return f"{prefix}{hours:02d}:{minutes:02d}:{secs:02d}.{int((seconds - whole) * 1000):03d}"


def normalize_time(value: str) -> str:
    parts = value.strip().split(":")
    if len(parts) == 2:
        hour, minute = parts
        second = "00"
    elif len(parts) == 3:
        hour, minute, second = parts
    else:
        raise ValueError(f"Invalid time {value!r}; use HH:MM or HH:MM:SS")
    return f"{int(hour):02d}:{int(minute):02d}:{int(second):02d}"


def parse_local_datetime(date_value: str, time_value: str) -> dt.datetime:
    local_tz = dt.datetime.now().astimezone().tzinfo
    date_part = dt.date.fromisoformat(date_value)
    time_part = dt.time.fromisoformat(normalize_time(time_value))
    return dt.datetime.combine(date_part, time_part, tzinfo=local_tz)


def load_config(path: str) -> dict[str, Any]:
    with Path(path).open() as f:
        config = json.load(f)
    if not isinstance(config, dict):
        raise SystemExit("Config root must be a JSON object")
    return config


def merge_config(
    base_config: dict[str, Any],
    facilities_config: dict[str, Any] | None,
    bookings_config: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(base_config)
    if facilities_config:
        if "facilities" in facilities_config:
            merged["facilities"] = facilities_config["facilities"]
        elif isinstance(facilities_config.get("list"), list):
            merged["facilities"] = normalize_facilities(facilities_config)
        else:
            raise SystemExit("Facilities config must contain facilities[] or API-style list[]")

    if not bookings_config:
        return merged
    if "bookings" in bookings_config:
        merged["bookings"] = bookings_config["bookings"]
    if "requests" in bookings_config:
        merged["bookings"] = bookings_config["requests"]
    if "defaults" in bookings_config:
        merged["defaults"] = {**(base_config.get("defaults") or {}), **(bookings_config.get("defaults") or {})}
    return merged


def slugify(value: str) -> str:
    chars = []
    last_was_sep = False
    for char in value.lower():
        if char.isalnum():
            chars.append(char)
            last_was_sep = False
        elif not last_was_sep:
            chars.append("_")
            last_was_sep = True
    return "".join(chars).strip("_")


def normalize_facilities(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw_facilities = data.get("facilities") or data.get("list") or []
    facilities = []
    for item in raw_facilities:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or ""
        facility_id = item.get("facility_id") or item.get("id")
        if not name or not facility_id:
            continue
        facilities.append(
            {
                "key": slugify(name),
                "name": name,
                "facility_id": facility_id,
                "category": item.get("categoryName") or item.get("category") or "",
            }
        )
    return facilities


def facility_lookup_key(facility: dict[str, Any]) -> str:
    return str(facility.get("key") or facility.get("name") or facility.get("facility_id") or facility.get("id"))


def facilities_by_key(facilities: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for facility in facilities:
        key = facility_lookup_key(facility)
        if key:
            lookup[key] = facility
        for alias in facility.get("aliases") or []:
            lookup[str(alias)] = facility
    return lookup


def make_job(
    facility: dict[str, Any],
    defaults: dict[str, Any],
    booking_date: str,
    starts: list[str] | tuple[str, ...],
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    overrides = overrides or {}
    facility_id = facility.get("facility_id") or facility.get("id")
    if not facility_id:
        raise SystemExit(f"Facility {facility_lookup_key(facility)!r} requires facility_id")

    target_date = dt.date.fromisoformat(str(booking_date))
    advance_days = int(overrides.get("advance_days", facility.get("advance_days", defaults.get("advance_days", DEFAULT_ADVANCE_DAYS))))
    open_time = overrides.get("open_time", facility.get("open_time", defaults.get("open_time", DEFAULT_OPEN_TIME)))
    lead_seconds = float(overrides.get("lead_seconds", facility.get("lead_seconds", defaults.get("lead_seconds", DEFAULT_LEAD_SECONDS))))
    interval = float(overrides.get("interval", facility.get("interval", defaults.get("interval", 0.2))))
    max_attempts = int(overrides.get("max_attempts", facility.get("max_attempts", defaults.get("max_attempts", 0))))
    payment_method = overrides.get("payment_method", facility.get("payment_method", defaults.get("payment_method", "EstateCredit")))
    validate = bool(overrides.get("validate", facility.get("validate", defaults.get("validate", False))))
    book = bool(overrides.get("book", facility.get("book", defaults.get("book", False))))

    open_date = target_date - dt.timedelta(days=advance_days)
    open_at = parse_local_datetime(open_date.isoformat(), open_time)
    start_at = open_at - dt.timedelta(seconds=lead_seconds)
    return {
        "name": facility.get("name") or facility_lookup_key(facility),
        "facility_id": facility_id,
        "date": target_date.isoformat(),
        "preferred_starts": tuple(normalize_time(str(start)) for start in starts),
        "payment_method": payment_method,
        "validate": validate,
        "book": book,
        "interval": interval,
        "max_attempts": max_attempts,
        "open_at": open_at,
        "start_at": start_at,
        "advance_days": advance_days,
        "lead_seconds": lead_seconds,
    }


def expand_config_jobs(config: dict[str, Any]) -> list[dict[str, Any]]:
    defaults = config.get("defaults") or {}
    facilities = config.get("facilities") or []
    if not facilities:
        raise SystemExit("Config must contain at least one facility in facilities[]")

    jobs: list[dict[str, Any]] = []
    booking_requests = config.get("bookings") or config.get("requests") or []
    if booking_requests:
        lookup = facilities_by_key(facilities)
        for request in booking_requests:
            if not isinstance(request, dict):
                raise SystemExit("Each bookings[] item must be an object")
            facility_ref = str(request.get("facility") or request.get("facility_key") or request.get("facility_id") or "")
            if not facility_ref:
                raise SystemExit("Each bookings[] item requires facility")
            facility = lookup.get(facility_ref)
            if not facility:
                raise SystemExit(f"Unknown facility reference in bookings[]: {facility_ref}")
            starts = request.get("preferred_starts") or request.get("timings") or request.get("starts") or defaults.get("preferred_starts")
            if not starts:
                raise SystemExit(f"Booking request for {facility_ref} requires preferred_starts[]")
            dates = request.get("dates")
            if dates:
                for booking_date in dates:
                    jobs.append(make_job(facility, defaults, str(booking_date), starts, request))
            else:
                booking_date = request.get("date")
                if not booking_date:
                    raise SystemExit(f"Booking request for {facility_ref} requires date or dates[]")
                jobs.append(make_job(facility, defaults, str(booking_date), starts, request))
        return sorted(jobs, key=lambda job: (job["start_at"], job["date"], job["name"]))

    for facility in facilities:
        if not isinstance(facility, dict):
            raise SystemExit("Each facilities[] item must be an object")

        dates = facility.get("dates") or []
        starts = facility.get("preferred_starts") or facility.get("timings") or defaults.get("preferred_starts")
        if not dates:
            raise SystemExit(f"Facility {facility_lookup_key(facility)} requires dates[]")
        if not starts:
            raise SystemExit(f"Facility {facility_lookup_key(facility)} requires preferred_starts[]")

        for booking_date in dates:
            jobs.append(make_job(facility, defaults, str(booking_date), starts))

    return sorted(jobs, key=lambda job: (job["start_at"], job["date"], job["name"]))


def attempt_once(
    session: requests.Session,
    booking_date: str,
    facility_id: str,
    preferred_starts: tuple[str, ...],
    payment_method: str,
    do_validate: bool,
    do_book: bool,
) -> bool:
    use_color = not getattr(session, "no_color", False)
    attempt_started = dt.datetime.now().astimezone()
    print(
        colorize("Poll start", Style.MAGENTA, use_color)
        + f" at={attempt_started.isoformat(timespec='milliseconds')} "
        f"facility_id={facility_id} date={booking_date} preferred_starts={list(preferred_starts)} "
        f"book={do_book}",
        flush=True,
    )
    data = request_json(session, "GET", facility_url(facility_id, booking_date))
    facility = data.get("facility") or {}
    day = get_date_entry(data, booking_date)
    if not day:
        print(
            colorize("Poll result", Style.YELLOW, use_color)
            + f" at={dt.datetime.now().astimezone().isoformat(timespec='milliseconds')} "
            f"facility={facility.get('name')} date={booking_date} target date not returned",
            flush=True,
        )
        return False

    slot, slots = find_preferred_slot(day, preferred_starts)
    print(
        colorize("Poll result", Style.YELLOW, use_color)
        + f" at={dt.datetime.now().astimezone().isoformat(timespec='milliseconds')} "
        f"facility={facility.get('name')} date={booking_date} dayStatus={day.get('status')} "
        f"availability=\"{summarize_slots(slots, preferred_starts)}\"",
        flush=True,
    )

    if not slot:
        print(
            colorize("Poll outcome", Style.GRAY, use_color)
            + f" at={dt.datetime.now().astimezone().isoformat(timespec='milliseconds')} "
            f"available=false next_sleep_pending=true",
            flush=True,
        )
        return False

    slot_id = slot["id"]
    print(
        colorize("Poll outcome", Style.GREEN, use_color)
        + f" at={dt.datetime.now().astimezone().isoformat(timespec='milliseconds')} "
        f"available=true selected={slot['startTime']}-{slot['endTime']} slot_id={slot_id}",
        flush=True,
    )

    if do_validate or do_book:
        print(
            colorize("Validation start", Style.BLUE, use_color)
            + f" at={dt.datetime.now().astimezone().isoformat(timespec='milliseconds')} "
            f"slot_id={slot_id}",
            flush=True,
        )
        validation = validate_slot(session, booking_date, facility_id, slot_id)
        validation_status = ((validation.get("data") or {}).get("bookingStatus") or validation.get("message") or "")
        print(
            colorize("Validation end", Style.GREEN, use_color)
            + f" at={dt.datetime.now().astimezone().isoformat(timespec='milliseconds')} "
            f"result={validation_status!r}",
            flush=True,
        )
        print("Validation:", json.dumps(validation, indent=2), flush=True)

    if not do_book:
        print(
            colorize("Dry run stop", Style.YELLOW, use_color)
            + f" at={dt.datetime.now().astimezone().isoformat(timespec='milliseconds')} "
            f"reason=slot_available_but_book_false",
            flush=True,
        )
        return True

    print(
        colorize("Booking confirm start", Style.BLUE, use_color)
        + f" at={dt.datetime.now().astimezone().isoformat(timespec='milliseconds')} "
        f"slot_id={slot_id} payment_method={payment_method}",
        flush=True,
    )
    result = confirm_booking(session, booking_date, facility_id, slot_id, payment_method)
    result_payload = result.get("data") or {}
    print(
        colorize("Booking confirm end", Style.GREEN, use_color)
        + f" at={dt.datetime.now().astimezone().isoformat(timespec='milliseconds')} "
        f"booking_id={result_payload.get('id')} status={result_payload.get('bookingStatus')} "
        f"message={result.get('message')!r}",
        flush=True,
    )
    print("Booking result:", json.dumps(result, indent=2), flush=True)
    status = ((result.get("data") or {}).get("bookingStatus") or "").lower()
    return status in {"booked", "onhold", "pending"}


def load_session(args: argparse.Namespace) -> tuple[requests.Session, str, bool]:
    auth_path = Path(args.auth_file) if args.auth_file else None
    auth_file_exists = bool(auth_path and auth_path.exists())
    fixed_token = bool(args.token or auth_file_exists)
    if args.token:
        token = args.token
        source = "command line"
    elif auth_file_exists:
        token = load_auth_file_token(str(auth_path))
        source = str(auth_path)
    else:
        token = extract_latest_bearer(args.flow_file)
        source = args.flow_file
    exp = token_expiry(token)
    if exp:
        print(
            colorize("Using Bearer token", Style.CYAN, not args.no_color)
            + f" source={source} expiring_at={exp.isoformat(timespec='seconds')}",
            flush=True,
        )
    else:
        print(colorize("Using Bearer token", Style.CYAN, not args.no_color) + f" source={source}", flush=True)
    session = make_session(token)
    session.no_color = args.no_color
    return session, token, fixed_token


def maybe_reload_token(
    args: argparse.Namespace,
    session: requests.Session,
    token: str,
    fixed_token: bool,
) -> str:
    if fixed_token or not args.token_refresh_skew or not should_reload_token(token, args.token_refresh_skew):
        return token
    token = extract_latest_bearer(args.flow_file)
    set_session_token(session, token)
    exp = token_expiry(token)
    if exp:
        print(
            colorize("Reloaded Bearer token", Style.CYAN, not args.no_color)
            + f" expiring_at={exp.isoformat(timespec='seconds')}",
            flush=True,
        )
    return token


def attempt_with_auth_retry(
    args: argparse.Namespace,
    session: requests.Session,
    token: str,
    fixed_token: bool,
    booking_date: str,
    facility_id: str,
    preferred_starts: tuple[str, ...],
    payment_method: str,
    do_validate: bool,
    do_book: bool,
) -> tuple[bool, str]:
    token = maybe_reload_token(args, session, token, fixed_token)
    try:
        done = attempt_once(
            session=session,
            booking_date=booking_date,
            facility_id=facility_id,
            preferred_starts=preferred_starts,
            payment_method=payment_method,
            do_validate=do_validate,
            do_book=do_book,
        )
        return done, token
    except ApiError as exc:
        if fixed_token or exc.status_code != 401:
            raise
        print("API returned 401. Reloading latest captured token and retrying once.", file=sys.stderr, flush=True)
        token = extract_latest_bearer(args.flow_file)
        set_session_token(session, token)
        exp = token_expiry(token)
        if exp:
            print(
                colorize("Reloaded Bearer token", Style.CYAN, not args.no_color)
                + f" expiring_at={exp.isoformat(timespec='seconds')}",
                flush=True,
            )
        done = attempt_once(
            session=session,
            booking_date=booking_date,
            facility_id=facility_id,
            preferred_starts=preferred_starts,
            payment_method=payment_method,
            do_validate=do_validate,
            do_book=do_book,
        )
        return done, token


def run_config(args: argparse.Namespace, config: dict[str, Any]) -> int:
    use_color = not args.no_color
    jobs = expand_config_jobs(config)
    now = dt.datetime.now().astimezone()
    pending_jobs = []
    skipped_jobs = []
    future_jobs = []
    due_by = now + dt.timedelta(seconds=args.due_window_seconds) if args.due_window_seconds else None
    for job in jobs:
        comparable_now = now.astimezone(job["open_at"].tzinfo)
        if job["open_at"] <= comparable_now:
            skipped_jobs.append(job)
        elif due_by and job["start_at"] > due_by.astimezone(job["start_at"].tzinfo):
            future_jobs.append(job)
        else:
            pending_jobs.append(job)

    for job in skipped_jobs:
        print(
            colorize("Skipping past booking window", Style.YELLOW, use_color)
            + f" name={job['name']} date={job['date']} "
            f"open_at={job['open_at'].isoformat(timespec='seconds')} "
            f"now={now.isoformat(timespec='seconds')} "
            f"advance_days={job['advance_days']}",
            flush=True,
        )
    jobs = pending_jobs

    if args.book:
        for job in jobs:
            job["book"] = True
    if args.validate:
        for job in jobs:
            job["validate"] = True

    print(
        colorize(f"Loaded {len(jobs)} pending booking job(s)", Style.BOLD + Style.CYAN, use_color)
        + f" skipped={len(skipped_jobs)} future={len(future_jobs)} due_window_seconds={args.due_window_seconds}",
        flush=True,
    )
    if not jobs:
        return 0

    session, token, fixed_token = load_session(args)
    failures = 0

    for i, job in enumerate(jobs, 1):
        print(
            colorize(f"Job {i}/{len(jobs)}", Style.BOLD + Style.MAGENTA, use_color)
            + f" name={job['name']} facility_id={job['facility_id']} "
            f"date={job['date']} starts={list(job['preferred_starts'])} "
            f"open_at={job['open_at'].isoformat(timespec='seconds')} "
            f"start_at={job['start_at'].isoformat(timespec='seconds')} "
            f"book={job['book']}",
            flush=True,
        )

        now = dt.datetime.now(job["start_at"].tzinfo)
        if now < job["start_at"]:
            sleep_seconds = (job["start_at"] - now).total_seconds()
            print(
                colorize("Waiting", Style.CYAN, use_color)
                + f" {sleep_seconds:.1f}s until probe start",
                flush=True,
            )
            sleep_until(job["start_at"], no_color=args.no_color)

        attempts = 0
        while True:
            attempts += 1
            print(
                colorize("Job poll attempt start", Style.MAGENTA, use_color)
                + f" job={job['name']} date={job['date']} attempt={attempts} "
                f"at={dt.datetime.now().astimezone().isoformat(timespec='milliseconds')}",
                flush=True,
            )
            try:
                done, token = attempt_with_auth_retry(
                    args=args,
                    session=session,
                    token=token,
                    fixed_token=fixed_token,
                    booking_date=job["date"],
                    facility_id=job["facility_id"],
                    preferred_starts=job["preferred_starts"],
                    payment_method=job["payment_method"],
                    do_validate=job["validate"],
                    do_book=job["book"],
                )
                if done:
                    print(
                        colorize("Job complete", Style.GREEN + Style.BOLD, use_color)
                        + f" job={job['name']} date={job['date']} attempts={attempts} "
                        f"at={dt.datetime.now().astimezone().isoformat(timespec='milliseconds')}",
                        flush=True,
                    )
                    break
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"{dt.datetime.now().isoformat(timespec='seconds')} ERROR job={job['name']} {exc}", file=sys.stderr, flush=True)

            if job["max_attempts"] and attempts >= job["max_attempts"]:
                failures += 1
                print(
                    colorize("Job reached max attempts", Style.RED, use_color)
                    + f" max_attempts={job['max_attempts']} job={job['name']} date={job['date']}",
                    flush=True,
                )
                break
            print(
                colorize("Job poll sleep", Style.GRAY, use_color)
                + f" job={job['name']} date={job['date']} attempt={attempts} "
                f"interval={job['interval']}s next_at="
                f"{(dt.datetime.now().astimezone() + dt.timedelta(seconds=job['interval'])).isoformat(timespec='milliseconds')}",
                flush=True,
            )
            time.sleep(job["interval"])

    return 1 if failures else 0


def main() -> int:
    load_env_file(os.environ.get("QOMMUNITY_ENV_FILE", DEFAULT_ENV_FILE))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("QOMMUNITY_CONFIG", ""), help="JSON config file for long-running multi-date booking.")
    parser.add_argument("--facilities-config", default=os.environ.get("QOMMUNITY_FACILITIES_CONFIG", ""), help="Optional JSON file with facilities[] to merge with --config.")
    parser.add_argument("--bookings-config", default=os.environ.get("QOMMUNITY_BOOKINGS_CONFIG", ""), help="Optional JSON file with bookings[] to merge with --config.")
    parser.add_argument("--list-facilities", action="store_true", help="Fetch available facilities from the API and print them.")
    parser.add_argument("--write-facilities-config", default="", help="Fetch facilities and write a facilities[] config JSON file.")
    parser.add_argument("--log-file", default=os.environ.get("QOMMUNITY_LOG_FILE", DEFAULT_LOG_FILE), help='Mirror stdout/stderr to this file. Use "" to disable.')
    parser.add_argument("--no-color", action="store_true", help="Disable ANSI colors in terminal output.")
    parser.add_argument("--date", help="Booking date, e.g. 2026-07-26")
    parser.add_argument("--flow-file", default=DEFAULT_FLOW_FILE)
    parser.add_argument("--auth-file", default=os.environ.get("QOMMUNITY_AUTH_FILE", DEFAULT_AUTH_FILE), help="Saved auth JSON. Used before --flow-file when present.")
    parser.add_argument(
        "--auth-config",
        default=os.environ.get("QOMMUNITY_AUTH_CONFIG", ""),
        help="Auth config JSON. Defaults to auth_config.json, then --config or booking_base_config.json.",
    )
    parser.add_argument(
        "--login",
        nargs="?",
        const="email",
        choices=("email", "mobile"),
        default="",
        help="Request OTP, exchange it for a Bearer token, save --auth-file, then exit. Use --login email or --login mobile.",
    )
    parser.add_argument(
        "--auth-contact",
        default=os.environ.get("QOMMUNITY_AUTH_CONTACT", ""),
        help="Mobile/email contact used for OTP login. Can also be set with QOMMUNITY_AUTH_CONTACT.",
    )
    parser.add_argument("--auth-mobile-country-code", default=os.environ.get("QOMMUNITY_AUTH_MOBILE_COUNTRY_CODE", ""), help="Mobile country code used for mobile OTP login.")
    parser.add_argument("--auth-contact-type", default=os.environ.get("QOMMUNITY_AUTH_CONTACT_TYPE", ""), help="Qommunity auth contactType. Defaults to 2 for email, 1 for mobile.")
    parser.add_argument("--auth-client-id", default=os.environ.get("QOMMUNITY_AUTH_CLIENT_ID", DEFAULT_CLIENT_ID), help="Qommunity client_id used for OTP login.")
    parser.add_argument("--auth-accept-tc", action=argparse.BooleanOptionalAction, default=True, help="Send TCAccepted=true during token generation.")
    parser.add_argument("--otp", default="", help="OTP value. If omitted with --login, prompt interactively.")
    parser.add_argument("--otp-source", choices=("prompt", "worker"), default=os.environ.get("QOMMUNITY_OTP_SOURCE", ""), help="OTP source. Defaults to QOMMUNITY_OTP_SOURCE, otp.source in auth_config.json, else prompt.")
    parser.add_argument("--otp-worker-url", default=os.environ.get("QOMMUNITY_OTP_WORKER_URL", ""), help="Secret-protected Worker URL used by --otp-source worker.")
    parser.add_argument("--otp-secret", default=os.environ.get("QOMMUNITY_OTP_SECRET", ""), help="Worker read secret. Prefer --otp-secret-file.")
    parser.add_argument("--otp-secret-file", default=os.environ.get("QOMMUNITY_OTP_SECRET_FILE", ""), help="File containing the Worker read secret.")
    parser.add_argument("--otp-timeout-seconds", type=int, default=int(os.environ["QOMMUNITY_OTP_TIMEOUT_SECONDS"]) if os.environ.get("QOMMUNITY_OTP_TIMEOUT_SECONDS") else None, help="Seconds to wait for Worker OTP. Default: 180.")
    parser.add_argument("--otp-poll-interval", type=float, default=float(os.environ["QOMMUNITY_OTP_POLL_INTERVAL"]) if os.environ.get("QOMMUNITY_OTP_POLL_INTERVAL") else None, help="Seconds between Worker OTP polls. Default: 2.")
    parser.add_argument("--otp-regex", default=os.environ.get("QOMMUNITY_OTP_REGEX", ""), help=f"OTP regex. Default: {DEFAULT_OTP_REGEX}")
    parser.add_argument("--token", default="", help="Bearer token. Defaults to --auth-file when present, else latest token from flow file.")
    parser.add_argument(
        "--token-refresh-skew",
        type=int,
        default=TOKEN_REFRESH_SKEW_SECONDS,
        help="Reload captured token this many seconds before expiry. Use 0 to disable.",
    )
    parser.add_argument("--facility-id", default=TENNIS_COURT_3_ID)
    parser.add_argument("--payment-method", default="EstateCredit")
    parser.add_argument(
        "--preferred-start",
        action="append",
        default=[],
        help="Preferred start time. Can be repeated. Defaults to 08:00:00 then 07:00:00.",
    )
    parser.add_argument("--validate", action="store_true", help="POST validation when a slot is available.")
    parser.add_argument("--book", action="store_true", help="Actually confirm the booking.")
    parser.add_argument("--show-booking-id", default="", help="Fetch and print a booking by ID, then exit.")
    parser.add_argument("--cancel-booking-id", default="", help="Cancel a booking by ID, then fetch and print it.")
    parser.add_argument("--cancel-reason", default="Wrong timing", help="Reason sent with --cancel-booking-id.")
    parser.add_argument("--watch", action="store_true", help="Poll until booked or until --max-attempts is reached.")
    parser.add_argument("--interval", type=float, default=0.5, help="Polling interval in seconds for --watch.")
    parser.add_argument("--max-attempts", type=int, default=0, help="0 means unlimited in --watch mode.")
    parser.add_argument(
        "--due-window-seconds",
        type=int,
        default=int(os.environ.get("QOMMUNITY_DUE_WINDOW_SECONDS", str(DEFAULT_DUE_WINDOW_SECONDS))),
        help="With config mode, only run jobs whose probe start is within this many seconds. Default: 120.",
    )
    parser.add_argument(
        "--wait-until",
        default="",
        help="ISO timestamp to wait for before polling, e.g. 2026-06-28T00:00:00+08:00",
    )
    args = parser.parse_args()
    setup_output_logging(args.log_file, args.no_color)
    use_color = not args.no_color

    if args.login:
        return run_login(args)

    if args.list_facilities or args.write_facilities_config:
        session, _, _ = load_session(args)
        data = fetch_facilities(session)
        facilities = normalize_facilities(data)
        print_facilities(facilities)
        if args.write_facilities_config:
            Path(args.write_facilities_config).write_text(json.dumps({"facilities": facilities}, indent=2) + "\n")
            print(
                colorize("Wrote facilities config", Style.GREEN, use_color)
                + f" count={len(facilities)} path={args.write_facilities_config}",
                flush=True,
            )
        return 0

    if args.config:
        base_config = load_config(args.config)
        facilities_config = load_config(args.facilities_config) if args.facilities_config else None
        bookings_config = load_config(args.bookings_config) if args.bookings_config else None
        return run_config(args, merge_config(base_config, facilities_config, bookings_config))

    if not args.date and not args.show_booking_id and not args.cancel_booking_id:
        parser.error("--date is required unless --show-booking-id or --cancel-booking-id is used")

    preferred_starts = tuple(args.preferred_start or DEFAULT_PREFERRED_STARTS)

    wait_until = parse_wait_until(args.wait_until)
    if wait_until:
        print(colorize("Waiting until", Style.CYAN, use_color) + f" {wait_until.isoformat()}", flush=True)
        sleep_until(wait_until, no_color=args.no_color)

    session, token, fixed_token = load_session(args)

    if args.show_booking_id:
        result = get_booking(session, args.show_booking_id)
        print_booking_summary("Booking", result)
        print(json.dumps(result, indent=2), flush=True)
        return 0

    if args.cancel_booking_id:
        result = cancel_booking(session, args.cancel_booking_id, args.cancel_reason)
        print_booking_summary("Cancel result", result)
        print(json.dumps(result, indent=2), flush=True)
        status = get_booking(session, args.cancel_booking_id)
        print_booking_summary("Current booking", status)
        print(json.dumps(status, indent=2), flush=True)
        booking_status = ((status.get("data") or status).get("bookingStatus") or "").lower()
        return 0 if booking_status == "cancelled" else 1

    attempts = 0
    while True:
        attempts += 1
        print(
            colorize("CLI poll attempt start", Style.MAGENTA, use_color)
            + f" date={args.date} facility_id={args.facility_id} attempt={attempts} "
            f"at={dt.datetime.now().astimezone().isoformat(timespec='milliseconds')}",
            flush=True,
        )
        try:
            done, token = attempt_with_auth_retry(
                args=args,
                session=session,
                token=token,
                fixed_token=fixed_token,
                booking_date=args.date,
                facility_id=args.facility_id,
                preferred_starts=preferred_starts,
                payment_method=args.payment_method,
                do_validate=args.validate,
                do_book=args.book,
            )
            if done:
                return 0
        except ApiError as exc:
            print(f"{dt.datetime.now().isoformat(timespec='seconds')} ERROR {exc}", file=sys.stderr, flush=True)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"{dt.datetime.now().isoformat(timespec='seconds')} ERROR {exc}", file=sys.stderr, flush=True)

        if not args.watch:
            return 1
        if args.max_attempts and attempts >= args.max_attempts:
            print(colorize("Reached max attempts", Style.RED, use_color) + f" max_attempts={args.max_attempts}", flush=True)
            return 2
        print(
            colorize("CLI poll sleep", Style.GRAY, use_color)
            + f" date={args.date} attempt={attempts} interval={args.interval}s next_at="
            f"{(dt.datetime.now().astimezone() + dt.timedelta(seconds=args.interval)).isoformat(timespec='milliseconds')}",
            flush=True,
        )
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
