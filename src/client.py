from __future__ import annotations

import http.cookiejar
import html
import json
import mimetypes
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
import time
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

    def _profile_context(self) -> tuple[dict[str, Any], str, str]:
        payload = self.own_profile()
        profile = next(
            (item for item in payload.get("included", [])
             if str(item.get("$type", "")).endswith(".Profile")),
            None,
        )
        if not profile:
            raise LinkedInError("LinkedIn's profile response did not contain the current profile")
        profile_id = internal_profile_id(profile)
        version_tag = str(profile.get("versionTag") or "")
        if not profile_id or not version_tag:
            raise LinkedInError("could not determine the current profile ID and version")
        return profile, profile_id, version_tag

    def _curl_session(self):
        try:
            from curl_cffi import requests as curl_requests
        except ImportError:
            raise LinkedInError("curl_cffi is required for mutations; install requirements.txt") from None
        session = curl_requests.Session(impersonate="chrome")
        session.cookies.update(self.cookies)
        return session

    def _versioned_headers(self, profile: dict[str, Any]) -> dict[str, str]:
        headers = self._headers()
        headers.pop("Cookie", None)
        headers["Content-Type"] = "application/json"
        tracking_id = str(profile.get("trackingId") or "")
        headers["x-li-page-instance"] = (
            "urn:li:page:d_flagship3_profile_self_edit_top_card;" + tracking_id
        )
        return headers

    def _position_form_session(
        self, vanity_name: str, position_id: str,
    ) -> tuple[Any, str, dict[str, Any], dict[str, str]]:
        session = self._curl_session()
        form_url = f"{BASE}/in/{vanity_name}/details/experience/edit/forms/{position_id}"
        response = session.get(form_url, timeout=30)
        if response.status_code != 200:
            raise LinkedInError(f"LinkedIn experience form returned HTTP {response.status_code}")
        context_match = re.search(r'<meta name="como-t" content="([^"]+)">', response.text)
        try:
            context = json.loads(html.unescape(context_match.group(1))) if context_match else {}
        except json.JSONDecodeError:
            context = {}
        headers = self._headers()
        headers.pop("Cookie", None)
        current_jsession = session.cookies.get("JSESSIONID")
        if current_jsession:
            headers["csrf-token"] = current_jsession.strip('"')
        page_key = "d_flagship3_profile_self_edit_position"
        headers.update({
            "Accept": "*/*", "Content-Type": "application/json", "Origin": BASE,
            "Referer": form_url, "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin", "x-li-rsc-stream": "true",
            "x-li-anchor-page-key": page_key,
            "x-li-page-instance-tracking-id": context.get("trackingId", ""),
            "x-li-application-instance": context.get("appTrackingId", ""),
            "x-li-application-version": context.get("serviceVersion", ""),
            "x-li-page-instance": f"urn:li:page:{page_key};{context.get('trackingId', '')}",
        })
        return session, form_url, context, headers

    def _sdui_request(
        self, session: Any, headers: dict[str, str], request_id: str,
        server_request: dict[str, Any], states: list[dict[str, Any]],
    ) -> dict[str, Any]:
        requested = server_request["requestedArguments"]
        body = {
            "requestId": request_id,
            "serverRequest": server_request,
            "states": states,
            "requestedArguments": {
                **requested, "states": states,
                "screenId": "com.linkedin.sdui.flagshipnav.profile.ProfilePositionDetailsEditForm",
            },
        }
        url = BASE + "/flagship-web/rsc-action/actions/server-request?sduiid=" + urllib.parse.quote(request_id)
        response = session.post(url, headers=headers, json=body, timeout=30)
        if response.status_code != 200:
            detail = re.sub(r"\s+", " ", response.text[:500]).strip()
            raise LinkedInError(
                f"LinkedIn experience media request returned HTTP {response.status_code}"
                + (f": {detail}" if detail else "")
            )
        result = parse_rsc_response(response.text)
        errors = result.get("response", {}).get("errors") or []
        if errors:
            raise LinkedInError(f"LinkedIn rejected the experience media request: {errors[0]}")
        return result

    def add_position_image(
        self, position: dict[str, Any], profile: dict[str, Any], image_path: str | Path,
    ) -> str:
        """Upload an image and attach it as media to an existing experience."""
        path = Path(image_path).expanduser().resolve()
        info = image_file_info(path)
        position_id_value = position_id(position)
        profile_id = internal_profile_id(profile)
        vanity_name = profile.get("publicIdentifier")
        if not position_id_value or not profile_id or not vanity_name:
            raise LinkedInError("could not determine the profile and experience IDs")

        session, _, _, headers = self._position_form_session(vanity_name, position_id_value)
        pointer = uuid.uuid4().hex[:10]
        prefix = f"auto-binding-{uuid.uuid4()}ProfilePositionForm"
        media_file = {
            "$type": "proto.sdui.media.MediaFile", "mediaFileId": pointer,
            "filePointer": pointer, "mimeType": info["content_type"],
            "size": str(info["size"]), "fileName": path.name,
            "source": {"$case": "localFile", "localFile": {
                "$type": "proto.sdui.media.LocalFile", "filePointer": pointer,
                "mimeType": info["content_type"], "size": str(info["size"]),
                "fileName": path.name, "shouldIncludeFileContents": False,
                "fileContents": "",
            }},
            "thumbnails": [], "captions": [], "mediaAttributions": [],
        }
        ref = lambda key: {"key": key, "namespace": "MemoryNamespace"}
        followup_data = {
            "profileId": profile_id, "entityId": position_id_value,
            "vanityName": vanity_name, "assetUrns": ref("position-mediaAssetUrns"),
            "allMediaIds": ref("position-existingMediaId"),
            "mediaList": ref("position-mediaList"),
            "ingestedContentIds": ref("position-ingestedIds"),
            "uploadedDocumentList": ref("position-uploadedDocumentList"),
            "uploadedDocumentIds": ref("position-uploadedDocumentIds"),
            "existingMedia": [], "hasChanges": ref("isActiveProfileFormHasChangesProfileEditForm"),
            "hasExistingProfileSkills": True, "formIdPrefix": prefix,
            "sortedMediaIds": ref("position-mediaIds"),
            "isLoading": ref("isActiveProfileFormLoadingProfileEditForm"),
            "careerBreakAssociatedOrganizations": [{}],
            "mediaFileIdToIngestedContentMap": ref("position-fileIdToIngestedContentMap"),
            "ingestedMediaIdToBindingIdMap": ref("position-ingestedMediaIdToBindingIdMap"),
        }
        media_keys = ["position-mediaAssetUrns", "position-existingMediaId", "position-mediaList",
                      "position-ingestedIds", "position-uploadedDocumentList",
                      "position-uploadedDocumentIds", "position-mediaIds",
                      "position-fileIdToIngestedContentMap", "position-ingestedMediaIdToBindingIdMap"]
        requested = {
            "$type": "proto.sdui.actions.requests.RequestedArguments",
            "requestedStateKeys": [_requested_state(key) for key in ("position-mediaList", "position-mediaAssetUrns")],
            "payload": {
                "profileId": profile_id, "newMedia": ref("position-mediaList"),
                "assetUrns": ref("position-mediaAssetUrns"),
                "useCase": "ImageAssetUseCase_PROFILE_TREASURY_IMAGE",
                "isLoading": ref("isActiveProfileFormLoadingProfileEditForm"),
                "followUpRequest": {
                    "key": "com.linkedin.sdui.impl.profile.components.forms.uploadPositionMedia",
                    "data": followup_data,
                    "stateKeys": [ref(key) for key in media_keys],
                },
            },
            "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
        }
        initial_states = [
            _state("position-mediaList", {"$type": "proto.sdui.media.MediaList", "mediaFiles": [media_file]}, "mediaListValue"),
            _state("position-mediaAssetUrns", [], "stringListValue"),
        ]
        request_id = "com.linkedin.sdui.requests.profile.profileMediaRegister"
        register = self._sdui_request(session, headers, request_id, {
            "requestId": request_id, "requestedArguments": requested,
            "onClientRequestFailureAction": {"actions": []}, "isApfcEnabled": False,
            "isStreaming": False, "rumPageKey": "",
        }, initial_states)
        upload_action = find_sdui_action(register, "UploadMedia")
        if not upload_action:
            raise LinkedInError("LinkedIn did not return media upload instructions")
        try:
            instruction = upload_action["value"]["uploadInstructions"][0]
            asset = instruction["assetUrn"]
            single = instruction["uploadInstructions"]["ingestionOperations"][0]["uploadInstruction"]["instruction"]["singleRequestUploadInstruction"]
            upload_url = single["uploadUrl"]
            upload_headers = {item["key"]: item["value"] for item in single.get("uploadHeaders", [])}
            followup = next(
                action["value"] for action in upload_action["value"]["onSuccess"]["actions"]
                if str(action.get("$type", "")).endswith(".ServerRequest")
            )
        except (KeyError, IndexError, StopIteration, TypeError):
            raise LinkedInError("LinkedIn returned incomplete media upload instructions") from None
        upload_headers["Content-Type"] = info["content_type"]
        upload = session.put(upload_url, headers=upload_headers, data=path.read_bytes(), timeout=30)
        if upload.status_code not in {200, 201}:
            raise LinkedInError(f"LinkedIn media upload returned HTTP {upload.status_code}")

        empty_media = {"$type": "proto.sdui.media.MediaList", "mediaFiles": []}
        followup_states = [
            _state("position-mediaAssetUrns", [asset], "stringListValue"),
            _state("position-existingMediaId", [], "stringListValue"),
            _state("position-mediaList", {"$type": "proto.sdui.media.MediaList", "mediaFiles": [media_file]}, "mediaListValue"),
            _state("position-ingestedIds", [], "stringListValue"),
            _state("position-uploadedDocumentList", empty_media, "mediaListValue"),
            _state("position-uploadedDocumentIds", [], "stringListValue"),
            _state("position-mediaIds", [], "expression"),
            _state("position-fileIdToIngestedContentMap", [], "stringListValue"),
            _state("position-ingestedMediaIdToBindingIdMap", [], "stringListValue"),
        ]
        followup_result = self._sdui_request(
            session, headers, followup["requestId"], followup, followup_states,
        )
        save_request = find_sdui_action(followup_result, "ServerRequest")
        if not save_request:
            raise LinkedInError("LinkedIn did not return the final experience save request")
        save = save_request["value"]
        save_states = position_save_states(
            save["requestedArguments"]["payload"], position, profile, media_file, asset,
        )
        final = self._sdui_request(session, headers, save["requestId"], save, save_states)
        # Failure handlers are serialized alongside successful SDUI responses,
        # so nested ServerRequest actions are not themselves evidence of failure.
        # _sdui_request has already validated the response's actual error list.
        return str(asset)

    def update_position_organization(
        self, position: dict[str, Any], profile: dict[str, Any], company_id: str,
    ) -> None:
        if not re.fullmatch(r"\d+", company_id):
            raise LinkedInError("company ID must be numeric")
        position_id_value = position_id(position)
        profile_id = internal_profile_id(profile)
        vanity_name = profile.get("publicIdentifier")
        if not position_id_value or not profile_id or not vanity_name:
            raise LinkedInError("could not determine the profile and experience IDs")
        session, _, _, headers = self._position_form_session(vanity_name, position_id_value)
        prefix = f"auto-binding-{uuid.uuid4()}ProfilePositionForm"
        ref = lambda key: {"key": key, "namespace": "MemoryNamespace"}
        suffixes = {
            "allowProfileEditBroadcasts": "allowProfileEditBroadcasts",
            "title": "title", "employmentType": "employmentType",
            "isCompanyRequired": "companyRequired", "organizationEntityId": "organizationEntityId",
            "organizationId": "organizationId", "companyName": "companyName",
            "startDate": "startDate", "endDate": "endDate", "description": "description",
            "originalDescription": "initialDescription", "isCurrentPosition": "isCurrentPosition",
            "endCurrentPositionIds": "endCurrentPositionIds", "location": "location",
            "geoLocationId": "geoLocationId", "locationType": "locationType",
            "headline": "headline", "shouldDynamicallyCreateHeadline": "dynamicallyCreateHeadline",
            "jobSource": "jobSource", "showSourceOfHire": "displaySourceOfHire",
            "skills": "ReorderableSkillsFormBindingsskillIdsBinding",
            "isWevValidated": "isWevValidated", "aiSuggestionTrackingId": "aiSuggestionTrackingId",
        }
        references = {name: ref(prefix + suffix) for name, suffix in suffixes.items()}
        references.update({
            "uploadedMedia": ref("position-mediaList"),
            "uploadedMediaAssetUrns": ref("position-mediaAssetUrns"),
            "progressIndicator": ref("isActiveProfileFormLoadingProfileEditForm"),
        })
        payload = {
            "profileId": profile_id, "positionId": position_id_value,
            **references,
            "hasChanges": ref("isActiveProfileFormHasChangesProfileEditForm"),
            "hasExistingProfileSkills": True, "vanityName": vanity_name, "mediaItems": [],
        }
        requested = {
            "$type": "proto.sdui.actions.requests.RequestedArguments",
            "requestedStateKeys": [_requested_state(value["key"]) for value in references.values()],
            "payload": payload,
            "requestMetadata": {"$type": "proto.sdui.common.RequestMetadata"},
        }
        updated = dict(position)
        updated["companyUrn"] = f"urn:li:fsd_company:{company_id}"
        request_id = "com.linkedin.sdui.requests.profile.saveProfilePositionForm"
        server_request = {
            "requestId": request_id, "requestedArguments": requested,
            "onClientRequestFailureAction": {"actions": []}, "isApfcEnabled": False,
            "isStreaming": False, "rumPageKey": "",
        }
        states = position_save_states(payload, updated, profile)
        self._sdui_request(session, headers, request_id, server_request, states)

    def register_media_upload(self, path: Path, media_upload_type: str) -> dict[str, Any]:
        info = image_file_info(path)
        payload = self.request(
            "POST",
            "/voyager/api/voyagerMediaUploadMetadata",
            params={"action": "upload"},
            data={
                "mediaUploadType": media_upload_type,
                "fileSize": info["size"],
                "filename": path.name,
            },
        )
        try:
            metadata = payload["data"]["value"]
            upload_url = metadata["singleUploadUrl"]
            urn = metadata["urn"]
        except (KeyError, TypeError):
            raise LinkedInError("LinkedIn returned incomplete media upload metadata") from None
        if not str(urn).startswith("urn:li:digitalmediaAsset:") or not str(upload_url).startswith("https://"):
            raise LinkedInError("LinkedIn returned invalid media upload metadata")
        return metadata

    def upload_registered_media(self, path: Path, metadata: dict[str, Any]) -> None:
        info = image_file_info(path)
        headers = {
            **{str(k): str(v) for k, v in (metadata.get("singleUploadHeaders") or {}).items()},
            "Content-Type": info["content_type"],
            "Content-Length": str(info["size"]),
        }
        response = self._curl_session().put(
            metadata["singleUploadUrl"], headers=headers,
            data=path.read_bytes(), timeout=30,
        )
        if response.status_code not in {200, 201}:
            raise LinkedInError(f"LinkedIn media upload returned HTTP {response.status_code}")

    def update_background_image(self, image_path: str | Path) -> dict[str, str]:
        path = Path(image_path).expanduser().resolve()
        image_file_info(path)
        uploaded: dict[str, str] = {}
        for label, media_type in (
            ("original", "PROFILE_ORIGINAL_BACKGROUND"),
            ("display", "PROFILE_DISPLAY_BACKGROUND"),
        ):
            metadata = self.register_media_upload(path, media_type)
            self.upload_registered_media(path, metadata)
            uploaded[label] = str(metadata["urn"])

        profile, profile_id, version_tag = self._profile_context()
        body = {
            "patch": {
                "backgroundPicture": {
                    "$set": {
                        "originalImage": uploaded["original"],
                        "displayImage": uploaded["display"],
                    }
                }
            }
        }
        response = self._curl_session().post(
            f"{BASE}/voyager/api/identity/normProfiles/{profile_id}",
            params={"versionTag": version_tag},
            headers=self._versioned_headers(profile), json=body, timeout=30,
        )
        if response.status_code not in {200, 202}:
            detail = re.sub(r"\s+", " ", response.text[:500]).strip()
            raise LinkedInError(
                f"LinkedIn background update returned HTTP {response.status_code}"
                + (f": {detail}" if detail else "")
            )
        return uploaded

    def delete_project(self, entity_urn: str) -> None:
        if not re.fullmatch(r"urn:li:fsd_profileProject:\([^,]+,\d+\)", entity_urn):
            raise LinkedInError("invalid LinkedIn project URN")
        profile, _, version_tag = self._profile_context()
        response = self._curl_session().delete(
            f"{BASE}/voyager/api/identity/dash/profileProjects/{entity_urn}",
            params={"versionTag": version_tag},
            headers=self._versioned_headers(profile), timeout=30,
        )
        if response.status_code != 204:
            detail = re.sub(r"\s+", " ", response.text[:500]).strip()
            raise LinkedInError(
                f"LinkedIn project deletion returned HTTP {response.status_code}"
                + (f": {detail}" if detail else "")
            )

    def start_workplace_verification(self, email_address: str, company_id: str) -> str:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email_address):
            raise LinkedInError("invalid work email address")
        if not re.fullmatch(r"\d+", company_id):
            raise LinkedInError("company ID must be numeric")
        session = self._curl_session()
        headers = self._headers()
        headers.pop("Cookie", None)
        headers.update({"Content-Type": "application/json", "x-requested-with": "XMLHttpRequest"})
        company_urn = f"urn:li:fsd_company:{company_id}"
        verify = session.post(
            BASE + "/voyager/api/voyagerHiringDashOrganizationEmailVerifications/",
            params={"action": "verifyEmailAndCompany"}, headers=headers,
            json={"emailAddress": email_address, "flowUseCase": "EMPLOYEE_VERIFICATION", "companyUrn": company_urn},
            timeout=30,
        )
        if verify.status_code != 200:
            detail = re.sub(r"\s+", " ", verify.text[:500]).strip()
            raise LinkedInError(f"LinkedIn workplace email check returned HTTP {verify.status_code}" + (f": {detail}" if detail else ""))
        try:
            verification = verify.json()
        except ValueError:
            raise LinkedInError("LinkedIn returned an invalid workplace email response") from None
        result = verification.get("value") or verification.get("data") or verification
        verification_type = result.get("verificationType") if isinstance(result, dict) else None
        if verification_type and verification_type != "VERIFIED":
            raise LinkedInError(f"LinkedIn rejected the workplace email: {verification_type}")

        send = session.post(
            BASE + "/psettings/email/workEmailConfirmationMessages",
            headers=headers,
            json={"emailAddress": email_address, "emailKey": "email_job_posting_work_email_verification"},
            timeout=30,
        )
        if send.status_code not in {200, 201}:
            detail = re.sub(r"\s+", " ", send.text[:500]).strip()
            raise LinkedInError(f"LinkedIn verification-code request returned HTTP {send.status_code}" + (f": {detail}" if detail else ""))
        try:
            sent = send.json()
            challenge_id = sent.get("pinId") or sent.get("value") or sent.get("id")
            if isinstance(challenge_id, dict):
                challenge_id = challenge_id.get("pinId") or challenge_id.get("id")
        except ValueError:
            challenge_id = None
        if not challenge_id:
            raise LinkedInError("LinkedIn sent no verification challenge ID")
        return str(challenge_id)

    def complete_workplace_verification(
        self, email_address: str, company_id: str, challenge_id: str, code: str,
    ) -> None:
        if not re.fullmatch(r"\d{6}", code):
            raise LinkedInError("verification code must contain six digits")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", challenge_id):
            raise LinkedInError("invalid verification challenge ID")
        session = self._curl_session()
        headers = self._headers()
        headers.pop("Cookie", None)
        headers.update({"Content-Type": "application/json", "x-requested-with": "XMLHttpRequest"})
        verify = session.post(
            f"{BASE}/checkpoint/challenges/emailVerificationChallenge/{challenge_id}",
            params={"displayTime": int(time.time() * 1000)}, headers=headers,
            json={"pin": code}, timeout=30,
        )
        if verify.status_code != 200:
            detail = re.sub(r"\s+", " ", verify.text[:500]).strip()
            raise LinkedInError(f"LinkedIn code verification returned HTTP {verify.status_code}" + (f": {detail}" if detail else ""))
        try:
            pin_result = verify.json()
        except ValueError:
            raise LinkedInError("LinkedIn returned an invalid code-verification response") from None
        status = pin_result.get("status") or pin_result.get("value", {}).get("status")
        if status and status != "SUCCESS":
            raise LinkedInError(f"LinkedIn rejected the verification code: {status}")

        save = session.post(
            BASE + "/voyager/api/voyagerHiringDashOrganizationMemberVerifications/",
            params={"action": "saveEmail"}, headers=headers,
            json={
                "companyUrn": f"urn:li:fsd_company:{company_id}",
                "emailAddress": email_address, "challengeId": challenge_id,
                "flowUseCase": "EMPLOYEE_VERIFICATION",
            }, timeout=30,
        )
        if save.status_code not in {200, 201}:
            detail = re.sub(r"\s+", " ", save.text[:500]).strip()
            raise LinkedInError(f"LinkedIn workplace verification save returned HTTP {save.status_code}" + (f": {detail}" if detail else ""))

    def create_organization(
        self, *, name: str, universal_name: str, industry_id: str,
        industry_name: str, organization_size: str, organization_type: str,
        tagline: str = "", website: str = "", logo_path: str | Path | None = None,
    ) -> dict[str, Any]:
        if not name.strip() or len(name) > 100:
            raise LinkedInError("organization name must contain 1 to 100 characters")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", universal_name):
            raise LinkedInError("organization public name must use lowercase letters, numbers, and hyphens")
        if not re.fullmatch(r"\d+", industry_id):
            raise LinkedInError("industry ID must be numeric")
        if len(tagline) > 120:
            raise LinkedInError("organization tagline must be at most 120 characters")
        valid_sizes = {
            "SIZE_1", "SIZE_2_TO_10", "SIZE_11_TO_50", "SIZE_51_TO_200",
            "SIZE_201_TO_500", "SIZE_501_TO_1000", "SIZE_1001_TO_5000",
            "SIZE_5001_TO_10000", "SIZE_10001_OR_MORE",
        }
        valid_types = {
            "PUBLIC_COMPANY", "SELF_EMPLOYED", "GOVERNMENT_AGENCY", "NON_PROFIT",
            "SELF_OWNED", "PRIVATELY_HELD", "PARTNERSHIP",
        }
        if organization_size not in valid_sizes:
            raise LinkedInError("invalid organization size")
        if organization_type not in valid_types:
            raise LinkedInError("invalid organization type")

        session = self._curl_session()
        headers = self._headers()
        headers.pop("Cookie", None)
        headers.update({
            "Content-Type": "application/json", "Origin": BASE,
            "Referer": BASE + "/company/setup/new/", "x-requested-with": "XMLHttpRequest",
            "x-li-page-instance": "urn:li:page:flagship3_company_creation_form;",
        })
        eligibility = session.post(
            BASE + "/voyager/api/voyagerOrganizationDashEntryPageCreationForm",
            params={"action": "validatePageCreationForViewer"}, headers=headers,
            json={}, timeout=30,
        )
        if eligibility.status_code not in {200, 204}:
            detail = re.sub(r"\s+", " ", eligibility.text[:500]).strip()
            raise LinkedInError(f"LinkedIn Page eligibility check returned HTTP {eligibility.status_code}" + (f": {detail}" if detail else ""))

        logo_urn = ""
        if logo_path is not None:
            path = Path(logo_path).expanduser().resolve()
            metadata = self.register_media_upload(path, "COMPANY_LOGO")
            self.upload_registered_media(path, metadata)
            logo_urn = str(metadata["urn"])

        def text_input(item: str, value: str) -> dict[str, Any]:
            return {"formElementUrn": f"urn:li:fsu_pageCreationFormItem:{item}",
                    "formElementInputValues": [{"textInputValue": value}]}

        def entity_input(item: str, entity_name: str, entity_urn: str | None = None) -> dict[str, Any]:
            value = {"inputEntityName": entity_name}
            if entity_urn:
                value["inputEntityUrn"] = entity_urn
            return {"formElementUrn": f"urn:li:fsu_pageCreationFormItem:{item}",
                    "formElementInputValues": [{"entityInputValue": value}]}

        inputs = [
            text_input("NAME", name.strip()), text_input("UNIVERSAL_NAME", universal_name),
            entity_input("INDUSTRY", industry_name, f"urn:li:fsd_industry:{industry_id}"),
            entity_input("ORGANIZATION_SIZE", organization_size),
            entity_input("ORGANIZATION_TYPE", organization_type),
            entity_input("TERMS_AND_CONDITIONS", "TERMS_AND_CONDITIONS"),
        ]
        if website:
            inputs.append(text_input("WEBSITE", website))
        if tagline:
            inputs.append(text_input("TAGLINE", tagline))
        if logo_urn:
            inputs.append({
                "formElementUrn": "urn:li:fsu_pageCreationFormItem:LOGO",
                "formElementInputValues": [{"urnInputValue": logo_urn}],
            })
        response = session.post(
            BASE + "/voyager/api/voyagerOrganizationDashPageCreationForm",
            params={"action": "createOrganization"}, headers=headers,
            json={"organizationPageType": "COMPANY", "formElementInputs": inputs},
            timeout=30,
        )
        if response.status_code not in {200, 201}:
            detail = re.sub(r"\s+", " ", response.text[:800]).strip()
            raise LinkedInError(f"LinkedIn Page creation returned HTTP {response.status_code}" + (f": {detail}" if detail else ""))
        try:
            return response.json()
        except ValueError:
            return {"status": response.status_code, "location": response.headers.get("location")}

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


