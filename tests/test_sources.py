"""Checks of the code that reads other sites, on short pages written to their shape, with no network.

A site that changes its pages breaks these readers quietly, so each reader is held to the shape it
was written for. Run from the project folder: python -m unittest discover tests
"""

import pathlib
import sys
import unittest
from datetime import date

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import devjobs  # noqa: E402
import fetch_jobs  # noqa: E402
import filter_jobs  # noqa: E402
import linkedin  # noqa: E402
import run  # noqa: E402
import scout  # noqa: E402

TEXT = "Build backend services in Python and Java, own them in production, and work with the product team. " * 4

JOB_PAGE = f"""
<span class="posted-time-ago__text topcard__flavor--metadata">
  3 days ago
</span>
<div class="show-more-less-html__markup relative overflow-hidden">
  <p>{TEXT}</p><ul><li>2 years of experience</li></ul>
</div>
<ul class="description__job-criteria-list">
  <li><h3 class="description__job-criteria-subheader">Seniority level</h3>
      <span class="description__job-criteria-text description__job-criteria-text--criteria">Entry level</span></li>
  <li><h3 class="description__job-criteria-subheader">Employment type</h3>
      <span class="description__job-criteria-text description__job-criteria-text--criteria">Full-time</span></li>
</ul>
"""

SEARCH_PAGE = """
<li><div class="base-card base-search-card job-search-card">
  <a class="base-card__full-link absolute" href="https://il.linkedin.com/jobs/view/backend-developer-at-example-4413420945?position=1&amp;pageNum=0">
  </a>
  <h3 class="base-search-card__title">Backend Developer &amp; Data</h3>
  <h4 class="base-search-card__subtitle"><a href="x">Example Ltd</a></h4>
  <span class="job-search-card__location">Tel Aviv-Yafo, Tel Aviv District, Israel</span>
  <time class="job-search-card__listdate" datetime="2026-10-31">1 day ago</time>
</div></li>
<li><div class="base-card"><h3 class="base-search-card__title">No link</h3></div></li>
"""

CARD = """
<a class="name-job" target="_blank" href="https://www.devjobs.co.il/job-details/4400000001" wire:click="x">Backend Developer &amp; Data
</a>
<div class="w-100"><span class="location-small">Tel Aviv (Hybrid)</span><span class="card-time">Oct 07, 2026</span></div>
"""


class LinkedInPage(unittest.TestCase):
    def test_number_comes_from_the_end_of_the_link(self):
        self.assertEqual(linkedin.number_of(
            "https://il.linkedin.com/jobs/view/backend-developer-at-example-4473640796?utm_source=x"), "4473640796")
        self.assertEqual(linkedin.number_of("https://example.com/jobs/1"), "")

    def test_relative_dates(self):
        today = date(2026, 10, 9)
        self.assertEqual(linkedin.posted_on("3 days ago", today), "2026-10-06")
        self.assertEqual(linkedin.posted_on("2 weeks ago", today), "2026-09-25")
        self.assertEqual(linkedin.posted_on("5 hours ago", today), "2026-10-09")
        self.assertEqual(linkedin.posted_on("recently", today), "")

    def test_a_page_gives_its_text_criteria_and_date(self):
        found = linkedin.parse(JOB_PAGE, date(2026, 10, 9))
        self.assertEqual(found["result"], "text")
        self.assertEqual(found["posted"], "2026-10-06")
        self.assertIn("2 years of experience", found["text"])
        self.assertIn("Seniority level: Entry level.", found["text"])

    def test_a_closed_job_is_closed(self):
        self.assertEqual(linkedin.parse(JOB_PAGE + "<figcaption>No longer accepting applications</figcaption>")["result"],
                         "closed")

    def test_a_page_without_its_text_is_unparsed(self):
        self.assertEqual(linkedin.parse("<html><body>Sign in to see this job</body></html>")["result"], "unparsed")


