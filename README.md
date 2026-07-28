# linkedin-cli

An unofficial LinkedIn CLI for Exocortex. It uses LinkedIn's authenticated HTTPS
endpoints directly with a user-supplied `li_at` session token or Cookie header.
It does not launch or control a browser.

Current capabilities include reading the authenticated account, complete profile
sections, first-degree connections, and pending invitations. Education updates
are supported experimentally, preview by default, and require explicit
confirmation.

LinkedIn does not publish or support these endpoints for general client use.
They can change without notice, and automated access may be restricted by
LinkedIn's terms and anti-abuse systems. Use only with your own account and at
human-scale request rates.

## Install

Clone the tool as its own repository under Exocortex's `external-tools/`
directory, then create its virtual environment:

```bash
git clone https://github.com/Yeyito777/linkedin-cli.git \
  ~/Workspace/Exocortex/external-tools/linkedin
cd ~/Workspace/Exocortex/external-tools/linkedin
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
linkedin login
```

Exocortex discovers `manifest.json` automatically. Outside Exocortex, invoke the
tool through `bin/linkedin` or add `bin/` to `PATH`.

## Login

Run `linkedin login` and paste either the value of the `li_at` cookie or a full
Cookie request header. Input is hidden. The session is written to
`config/session.json` with file mode `0600` and is ignored by git.

For scripts, avoid command-line arguments and use standard input:

```bash
printf '%s' "$LINKEDIN_COOKIE" | linkedin login --cookie-stdin
```

## Commands

```text
linkedin login [--cookie-stdin]
linkedin logout
linkedin status [--json]
linkedin me [--json] [--raw]
linkedin profile show [PERSON] [--json] [--raw]
linkedin connection list [-q QUERY] [-n LIMIT] [--json] [--raw]
linkedin education update ID [fields] [--organization-id COMPANY_ID] [--yes]
linkedin background update IMAGE [--yes]
linkedin experience media add ID_OR_EXACT_COMPANY IMAGE [--yes]
linkedin project delete ID_OR_EXACT_TITLE [--yes]
linkedin invitations [-n LIMIT] [--start N] [--json] [--raw]
linkedin api /voyager/api/...       # advanced, GET-only escape hatch
```

The `api` command is intentionally constrained to HTTPS GET requests under
`/voyager/api/`; this first version cannot mutate LinkedIn state.

`profile show` reads LinkedIn's decorated full-profile resource for you or a
public profile ID/URL. Its normalized
JSON includes top-card fields, photo URLs, location, industry, and every profile
section returned by LinkedIn. Use `--raw` when exact protocol fidelity is needed.

`connection list` returns recently added first-degree connections. `--search`
performs a case-insensitive filter over names, headlines, and public profile IDs.

Education changes are previews unless `--yes` is supplied:

```bash
linkedin education update 123456 --start 2025-09 --end 2029-06 \
  --degree "Bachelor of Science" --field "Mathematics and Physics"
```

Use `--organization-id` when an education entry needs to be associated with an
official LinkedIn organization page that the school autocomplete does not
surface. The numeric company ID can be found in that page's LinkedIn URN. This
association allows LinkedIn to display the organization's logo.

Background-image updates and project deletions also preview by default:

```bash
linkedin background update ~/Pictures/linkedin-banner.png
linkedin background update ~/Pictures/linkedin-banner.png --yes

linkedin project delete "Old project title"
linkedin project delete "Old project title" --yes
```

Background updates use LinkedIn's authenticated media-registration flow and
signed binary upload URLs directly. PNG and JPEG images up to 8 MiB are
accepted; the CLI does not open or control a browser.

Experience media attachments use the same direct authenticated approach and
preview by default. They add an image to the experience's media gallery; only
association with an official LinkedIn organization page can change the small
organization logo shown beside an experience.

## Development

```bash
python3 -m unittest discover -s tests
```

Runtime credentials and state live under `config/` and are excluded from git;
only `config/.gitkeep` is tracked.

## License

MIT. See [LICENSE](LICENSE).
