import os
import stat
import tempfile
import unittest

from src.client import LinkedInError, connection_summaries, education_id, education_update_payload, full_profile_summary, me_summary, normalize_profile_identity, parse_cookie_input, save_session, session_path


class ClientTests(unittest.TestCase):
    def test_bare_token(self):
        self.assertEqual(parse_cookie_input("secret"), {"li_at": "secret"})

    def test_cookie_header(self):
        self.assertEqual(parse_cookie_input("li_at=secret; JSESSIONID=\"ajax:1\""), {
            "li_at": "secret", "JSESSIONID": '"ajax:1"'
        })

    def test_requires_li_at(self):
        with self.assertRaises(LinkedInError):
            parse_cookie_input("JSESSIONID=x; lang=en")

    def test_profile_identity(self):
        self.assertEqual(normalize_profile_identity("grace-hopper"), "grace-hopper")
        self.assertEqual(
            normalize_profile_identity("https://www.linkedin.com/in/grace-hopper/?trk=x"),
            "grace-hopper",
        )
        with self.assertRaises(LinkedInError):
            normalize_profile_identity("Grace Hopper")

    def test_summary(self):
        payload = {"data": {"plainId": 7, "premiumSubscriber": False}, "included": [{
            "$type": "com.linkedin.voyager.identity.shared.MiniProfile",
            "firstName": "Ada", "lastName": "Lovelace", "occupation": "Engineer",
            "publicIdentifier": "ada-lovelace"
        }]}
        result = me_summary(payload)
        self.assertEqual(result["name"], "Ada Lovelace")
        self.assertEqual(result["url"], "https://www.linkedin.com/in/ada-lovelace/")

    def test_session_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            old = os.environ.get("LINKEDIN_CONFIG_DIR")
            os.environ["LINKEDIN_CONFIG_DIR"] = directory
            try:
                save_session({"li_at": "secret"})
                mode = stat.S_IMODE(session_path().stat().st_mode)
                self.assertEqual(mode, 0o600)
            finally:
                if old is None:
                    os.environ.pop("LINKEDIN_CONFIG_DIR", None)
                else:
                    os.environ["LINKEDIN_CONFIG_DIR"] = old

    def test_full_profile_summary(self):
        payload = {"included": [
            {"$type": "com.linkedin.voyager.dash.identity.profile.Profile",
             "entityUrn": "urn:li:fsd_profile:abc", "objectUrn": "urn:li:member:7",
             "publicIdentifier": "ada", "firstName": "Ada", "lastName": "Lovelace",
             "headline": "Engineer", "summary": "About", "premium": False,
             "location": {"countryCode": "GB"}, "geoLocation": {"*geo": "urn:geo:1"},
             "*industry": "urn:industry:1", "supportedLocales": []},
            {"$type": "com.linkedin.voyager.dash.common.Geo", "entityUrn": "urn:geo:1",
             "defaultLocalizedName": "London, United Kingdom"},
            {"$type": "com.linkedin.voyager.dash.common.Industry", "entityUrn": "urn:industry:1",
             "name": "Software"},
            {"$type": "com.linkedin.voyager.dash.identity.profile.Education",
             "entityUrn": "urn:education:1", "schoolName": "University", "fieldOfStudy": "Math",
             "dateRange": {"start": {"year": 1830}, "end": {"year": 1835, "month": 6}}},
        ]}
        result = full_profile_summary(payload)
        self.assertEqual(result["name"], "Ada Lovelace")
        self.assertEqual(result["location"], "London, United Kingdom")
        self.assertEqual(result["industry"], "Software")
        self.assertEqual(result["sections"]["education"][0]["start"], "1830")
        self.assertEqual(result["sections"]["education"][0]["end"], "1835-06")

    def test_connection_summaries(self):
        urn = "urn:li:fsd_profile:abc"
        payload = {"included": [
            {"$type": "com.linkedin.voyager.dash.identity.profile.Profile",
             "entityUrn": urn, "publicIdentifier": "grace-hopper",
             "firstName": "Grace", "lastName": "Hopper", "headline": "Computer Scientist"},
            {"$type": "com.linkedin.voyager.dash.relationships.Connection",
             "entityUrn": "urn:li:fsd_connection:abc", "connectedMember": urn,
             "*connectedMemberResolutionResult": urn, "createdAt": 1783553216000},
        ]}
        rows = connection_summaries(payload)
        self.assertEqual(rows[0]["id"], "grace-hopper")
        self.assertEqual(rows[0]["name"], "Grace Hopper")
        self.assertEqual(rows[0]["connected_at"], "2026-07-08T23:26:56Z")

    def test_education_update_payload(self):
        entity = {"entityUrn": "urn:li:fsd_profileEducation:(abc,123)",
                  "schoolName": "University", "schoolUrn": "urn:school:1",
                  "fieldOfStudy": "Math", "degreeName": None, "dateRange": None}
        self.assertEqual(education_id(entity), "123")
        payload = education_update_payload(entity, start="2025-09", end="2029-06", degree="BSc")
        self.assertEqual(payload["degreeName"], "BSc")
        self.assertEqual(payload["fieldOfStudy"], "Math")
        self.assertEqual(payload["dateRange"]["start"], {"year": 2025, "month": 9})
        with self.assertRaises(LinkedInError):
            education_update_payload(entity, start="2029-09", end="2025-06")


if __name__ == "__main__":
    unittest.main()
