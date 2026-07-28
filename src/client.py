from __future__ import annotations

import http.cookiejar
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE = "https://www.linkedin.com"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
)

FULL_PROFILE_DECORATION = (
    "com.linkedin.voyager.dash.deco.identity.profile."
    "FullProfileWithEntities-93"
)
CONNECTIONS_DECORATION = (
    "com.linkedin.voyager.dash.deco.web.mynetwork."
    "ConnectionListWithProfile-16"
)


class LinkedInError(Exception):
    pass


def config_dir() -> Path:
    override = os.environ.get("LINKEDIN_CONFIG_DIR")
    path = Path(override) if override else Path(__file__).resolve().parents[1] / "config"
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def session_path() -> Path:
    return config_dir() / "session.json"


def normalize_profile_identity(value: str) -> str:
    value = value.strip()
    match = re.search(r"linkedin\.com/in/([^/?#]+)", value, re.IGNORECASE)
    if match:
        value = match.group(1)
    value = value.strip("/")
    if not value or any(character.isspace() for character in value):
        raise LinkedInError("profile must be a public profile ID or linkedin.com/in/... URL")
    return value


def parse_cookie_input(value: str) -> dict[str, str]:
    value = value.strip()
    if not value:
        raise LinkedInError("no cookie or token was provided")
    # A bare value is the li_at token. Otherwise accept a copied Cookie header.
    if ";" not in value and "=" not in value:
        return {"li_at": value.strip('"')}
    if value.lower().startswith("cookie:"):
        value = value.split(":", 1)[1].strip()
    cookies: dict[str, str] = {}
    for part in value.split(";"):
        if "=" not in part:
            continue
        name, cookie_value = part.strip().split("=", 1)
        if name:
            cookies[name] = cookie_value
    if "li_at" not in cookies:
        raise LinkedInError("cookie header does not contain li_at")
    return cookies


def save_session(cookies: dict[str, str]) -> None:
    path = session_path()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"cookies": cookies}) + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def load_session() -> dict[str, str]:
    path = session_path()
    if not path.exists():
        raise LinkedInError("not logged in. Run `linkedin login`")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cookies = data["cookies"]
        if not isinstance(cookies, dict) or "li_at" not in cookies:
            raise ValueError
        return {str(k): str(v) for k, v in cookies.items()}
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        raise LinkedInError("saved session is invalid. Run `linkedin login` again") from None


