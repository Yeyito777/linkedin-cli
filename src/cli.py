from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from typing import Any

from .client import (
    Client, LinkedInError, connection_summaries, education_id, education_update_payload,
    full_profile_summary, image_file_info, internal_profile_id, me_summary, mini_profiles,
    parse_cookie_input, position_id, save_session, session_path,
)


def emit_json(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def print_profile(profile: dict[str, Any], *, authenticated: bool = False) -> None:
    if authenticated:
        print("Authenticated.")
    print(f"  Name:       {profile.get('name') or '-'}")
    print(f"  Profile ID: {profile.get('id') or '-'}")
    print(f"  Member ID:  {profile.get('member_id') or '-'}")
    print(f"  Headline:   {profile.get('headline') or '-'}")
    print(f"  URL:        {profile.get('url') or '-'}")
    if profile.get("premium") is not None:
        print(f"  Premium:    {'yes' if profile['premium'] else 'no'}")


def command_login(args: argparse.Namespace) -> None:
    if args.cookie_stdin:
        raw = sys.stdin.read()
    elif not sys.stdin.isatty():
        raw = sys.stdin.read()
    else:
        print("Paste either the li_at value or a full LinkedIn Cookie header.")
        print("The value is stored locally with mode 0600 and is never printed.")
        raw = getpass.getpass("Cookie/token: ")
    cookies = parse_cookie_input(raw)
    payload = Client(cookies).me()
    save_session(cookies)
    print_profile(me_summary(payload), authenticated=True)


def command_logout(args: argparse.Namespace) -> None:
    path = session_path()
    if path.exists():
        path.unlink()
    print("Logged out.")


def command_me(args: argparse.Namespace) -> None:
    payload = Client().me()
    if args.json:
        emit_json(payload if args.raw else me_summary(payload))
    else:
        print_profile(me_summary(payload))


def command_status(args: argparse.Namespace) -> None:
    payload = Client().me()
    summary = me_summary(payload)
    if args.json:
        emit_json({"authenticated": True, **summary})
    else:
        print_profile(summary, authenticated=True)


def command_invitations(args: argparse.Namespace) -> None:
    payload = Client().invitations(args.limit, args.start)
    if args.raw:
        emit_json(payload)
        return
    profiles = mini_profiles(payload)
    rows = [{
        "id": p.get("publicIdentifier") or p.get("entityUrn"),
        "name": " ".join(x for x in (p.get("firstName"), p.get("lastName")) if x),
        "headline": p.get("occupation"),
        "url": "https://www.linkedin.com/in/" + p["publicIdentifier"] + "/" if p.get("publicIdentifier") else None,
    } for p in profiles]
    if args.json:
        emit_json(rows)
        return
    if not rows:
        print("No pending invitations.")
        return
    for row in rows:
        print(f"  {str(row['id'] or '-'):<32.32}  {str(row['name'] or '-'):<24.24}  {row['headline'] or '-'}")


def command_profile_show(args: argparse.Namespace) -> None:
    payload = Client().profile(args.person)
    if args.raw:
        emit_json(payload)
        return
    profile = full_profile_summary(payload)
    if args.json:
        emit_json(profile)
        return
    print(f"  Name:       {profile.get('name') or '-'}")
    print(f"  Profile ID: {profile.get('id') or '-'}")
    print(f"  Headline:   {profile.get('headline') or '-'}")
    print(f"  Location:   {profile.get('location') or '-'}")
    print(f"  Industry:   {profile.get('industry') or '-'}")
    print(f"  URL:        {profile.get('url') or '-'}")
    if profile.get("about"):
        print("\n  About")
        for line in profile["about"].splitlines():
            print(f"    {line}")
    labels = {
        "experience": "Experience", "education": "Education",
        "certifications": "Certifications", "skills": "Skills",
        "languages": "Languages", "projects": "Projects",
        "publications": "Publications", "volunteering": "Volunteering",
        "honors": "Honors", "courses": "Courses", "patents": "Patents",
        "test_scores": "Test scores", "organizations": "Organizations",
    }
    for key, label in labels.items():
        rows = profile["sections"].get(key) or []
        if not rows:
            continue
        print(f"\n  {label}")
        for row in rows:
            title = (row.get("title") or row.get("name") or row.get("schoolName")
                     or row.get("degreeName") or row.get("fieldOfStudy") or row.get("entityUrn") or "-")
            organization = row.get("companyName") or row.get("schoolName") or row.get("authority")
            date = " – ".join(x for x in (row.get("start"), row.get("end") or ("Present" if row.get("current") else None)) if x)
            print(f"    {title}" + (f" — {organization}" if organization and organization != title else ""))
            if date:
                print(f"      {date}")
            if row.get("fieldOfStudy") and row.get("fieldOfStudy") != title:
                print(f"      {row['fieldOfStudy']}")
            if row.get("description"):
                for line in str(row["description"]).splitlines():
                    print(f"      {line}")


def command_connection_list(args: argparse.Namespace) -> None:
    # Fetch a broad page, then apply the human-friendly search locally. LinkedIn's
    # server query uses an unstable structured filter rather than a plain keyword.
    payload = Client().connections(count=100, start=args.start)
    if args.raw:
        emit_json(payload)
        return
    rows = connection_summaries(payload)
    if args.search:
        terms = args.search.casefold().split()
        rows = [row for row in rows if all(term in " ".join(
            str(row.get(key) or "") for key in ("name", "headline", "id")
        ).casefold() for term in terms)]
    rows = rows[:args.limit]
    if args.json:
        emit_json(rows)
        return
    if not rows:
        print("No matching connections." if args.search else "No connections found.")
        return
    for row in rows:
        print(f"  {str(row['id'] or '-'):<28.28}  {str(row['name'] or '-'):<32.32}  {row['headline'] or '-'}")


def command_education_update(args: argparse.Namespace) -> None:
    client = Client()
    raw_profile = client.own_profile()
    profile_entity = next((x for x in raw_profile.get("included", []) if str(x.get("$type", "")).endswith(".Profile")), None)
    educations = [x for x in raw_profile.get("included", []) if str(x.get("$type", "")).endswith(".Education")]
    matches = [x for x in educations if education_id(x) == args.education or x.get("schoolName", "").casefold() == args.education.casefold()]
    if len(matches) != 1:
        choices = ", ".join(f"{education_id(x)} ({x.get('schoolName')})" for x in educations) or "none"
        raise LinkedInError(f"education '{args.education}' was not uniquely found. Available: {choices}")
    entity = matches[0]
    payload = education_update_payload(
        entity, start=args.start, end=args.end, degree=args.degree,
        field=args.field, grade=args.grade, activities=args.activities,
        description=args.description, organization_id=args.organization_id,
    )
    preview = {
        "education_id": education_id(entity),
        "school": entity.get("schoolName"),
        "changes": {key: value for key, value in {
            "start": args.start, "end": args.end, "degree": args.degree,
            "field": args.field, "grade": args.grade,
            "activities": args.activities, "description": args.description,
            "organization_id": args.organization_id,
        }.items() if value is not None},
    }
    if not preview["changes"]:
        raise LinkedInError("no changes were supplied")
    if not args.yes:
        if args.json:
            emit_json({"preview": True, **preview})
        else:
            print(f"Proposed education update: {preview['school']} ({preview['education_id']})")
            for key, value in preview["changes"].items():
                print(f"  {key.capitalize():<12} {value}")
            print("\nNo changes made. Run again with --yes to apply.")
        return
    profile_id = internal_profile_id(profile_entity or {})
    if not profile_id:
        raise LinkedInError("could not determine the account's internal profile ID")
    vanity_name = profile_entity.get("publicIdentifier") if profile_entity else None
    if not vanity_name:
        raise LinkedInError("could not determine the account's public profile ID")
    client.update_education(profile_id, vanity_name, education_id(entity) or "", payload)
    print(f"Updated. Education ID: {education_id(entity)}")


def command_background_update(args: argparse.Namespace) -> None:
    info = image_file_info(args.image)
    preview = {
        "image": str(info["path"]),
        "content_type": info["content_type"],
        "bytes": info["size"],
    }
    if not args.yes:
        if args.json:
            emit_json({"preview": True, **preview})
        else:
            print("Proposed profile background update:")
            print(f"  Image: {preview['image']}")
            print(f"  Type:  {preview['content_type']}")
            print(f"  Size:  {preview['bytes']} bytes")
            print("\nNo changes made. Run again with --yes to apply.")
        return
    Client().update_background_image(info["path"])
    print("Updated profile background image.")


def command_project_delete(args: argparse.Namespace) -> None:
    client = Client()
    profile = full_profile_summary(client.own_profile())
    projects = profile.get("sections", {}).get("projects", [])
    matches = [project for project in projects if (
        str(project.get("entityUrn", "")).endswith(f",{args.project})")
        or str(project.get("title", "")).casefold() == args.project.casefold()
    )]
    if len(matches) != 1:
        choices = ", ".join(
            f"{str(project.get('entityUrn', '')).rsplit(',', 1)[-1].rstrip(')')} ({project.get('title')})"
            for project in projects
        ) or "none"
        raise LinkedInError(
            f"project '{args.project}' was not uniquely found. Available: {choices}"
        )
    project = matches[0]
    preview = {
        "project_id": str(project["entityUrn"]).rsplit(",", 1)[-1].rstrip(")"),
        "title": project.get("title"),
        "entity_urn": project.get("entityUrn"),
    }
    if not args.yes:
        if args.json:
            emit_json({"preview": True, **preview})
        else:
            print(f"Proposed project deletion: {preview['title']} ({preview['project_id']})")
            print("\nNo changes made. Run again with --yes to apply.")
        return
    client.delete_project(str(preview["entity_urn"]))
    print(f"Deleted. Project ID: {preview['project_id']}")


def command_experience_media_add(args: argparse.Namespace) -> None:
    client = Client()
    raw = client.own_profile()
    profile = next((item for item in raw.get("included", []) if str(item.get("$type", "")).endswith(".Profile")), None)
    positions = [item for item in raw.get("included", []) if str(item.get("$type", "")).endswith(".Position")]
    matches = [item for item in positions if (
        position_id(item) == args.experience
        or str(item.get("companyName", "")).casefold() == args.experience.casefold()
        or str(item.get("title", "")).casefold() == args.experience.casefold()
    )]
    if len(matches) != 1:
        choices = ", ".join(f"{position_id(item)} ({item.get('title')} at {item.get('companyName')})" for item in positions) or "none"
        raise LinkedInError(f"experience '{args.experience}' was not uniquely found. Available: {choices}")
    if not profile:
        raise LinkedInError("LinkedIn's profile response did not contain the current profile")
    info = image_file_info(args.image)
    preview = {
        "experience_id": position_id(matches[0]), "title": matches[0].get("title"),
        "company": matches[0].get("companyName"), "image": str(info["path"]),
        "content_type": info["content_type"], "bytes": info["size"],
    }
    if not args.yes:
        if args.json:
            emit_json({"preview": True, **preview})
        else:
            print(f"Proposed media attachment: {preview['title']} at {preview['company']}")
            print(f"  Image: {preview['image']}\n  Type:  {preview['content_type']}\n  Size:  {preview['bytes']} bytes")
            print("\nNo changes made. Run again with --yes to apply.")
        return
    asset = client.add_position_image(matches[0], profile, info["path"])
    print(f"Attached image. Experience ID: {preview['experience_id']}; asset: {asset}")


def command_experience_update(args: argparse.Namespace) -> None:
    client = Client()
    raw = client.own_profile()
    profile = next((item for item in raw.get("included", []) if str(item.get("$type", "")).endswith(".Profile")), None)
    positions = [item for item in raw.get("included", []) if str(item.get("$type", "")).endswith(".Position")]
    matches = [item for item in positions if (
        position_id(item) == args.experience
        or str(item.get("companyName", "")).casefold() == args.experience.casefold()
        or str(item.get("title", "")).casefold() == args.experience.casefold()
    )]
    if len(matches) != 1:
        choices = ", ".join(f"{position_id(item)} ({item.get('title')} at {item.get('companyName')})" for item in positions) or "none"
        raise LinkedInError(f"experience '{args.experience}' was not uniquely found. Available: {choices}")
    if not profile:
        raise LinkedInError("LinkedIn's profile response did not contain the current profile")
    if not args.organization_id:
        raise LinkedInError("no changes were supplied")
    preview = {
        "experience_id": position_id(matches[0]), "title": matches[0].get("title"),
        "company": matches[0].get("companyName"), "organization_id": args.organization_id,
    }
    if not args.yes:
        if args.json:
            emit_json({"preview": True, **preview})
        else:
            print(f"Proposed experience update: {preview['title']} at {preview['company']}")
            print(f"  Organization ID: {preview['organization_id']}")
            print("\nNo changes made. Run again with --yes to apply.")
        return
    client.update_position_organization(matches[0], profile, args.organization_id)
    print(f"Updated. Experience ID: {preview['experience_id']}")


def verification_path():
    return session_path().with_name("pending-verification.json")


def command_verification_workplace_start(args: argparse.Namespace) -> None:
    email = (sys.stdin.read() if args.email_stdin else args.email or "").strip()
    if not email:
        raise LinkedInError("provide --email or --email-stdin")
    if not args.yes:
        print(f"Proposed workplace verification for LinkedIn company ID {args.company_id}.")
        print("A six-digit code will be sent to the supplied work email.\n")
        print("No code sent. Run again with --yes to continue.")
        return
    challenge_id = Client().start_workplace_verification(email, args.company_id)
    path = verification_path()
    path.write_text(json.dumps({"email": email, "company_id": args.company_id, "challenge_id": challenge_id}) + "\n")
    path.chmod(0o600)
    print("Verification code sent. Run `linkedin verification workplace complete CODE --yes` within 15 minutes.")


def command_verification_workplace_complete(args: argparse.Namespace) -> None:
    path = verification_path()
    if not path.exists():
        raise LinkedInError("no pending workplace verification; run the start command first")
    pending = json.loads(path.read_text())
    if not args.yes:
        print(f"Proposed completion for LinkedIn company ID {pending['company_id']}.")
        print("\nNo changes made. Run again with --yes to confirm the code.")
        return
    Client().complete_workplace_verification(
        pending["email"], pending["company_id"], pending["challenge_id"], args.code,
    )
    path.unlink(missing_ok=True)
    print("Workplace verification completed.")


def command_organization_create(args: argparse.Namespace) -> None:
    info = image_file_info(args.logo) if args.logo else None
    preview = {
        "name": args.name, "public_name": args.public_name,
        "industry": args.industry, "industry_id": args.industry_id,
        "size": args.size, "type": args.type, "website": args.website,
        "tagline": args.tagline, "logo": str(info["path"]) if info else None,
    }
    if not args.yes:
        if args.json:
            emit_json({"preview": True, **preview})
        else:
            print(f"Proposed LinkedIn Company Page: {args.name}")
            for key, value in preview.items():
                if key != "name" and value:
                    print(f"  {key.replace('_', ' ').capitalize():<14} {value}")
            print("\nNo Page created. Run again with --yes to apply.")
        return
    result = Client().create_organization(
        name=args.name, universal_name=args.public_name,
        industry_id=args.industry_id, industry_name=args.industry,
        organization_size=args.size, organization_type=args.type,
        tagline=args.tagline or "", website=args.website or "", logo_path=args.logo,
    )
    if args.json:
        emit_json(result)
    else:
        organization_id = (result.get("value") or result.get("id") or result.get("entityUrn")
                           or result.get("data", {}).get("value"))
        print("Created LinkedIn Company Page" + (f": {organization_id}" if organization_id else "."))


def command_api(args: argparse.Namespace) -> None:
    emit_json(Client().get(args.path))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="linkedin", description="Read LinkedIn using a pasted session cookie (no browser automation).")
    sub = p.add_subparsers(dest="command", required=True)
    login = sub.add_parser("login", help="save and validate a LinkedIn li_at token or Cookie header")
    login.add_argument("--cookie-stdin", action="store_true", help="read the secret from standard input")
    login.set_defaults(func=command_login)
    logout = sub.add_parser("logout", help="delete the locally saved session")
    logout.set_defaults(func=command_logout)
    status = sub.add_parser("status", help="validate the session and show the active account")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=command_status)
    me = sub.add_parser("me", help="show the authenticated member's basic profile")
    me.add_argument("--json", action="store_true")
    me.add_argument("--raw", action="store_true", help="return LinkedIn's unmodified response (requires --json for machine use)")
    me.set_defaults(func=command_me)
    invitations = sub.add_parser("invitations", help="list pending incoming invitations")
    invitations.add_argument("--limit", "-n", type=int, default=20)
    invitations.add_argument("--start", type=int, default=0)
    invitations.add_argument("--json", action="store_true")
    invitations.add_argument("--raw", action="store_true", help="return LinkedIn's unmodified response")
    invitations.set_defaults(func=command_invitations)
    profile = sub.add_parser("profile", help="inspect the complete authenticated member profile")
    profile_sub = profile.add_subparsers(dest="profile_command", required=True)
    profile_show = profile_sub.add_parser("show", help="show all fields and sections on a profile")
    profile_show.add_argument("person", nargs="?", help="public profile ID or linkedin.com/in/... URL; defaults to you")
    profile_show.add_argument("--json", action="store_true", help="return a stable normalized document")
    profile_show.add_argument("--raw", action="store_true", help="return LinkedIn's complete unmodified response")
    profile_show.set_defaults(func=command_profile_show)
    connection = sub.add_parser("connection", help="inspect first-degree connections")
    connection_sub = connection.add_subparsers(dest="connection_command", required=True)
    connection_list = connection_sub.add_parser("list", help="list or search your connections")
    connection_list.add_argument("--search", "-q", help="filter by name, headline, or profile ID")
    connection_list.add_argument("--limit", "-n", type=int, default=20)
    connection_list.add_argument("--start", type=int, default=0)
    connection_list.add_argument("--json", action="store_true")
    connection_list.add_argument("--raw", action="store_true", help="return LinkedIn's unmodified response")
    connection_list.set_defaults(func=command_connection_list)
    education = sub.add_parser("education", help="manage education entries on your profile")
    education_sub = education.add_subparsers(dest="education_command", required=True)
    education_update = education_sub.add_parser("update", help="preview or update an existing education entry")
    education_update.add_argument("education", help="education ID or exact school name")
    education_update.add_argument("--start", help="start month in YYYY-MM format")
    education_update.add_argument("--end", help="end month in YYYY-MM format")
    education_update.add_argument("--degree")
    education_update.add_argument("--field")
    education_update.add_argument("--grade")
    education_update.add_argument("--activities")
    education_update.add_argument("--description")
    education_update.add_argument(
        "--organization-id",
        help="associate the entry with a numeric LinkedIn company ID (enables its logo)",
    )
    education_update.add_argument("--json", action="store_true", help="emit a JSON preview")
    education_update.add_argument("--yes", action="store_true", help="apply the proposed update")
    education_update.set_defaults(func=command_education_update)
    background = sub.add_parser("background", help="manage the profile background image")
    background_sub = background.add_subparsers(dest="background_command", required=True)
    background_update = background_sub.add_parser("update", help="preview or upload a profile background image")
    background_update.add_argument("image", help="PNG or JPEG file, up to 8 MiB")
    background_update.add_argument("--json", action="store_true", help="emit a JSON preview")
    background_update.add_argument("--yes", action="store_true", help="upload and apply the image")
    background_update.set_defaults(func=command_background_update)
    project = sub.add_parser("project", help="manage profile projects")
    project_sub = project.add_subparsers(dest="project_command", required=True)
    project_delete = project_sub.add_parser("delete", help="preview or delete a project")
    project_delete.add_argument("project", help="project ID or exact title")
    project_delete.add_argument("--json", action="store_true", help="emit a JSON preview")
    project_delete.add_argument("--yes", action="store_true", help="delete the project")
    project_delete.set_defaults(func=command_project_delete)
    experience = sub.add_parser("experience", help="manage experience entries on your profile")
    experience_sub = experience.add_subparsers(dest="experience_command", required=True)
    experience_media = experience_sub.add_parser("media", help="manage media attached to an experience")
    experience_media_sub = experience_media.add_subparsers(dest="experience_media_command", required=True)
    experience_media_add = experience_media_sub.add_parser("add", help="preview or attach an image to an experience")
    experience_media_add.add_argument("experience", help="experience ID, exact company, or exact title")
    experience_media_add.add_argument("image", help="PNG or JPEG file, up to 8 MiB")
    experience_media_add.add_argument("--json", action="store_true", help="emit a JSON preview")
    experience_media_add.add_argument("--yes", action="store_true", help="upload and attach the image")
    experience_media_add.set_defaults(func=command_experience_media_add)
    experience_update = experience_sub.add_parser("update", help="preview or update an experience")
    experience_update.add_argument("experience", help="experience ID, exact company, or exact title")
    experience_update.add_argument("--organization-id", help="associate a numeric LinkedIn company ID")
    experience_update.add_argument("--json", action="store_true", help="emit a JSON preview")
    experience_update.add_argument("--yes", action="store_true", help="apply the update")
    experience_update.set_defaults(func=command_experience_update)
    verification = sub.add_parser("verification", help="manage LinkedIn account verifications")
    verification_sub = verification.add_subparsers(dest="verification_command", required=True)
    workplace = verification_sub.add_parser("workplace", help="verify a current workplace by work email")
    workplace_sub = workplace.add_subparsers(dest="workplace_command", required=True)
    workplace_start = workplace_sub.add_parser("start", help="send a workplace verification code")
    workplace_start.add_argument("--email")
    workplace_start.add_argument("--email-stdin", action="store_true", help="read the work email from standard input")
    workplace_start.add_argument("--company-id", default="22695", help="numeric LinkedIn company ID")
    workplace_start.add_argument("--yes", action="store_true", help="send the code")
    workplace_start.set_defaults(func=command_verification_workplace_start)
    workplace_complete = workplace_sub.add_parser("complete", help="confirm a workplace verification code")
    workplace_complete.add_argument("code", help="six-digit code received by work email")
    workplace_complete.add_argument("--yes", action="store_true", help="verify and save the workplace")
    workplace_complete.set_defaults(func=command_verification_workplace_complete)
    organization = sub.add_parser("organization", help="manage LinkedIn organization Pages")
    organization_sub = organization.add_subparsers(dest="organization_command", required=True)
    organization_create = organization_sub.add_parser("create", help="preview or create a Company Page")
    organization_create.add_argument("name")
    organization_create.add_argument("--public-name", required=True, help="linkedin.com/company/... suffix")
    organization_create.add_argument("--industry", required=True)
    organization_create.add_argument("--industry-id", required=True)
    organization_create.add_argument("--size", default="SIZE_1")
    organization_create.add_argument("--type", default="PRIVATELY_HELD")
    organization_create.add_argument("--website")
    organization_create.add_argument("--tagline")
    organization_create.add_argument("--logo", help="PNG or JPEG logo, up to 8 MiB")
    organization_create.add_argument("--json", action="store_true", help="emit a JSON preview or result")
    organization_create.add_argument("--yes", action="store_true", help="create the Page")
    organization_create.set_defaults(func=command_organization_create)
    api = sub.add_parser("api", help="advanced read-only GET to a /voyager/api/ endpoint")
    api.add_argument("path", help="absolute /voyager/api/... path")
    api.set_defaults(func=command_api)
    return p


def main() -> None:
    try:
        args = parser().parse_args()
        if hasattr(args, "limit") and not 1 <= args.limit <= 100:
            raise LinkedInError("--limit must be between 1 and 100")
        args.func(args)
    except LinkedInError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