def image_file_info(value: str | Path) -> dict[str, Any]:
    path = Path(value).expanduser().resolve()
    if not path.is_file():
        raise LinkedInError(f"image file not found: {path}")
    size = path.stat().st_size
    if size == 0:
        raise LinkedInError("image file is empty")
    if size > 8 * 1024 * 1024:
        raise LinkedInError("image must be no larger than 8 MiB")
    with path.open("rb") as handle:
        signature = handle.read(16)
    if signature.startswith(b"\x89PNG\r\n\x1a\n"):
        content_type = "image/png"
    elif signature.startswith(b"\xff\xd8\xff"):
        content_type = "image/jpeg"
    else:
        guessed = mimetypes.guess_type(path.name)[0]
        if guessed not in {"image/png", "image/jpeg"}:
            raise LinkedInError("image must be a PNG or JPEG file")
        raise LinkedInError(f"file contents do not match {guessed}")
    return {"path": path, "size": size, "content_type": content_type}


def parse_rsc_response(text: str) -> dict[str, Any]:
    for line in text.splitlines():
        if ":" not in line:
            continue
        _, value = line.split(":", 1)
        if not value.startswith("{"):
            continue
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and "response" in parsed:
            return parsed
    raise LinkedInError("LinkedIn returned an unrecognized streaming response")