class Client:
    def __init__(self, cookies: dict[str, str] | None = None):
        self.cookies = dict(cookies if cookies is not None else load_session())

    def _headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.linkedin.normalized+json+2.1",
            "Accept-Language": "en-US,en;q=0.9",
            "Cookie": "; ".join(f"{key}={value}" for key, value in self.cookies.items()),
            "x-restli-protocol-version": "2.0.0",
            "x-li-lang": "en_US",
        }
        jsession = self.cookies.get("JSESSIONID")
        if jsession:
            headers["csrf-token"] = jsession.strip('"')
        return headers

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, params=params)

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> Any:
        if not path.startswith("/voyager/api/"):
            raise LinkedInError("only /voyager/api/ endpoints are allowed")
        method = method.upper()
        if method not in {"GET", "POST", "PUT", "DELETE"}:
            raise LinkedInError(f"unsupported HTTP method: {method}")
        url = BASE + path
        if params:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
        headers = self._headers()
        body = None
        if data is not None:
            body = json.dumps(data, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, headers=headers, data=body, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            body = exc.read(500).decode("utf-8", "replace")
            if exc.code in (401, 403):
                raise LinkedInError("session expired or was rejected. Run `linkedin login` again") from None
            if exc.code == 429:
                raise LinkedInError("LinkedIn rate limited the request; wait before retrying") from None
            if exc.code == 999:
                raise LinkedInError("LinkedIn blocked the request (HTTP 999); wait and re-authenticate if needed") from None
            detail = re.sub(r"\s+", " ", body).strip()[:240]
            raise LinkedInError(f"LinkedIn returned HTTP {exc.code}" + (f": {detail}" if detail else "")) from None
        except urllib.error.URLError as exc:
            raise LinkedInError(f"could not reach LinkedIn: {exc.reason}") from None
        except json.JSONDecodeError:
            raise LinkedInError("LinkedIn returned an unexpected non-JSON response") from None

    def me(self) -> dict[str, Any]:
        return self.get("/voyager/api/me")

    def invitations(self, count: int = 20, start: int = 0) -> dict[str, Any]:
        return self.get(
            "/voyager/api/relationships/invitationViews",
            {"q": "receivedInvitation", "start": start, "count": count},
        )

    def profile(self, identity: str | None = None) -> dict[str, Any]:
        if identity is None:
            identity = me_summary(self.me()).get("id")
        else:
            identity = normalize_profile_identity(identity)
        if not identity:
            raise LinkedInError("LinkedIn did not return the account's public profile ID")
        return self.get(
            "/voyager/api/identity/dash/profiles",
            {
                "q": "memberIdentity",
                "memberIdentity": identity,
                "decorationId": FULL_PROFILE_DECORATION,
            },
        )

    def own_profile(self) -> dict[str, Any]:
        return self.profile()

    def connections(self, count: int = 40, start: int = 0) -> dict[str, Any]:
        return self.get(
            "/voyager/api/relationships/dash/connections",
            {
                "q": "search",
                "start": start,
                "count": count,
                "sortType": "RECENTLY_ADDED",
                "decorationId": CONNECTIONS_DECORATION,
            },
        )

    def update_education(
        self, profile_id: str, vanity_name: str, education_id: str,
        data: dict[str, Any],
    ) -> Any:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", profile_id):
            raise LinkedInError("invalid internal profile ID")
        if not re.fullmatch(r"\d+", education_id):
            raise LinkedInError("invalid education ID")
        vanity_name = normalize_profile_identity(vanity_name)

        try:
            from curl_cffi import requests as curl_requests
        except ImportError:
            raise LinkedInError("curl_cffi is required for profile mutations; install requirements.txt") from None
        session = curl_requests.Session(impersonate="chrome")
        session.cookies.update(self.cookies)
        form_url = f"{BASE}/in/{vanity_name}/edit/forms/education/{education_id}/"
        form_response = session.get(form_url, timeout=30)
        if form_response.status_code != 200:
            raise LinkedInError(f"LinkedIn education form returned HTTP {form_response.status_code}")
        match = re.search(
            r'(auto-binding-[A-Za-z0-9-]+education\.dataAwareAddEducationFormComponent)'
            r'ReorderableSkillsFormBindings',
            form_response.text,
        )
        if not match:
            raise LinkedInError("could not discover LinkedIn's current education form bindings")
        prefix = match.group(1)
        context_match = re.search(r'<meta name="como-t" content="([^"]+)">', form_response.text)
        try:
            page_context = json.loads(html.unescape(context_match.group(1))) if context_match else {}
        except json.JSONDecodeError:
            page_context = {}
        page_key = "d_flagship3_profile_self_edit_education"
        fields = {
            "allowProfileEditBroadcasts": (False, "booleanValue"),
            "schoolName": (data.get("schoolName", ""), "stringValue"),
            "organizationId": (_urn_number(data.get("companyUrn") or data.get("schoolUrn"), -1), "intValue"),
            "organizationEntityId": ({"type": "bigint", "value": str(_urn_number(data.get("companyUrn") or data.get("schoolUrn"), -1))}, "longValue"),
            "degree": (data.get("degreeName", ""), "stringValue"),
            "degreeId": (_urn_number(data.get("degreeUrn"), -1), "intValue"),
            "fieldOfStudy": (data.get("fieldOfStudy", ""), "stringValue"),
            "fieldOfStudyId": (_urn_number(data.get("standardizedFieldOfStudyUrn"), 0), "intValue"),
            "startDate": (_sdui_date(data.get("dateRange", {}).get("start")), "dateValue"),
            "endDate": (_sdui_date(data.get("dateRange", {}).get("end")), "dateValue"),
            "grade": (data.get("grade", ""), "stringValue"),
            "activities": (data.get("activities", ""), "stringValue"),
            "description": (data.get("description", ""), "stringValue"),
            "ReorderableSkillsFormBindingsskillIdsBinding": ([], "expression"),
        }
        refs = {name: {"key": prefix + name, "namespace": "MemoryNamespace"} for name in fields}
        requested_state_keys = [{"key": {"value": {"$case": "id", "id": prefix + name}}} for name in fields]
        states = [{
            "key": prefix + name, "namespace": "MemoryNamespace", "value": value,
            "originalProtoCase": proto_case,
        } for name, (value, proto_case) in fields.items()]
        requested_arguments = {
            "$type": "proto.sdui.actions.requests.RequestedArguments",
            "requestedStateKeys": requested_state_keys,
            "payload": {
                "allowProfileEditBroadcasts": refs["allowProfileEditBroadcasts"],
                "schoolName": refs["schoolName"],
                "organizationId": refs["organizationId"],
                "organizationEntityId": refs["organizationEntityId"],
                "degree": refs["degree"], "degreeId": refs["degreeId"],
                "fieldOfStudy": refs["fieldOfStudy"], "fieldOfStudyId": refs["fieldOfStudyId"],
                "startDate": refs["startDate"], "endDate": refs["endDate"],
                "grade": refs["grade"], "activities": refs["activities"],
                "description": refs["description"],
                "skills": refs["ReorderableSkillsFormBindingsskillIdsBinding"],
                "mediaItems": [], "educationId": education_id, "profileId": profile_id,
                "vanityName": vanity_name, "profileFormEntryPoint": "TopLevel",
                "hasChanges": {"key": "isActiveProfileFormHasChangesProfileEditForm", "namespace": "MemoryNamespace"},
                "hasExistingProfileSkills": False,
                "progressIndicator": {"key": "isActiveProfileFormLoadingProfileEditForm", "namespace": "MemoryNamespace"},
            },
            "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
        }
        request_id = "com.linkedin.sdui.requests.profile.saveProfileEducationForm"
        body = {
            "requestId": request_id,
            "serverRequest": {
                "requestId": request_id,
                "requestedArguments": requested_arguments,
                "onClientRequestFailureAction": {"actions": []},
                "isApfcEnabled": False,
                "isStreaming": False,
                "rumPageKey": "",
            },
            "states": states,
            "requestedArguments": {
                **requested_arguments,
                "states": states,
                "screenId": "com.linkedin.sdui.flagshipnav.profile.ProfileEducationEditForm",
            },
        }
        headers = self._headers()
        headers.pop("Cookie", None)
        current_jsession = session.cookies.get("JSESSIONID")
        if current_jsession:
            headers["csrf-token"] = current_jsession.strip('"')
        headers.update({
            "Accept": "*/*",
            "Content-Type": "application/json",
            "Origin": BASE,
            "Referer": form_url,
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "x-li-rsc-stream": "true",
            "x-li-anchor-page-key": page_key,
            "x-li-page-instance-tracking-id": page_context.get("trackingId", ""),
            "x-li-application-instance": page_context.get("appTrackingId", ""),
            "x-li-application-version": page_context.get("serviceVersion", ""),
            "x-li-page-instance": f"urn:li:page:{page_key};{page_context.get('trackingId', '')}",
            "x-li-track": json.dumps({
                "clientVersion": page_context.get("serviceVersion", ""),
                "mpVersion": page_context.get("serviceVersion", ""),
                "osName": "web", "timezoneOffset": -5,
                "timezone": "America/Panama", "deviceFormFactor": "DESKTOP",
                "mpName": "web", "displayDensity": 2,
                "displayWidth": 3024, "displayHeight": 1964,
            }, separators=(",", ":")),
        })
        url = BASE + "/flagship-web/rsc-action/actions/server-request?sduiid=" + urllib.parse.quote(request_id)
        response = session.post(url, headers=headers, json=body, timeout=30)
        if response.status_code != 200:
            detail = re.sub(r"\s+", " ", response.text[:500]).strip()
            raise LinkedInError(f"LinkedIn education update returned HTTP {response.status_code}" + (f": {detail}" if detail else ""))
        if "Something went wrong. Please try again." in response.text or "Save failed" in response.text:
            raise LinkedInError("LinkedIn rejected the education update")
        return response.text


def mini_profiles(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item for item in payload.get("included", [])
        if str(item.get("$type", "")).endswith("MiniProfile")
    ]


def me_summary(payload: dict[str, Any]) -> dict[str, Any]:
    profiles = mini_profiles(payload)
    profile = profiles[0] if profiles else {}
    public_id = profile.get("publicIdentifier")
    return {
        "id": public_id or profile.get("entityUrn"),
        "member_id": payload.get("data", {}).get("plainId"),
        "name": " ".join(x for x in (profile.get("firstName"), profile.get("lastName")) if x),
        "headline": profile.get("occupation"),
        "url": f"https://www.linkedin.com/in/{public_id}/" if public_id else None,
        "premium": payload.get("data", {}).get("premiumSubscriber"),
    }


def _type_name(item: dict[str, Any]) -> str:
    return str(item.get("$type", "")).rsplit(".", 1)[-1]


def _date(value: Any) -> str | None:
    if not isinstance(value, dict) or not value.get("year"):
        return None
    parts = [str(value["year"])]
    if value.get("month"):
        parts.append(f"{int(value['month']):02d}")
    if value.get("day"):
        parts.append(f"{int(value['day']):02d}")
    return "-".join(parts)


def _image_url(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("displayImageReference", "originalImageReference"):
        vector = value.get(key, {}).get("vectorImage", {})
        artifacts = vector.get("artifacts") or []
        if vector.get("rootUrl") and artifacts:
            artifact = max(artifacts, key=lambda x: (x.get("width", 0), x.get("height", 0)))
            segment = artifact.get("fileIdentifyingUrlPathSegment")
            if segment:
                return vector["rootUrl"] + segment
    return None


def _clean(value: Any) -> Any:
    """Remove Rest.li recipe noise while preserving unfamiliar profile fields."""
    if isinstance(value, list):
        return [_clean(x) for x in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, child in value.items():
        if key.startswith("$") or key.startswith("*") or key in {"trackingId", "versionTag"}:
            continue
        result[key] = _clean(child)
    return result


def full_profile_summary(payload: dict[str, Any]) -> dict[str, Any]:
    included = payload.get("included", [])
    profile = next((x for x in included if _type_name(x) == "Profile"), None)
    if not profile:
        raise LinkedInError("LinkedIn's profile response did not contain a profile")

    by_urn = {x.get("entityUrn"): x for x in included if x.get("entityUrn")}
    geo = by_urn.get(profile.get("geoLocation", {}).get("*geo"), {})
    industry = by_urn.get(profile.get("*industry"), {})
    public_id = profile.get("publicIdentifier")

    section_names = {
        "Position": "experience",
        "PositionGroup": "experience_groups",
        "Education": "education",
        "Certification": "certifications",
        "Skill": "skills",
        "Language": "languages",
        "Project": "projects",
        "Publication": "publications",
        "VolunteerExperience": "volunteering",
        "Honor": "honors",
        "Course": "courses",
        "Patent": "patents",
        "TestScore": "test_scores",
        "Organization": "organizations",
    }
    sections: dict[str, list[dict[str, Any]]] = {name: [] for name in section_names.values()}
    for item in included:
        section = section_names.get(_type_name(item))
        if not section:
            continue
        cleaned = _clean(item)
        date_range = item.get("dateRange")
        if isinstance(date_range, dict):
            cleaned["start"] = _date(date_range.get("start"))
            cleaned["end"] = _date(date_range.get("end"))
            cleaned["current"] = date_range.get("end") is None
            cleaned.pop("dateRange", None)
        sections[section].append(cleaned)

    return {
        "id": public_id or profile.get("entityUrn"),
        "member_id": str(profile.get("objectUrn", "")).rsplit(":", 1)[-1] or None,
        "name": " ".join(x for x in (profile.get("firstName"), profile.get("lastName")) if x),
        "first_name": profile.get("firstName"),
        "last_name": profile.get("lastName"),
        "headline": profile.get("headline"),
        "about": profile.get("summary"),
        "location": geo.get("defaultLocalizedName") or profile.get("locationName"),
        "country_code": profile.get("location", {}).get("countryCode"),
        "industry": industry.get("name"),
        "industry_urn": profile.get("industryUrn"),
        "url": f"https://www.linkedin.com/in/{public_id}/" if public_id else None,
        "photo_url": _image_url(profile.get("profilePicture")),
        "background_photo_url": _image_url(profile.get("backgroundPicture")),
        "premium": profile.get("premium"),
        "creator": profile.get("creator"),
        "influencer": profile.get("influencer"),
        "supported_locales": _clean(profile.get("supportedLocales") or []),
        "sections": sections,
    }


def connection_summaries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    included = payload.get("included", [])
    profiles = {
        item.get("entityUrn"): item for item in included
        if _type_name(item) == "Profile" and item.get("entityUrn")
    }
    rows = []
    for connection in included:
        if _type_name(connection) != "Connection":
            continue
        profile_urn = (connection.get("*connectedMemberResolutionResult")
                       or connection.get("connectedMember"))
        profile = profiles.get(profile_urn, {})
        public_id = profile.get("publicIdentifier")
        created = connection.get("createdAt")
        connected_at = None
        if isinstance(created, (int, float)):
            connected_at = datetime.fromtimestamp(created / 1000, timezone.utc).isoformat().replace("+00:00", "Z")
        rows.append({
            "id": public_id or profile_urn,
            "name": " ".join(x for x in (profile.get("firstName"), profile.get("lastName")) if x),
            "headline": profile.get("headline"),
            "url": f"https://www.linkedin.com/in/{public_id}/" if public_id else None,
            "photo_url": _image_url(profile.get("profilePicture")),
            "profile_urn": profile_urn,
            "connection_urn": connection.get("entityUrn"),
            "connected_at": connected_at,
        })
    rows.sort(key=lambda row: row.get("connected_at") or "", reverse=True)
    return rows


def education_id(entity: dict[str, Any]) -> str | None:
    urn = str(entity.get("entityUrn", ""))
    match = re.search(r",(\d+)\)$", urn)
    return match.group(1) if match else None


def _urn_number(value: Any, default: int) -> int:
    match = re.search(r":(\d+)$", str(value or ""))
    return int(match.group(1)) if match else default


def _sdui_date(value: Any) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    return {
        "year": int(value.get("year", 0)), "month": int(value.get("month", 0)),
        "day": int(value.get("day", 0)), "$type": "proto.sdui.common.Date",
    }


def internal_profile_id(entity: dict[str, Any]) -> str | None:
    urn = str(entity.get("entityUrn", ""))
    return urn.rsplit(":", 1)[-1] if urn.startswith("urn:li:fsd_profile:") else None


def education_update_payload(
    entity: dict[str, Any], *, start: str | None = None, end: str | None = None,
    degree: str | None = None, field: str | None = None, grade: str | None = None,
    activities: str | None = None, description: str | None = None,
    organization_id: str | None = None,
) -> dict[str, Any]:
    def parse_month(value: str | None, label: str) -> dict[str, int] | None:
        if value is None:
            return None
        match = re.fullmatch(r"(\d{4})-(0[1-9]|1[0-2])", value)
        if not match:
            raise LinkedInError(f"{label} must use YYYY-MM")
        return {"year": int(match.group(1)), "month": int(match.group(2))}

    old_range = entity.get("dateRange") if isinstance(entity.get("dateRange"), dict) else {}
    start_value = parse_month(start, "--start") if start is not None else old_range.get("start")
    end_value = parse_month(end, "--end") if end is not None else old_range.get("end")
    if start_value and end_value and (start_value["year"], start_value.get("month", 0)) > (end_value["year"], end_value.get("month", 0)):
        raise LinkedInError("education start date cannot be after its end date")
    if organization_id is not None and not re.fullmatch(r"\d+", organization_id):
        raise LinkedInError("--organization-id must be a numeric LinkedIn company ID")
    payload = {
        "schoolName": entity.get("schoolName"),
        "schoolUrn": entity.get("schoolUrn"),
        "companyUrn": (f"urn:li:fsd_company:{organization_id}"
                       if organization_id is not None else entity.get("companyUrn")),
        "degreeName": entity.get("degreeName") if degree is None else degree,
        "degreeUrn": entity.get("degreeUrn"),
        "fieldOfStudy": entity.get("fieldOfStudy") if field is None else field,
        "standardizedFieldOfStudyUrn": entity.get("standardizedFieldOfStudyUrn"),
        "grade": entity.get("grade") if grade is None else grade,
        "activities": entity.get("activities") if activities is None else activities,
        "description": entity.get("description") if description is None else description,
        "dateRange": {"start": start_value, "end": end_value},
    }
    return {key: value for key, value in payload.items() if value is not None or key == "dateRange"}
