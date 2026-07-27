from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from typing import Any

from .client import (
    Client, LinkedInError, connection_summaries, education_id, education_update_payload,
    full_profile_summary, internal_profile_id, me_summary, mini_profiles,
    parse_cookie_input, save_session, session_path,
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
        description=args.description,
    )
    preview = {
        "education_id": education_id(entity),
        "school": entity.get("schoolName"),
        "changes": {key: value for key, value in {
            "start": args.start, "end": args.end, "degree": args.degree,
            "field": args.field, "grade": args.grade,
            "activities": args.activities, "description": args.description,
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
    education_update.add_argument("--json", action="store_true", help="emit a JSON preview")
    education_update.add_argument("--yes", action="store_true", help="apply the proposed update")
    education_update.set_defaults(func=command_education_update)
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