def find_sdui_action(value: Any, action_name: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if str(value.get("$type", "")).rsplit(".", 1)[-1] == action_name:
            return value
        for child in value.values():
            found = find_sdui_action(child, action_name)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_sdui_action(child, action_name)
            if found:
                return found
    return None


def _requested_state(key: str) -> dict[str, Any]:
    return {"key": {"value": {"$case": "id", "id": key}}}


def _state(key: str, value: Any, proto_case: str) -> dict[str, Any]:
    return {"key": key, "namespace": "MemoryNamespace", "value": value, "originalProtoCase": proto_case}


def position_id(entity: dict[str, Any]) -> str | None:
    match = re.search(r"profilePosition:\([^,]+,(\d+)\)", str(entity.get("entityUrn", "")))
    return match.group(1) if match else None


def position_save_states(
    payload: dict[str, Any], position: dict[str, Any], profile: dict[str, Any],
    media_file: dict[str, Any] | None = None, asset: str | None = None,
) -> list[dict[str, Any]]:
    def key(name: str) -> str:
        reference = payload.get(name)
        if not isinstance(reference, dict) or not reference.get("key"):
            raise LinkedInError(f"LinkedIn's save request omitted {name}")
        return str(reference["key"])

    company_number = _urn_number(position.get("companyUrn"), -1)
    start = _sdui_date(position.get("dateRange", {}).get("start"))
    end = _sdui_date(position.get("dateRange", {}).get("end"))
    current = position.get("dateRange", {}).get("end") is None
    values = [
        ("allowProfileEditBroadcasts", False, "booleanValue"),
        ("title", position.get("title", ""), "stringValue"),
        ("employmentType", str(_urn_number(position.get("employmentTypeUrn"), 0) or ""), "stringValue"),
        ("isCompanyRequired", False, "expression"),
        ("organizationEntityId", {"type": "bigint", "value": str(company_number if company_number >= 0 else 2**64 - 1)}, "longValue"),
        ("organizationId", company_number, "intValue"),
        ("companyName", position.get("companyName", ""), "stringValue"),
        ("startDate", start, "dateValue"), ("endDate", end, "dateValue"),
        ("description", position.get("description", ""), "stringValue"),
        ("originalDescription", position.get("description", ""), "stringValue"),
        ("isCurrentPosition", current, "booleanValue"),
        ("endCurrentPositionIds", [], "stringListValue"),
        ("location", position.get("locationName") or position.get("geoLocationName") or "", "stringValue"),
        ("geoLocationId", _urn_number(position.get("geoUrn"), 0), "intValue"),
        ("locationType", str(_urn_number(position.get("locationTypeUrn"), 0) or ""), "stringValue"),
        ("headline", profile.get("headline", ""), "stringValue"),
        ("shouldDynamicallyCreateHeadline", False, "booleanValue"),
        ("jobSource", "", "stringValue"), ("showSourceOfHire", False, "booleanValue"),
        ("skills", [], "stringListValue"),
        ("uploadedMedia", {"$type": "proto.sdui.media.MediaList", "mediaFiles": [media_file] if media_file else []}, "mediaListValue"),
        ("uploadedMediaAssetUrns", [asset] if asset else [], "stringListValue"),
        ("isWevValidated", True, "booleanValue"),
        ("progressIndicator", True, "booleanValue"),
    ]
    return [_state(key(name), value, proto) for name, value, proto in values]


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


def _treasury_image_url(value: Any) -> str | None:
    vector = value.get("data", {}).get("VectorImage", {}) if isinstance(value, dict) else {}
    artifacts = vector.get("artifacts") or []
    if vector.get("rootUrl") and artifacts:
        artifact = max(artifacts, key=lambda item: (item.get("width", 0), item.get("height", 0)))
        if artifact.get("fileIdentifyingUrlPathSegment"):
            return vector["rootUrl"] + artifact["fileIdentifyingUrlPathSegment"]
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
        if _type_name(item) == "Position":
            collection = by_urn.get(item.get("*profileTreasuryMediaPosition"), {})
            media = []
            for urn in collection.get("*elements", []):
                treasury = by_urn.get(urn, {})
                media.append({
                    "entity_urn": urn,
                    "url": _treasury_image_url(treasury),
                })
            if media:
                cleaned["media"] = media
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