class LinkedInSearch(unittest.TestCase):
    def test_a_result_gives_title_company_place_link_and_date(self):
        self.assertEqual(linkedin.parse_cards(SEARCH_PAGE), [{
            "title": "Backend Developer & Data", "company": "Example Ltd",
            "location": "Tel Aviv-Yafo, Tel Aviv District, Israel",
            "url": "https://il.linkedin.com/jobs/view/backend-developer-at-example-4413420945", "posted": "2026-10-31"}])

    def test_a_rule_counts_once_whatever_tripped_it(self):
        self.assertEqual(run.short_reason("too senior (senior)"), "too senior")
        self.assertEqual(run.short_reason("wants 5+ years"), "wants more years")


class DevJobsPage(unittest.TestCase):
    def test_a_card_gives_number_title_place_and_date(self):
        self.assertEqual(devjobs.parse_cards(CARD),
                         (("4400000001", "Backend Developer & Data", "Tel Aviv (Hybrid)", "2026-10-07"),))


class Duplicates(unittest.TestCase):
    def test_a_map_job_that_a_board_gave_is_a_duplicate(self):
        mapped = [{"company": "Autofleet", "title": "Senior Full Stack Developer", "url": "m1"},
                  {"company": "Autofleet", "title": "Data Engineer", "url": "m2"}]
        direct = [{"company": "AutoFleet Ltd", "title": "Senior Full Stack Developer", "url": "d1"}]
        self.assertEqual(scout.duplicates(mapped, direct), {"m1"})

    def test_a_job_listed_by_a_fund_under_the_company_name_is_a_duplicate(self):
        mapped = [{"company": "Example Data", "title": "Platform Engineer", "url": "m1"}]
        direct = [{"company": "Example Fund", "title": "Example- Platform Engineer", "url": "d1"},
                  {"company": "Example Fund", "title": "Other Security- Senior Backend Engineer", "url": "d2"}]
        self.assertEqual(scout.duplicates(mapped, direct), {"m1"})

    def test_a_shared_generic_word_is_not_a_duplicate(self):
        mapped = [{"company": "Example Aero Systems", "title": "Software Engineer", "url": "m1"}]
        direct = [{"company": "Other Ltd", "title": "Software Systems Engineer", "url": "d1"}]
        self.assertEqual(scout.duplicates(mapped, direct), set())


class Places(unittest.TestCase):
    def test_a_place_counts_only_as_a_whole_word(self):
        places = fetch_jobs.REGIONS["israel"]
        self.assertTrue(fetch_jobs.names_place("TLV Office", places))
        self.assertTrue(fetch_jobs.names_place("Petach Tikvah · Petach Tikvah", places))
        self.assertFalse(fetch_jobs.names_place("WTLV WJXX-TV Jacksonville", places))
        self.assertFalse(fetch_jobs.names_place("Kanyakumari, Tamilnadu", places))


class Rules(unittest.TestCase):
    def reason(self, title):
        return filter_jobs.rejection_reason({"title": title, "company": "Example", "description": "", "location": "Israel"},
                                            filter_jobs.MAX_YEARS_REQUIRED)

    def test_algorithm_roles_need_software_in_the_title(self):
        self.assertIsNotNone(self.reason("Algorithm Engineer"))
        self.assertIsNone(self.reason("Algorithm Software Engineer"))
        self.assertIsNone(self.reason("Algorithm's Infrastructure Engineer"))

    def test_level_three_passes_when_the_posting_asks_for_few_years(self):
        def reason(title, description):
            return filter_jobs.rejection_reason({"title": title, "company": "Example", "description": description,
                                                 "location": "Israel"}, filter_jobs.MAX_YEARS_REQUIRED)
        self.assertIsNone(reason("Software Engineer III", "2 years of experience in software development"))
        self.assertIsNotNone(reason("Software Engineer III", ""))
        self.assertIsNotNone(reason("Senior Software Engineer III", "2 years of experience in software development"))

    def test_student_and_intern_roles_are_dropped(self):
        self.assertIsNotNone(self.reason("Software Engineer student position"))
        self.assertIsNotNone(self.reason("Backend Intern"))
        self.assertIsNone(self.reason("Graduate Software Engineer"))
        self.assertIsNone(self.reason("Internal Tools Software Engineer"))

    def test_embedded_roles_pass_only_when_junior(self):
        self.assertIsNotNone(self.reason("Embedded Software Engineer"))
        self.assertIsNone(self.reason("Junior Embedded Software Engineer"))


if __name__ == "__main__":
    unittest.main()
