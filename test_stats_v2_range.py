"""stats-v2 (2026-09-30): the dashboard range control (7d/30d/90d/all) and
the two root-cause number bugs it shipped alongside.

Covers:
  - /stats/data's `range` param: correct window boundaries for all four
    values, and per-range caching that returns DISTINCT bodies (not one
    shared cache key silently serving the wrong window).
  - The owner-exclusion rule (PHOSPHENE_TEST_RIG -> properties.test_rig)
    is actually present on every fleet HogQL query — SYS-41 documented it
    ("a fleet query excludes these rows with WHERE NOT test_rig") while
    not one of the shipped queries did it.
  - error_groups: grouping render_failed by the closed error_class
    taxonomy (count, distinct installs, first/last seen, top message),
    on both the local-mirror path and the fleet HogQL shape.
  - The local-mirror path buckets days in UTC (time.gmtime), matching the
    fleet's HogQL toDate(timestamp) and the GitHub-stats archive's UTC
    `date` field — it used to bucket by the machine's own local midnight.
  - _pct_delta's "no meaningful baseline" cases return None, not 0 or a
    divide-by-zero.
"""
from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from unittest import mock

import mlx_ltx_panel as p


def _rec(ev, ts, **props):
    return {"event": ev, "ts": ts, "props": props}


def _day_row(date: str, **kw) -> dict:
    row = {"date": date, "fetched_at": f"{date}T12:00:00Z",
           "stars": 1, "forks": 1, "open_issues": 0, "open_prs": 0}
    row.update(kw)
    return row


class StatsDataRangeParam(unittest.TestCase):
    """/stats/data?range=... — server-side window + per-range cache."""

    def setUp(self):
        # Write a 120-day synthetic archive straight into the sandboxed
        # STATE_DIR (conftest.py points LTX_STATE_DIR at a per-run temp
        # tree before mlx_ltx_panel is even imported, so this never
        # touches a real install's archive).
        now = time.time()
        rows = []
        for i in range(120, -1, -1):
            d = time.strftime("%Y-%m-%d", time.gmtime(now - i * 86400))
            rows.append(json.dumps(_day_row(d)))
        p.STATS_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        p.STATS_DATA_FILE.write_text("\n".join(rows) + "\n", encoding="utf-8")
        p._STATS_DATA_RANGE_CACHE.clear()
        self.addCleanup(p._STATS_DATA_RANGE_CACHE.clear)

    def _dates(self, range_key: str) -> list[str]:
        body = p._stats_data_for_range(range_key)
        return [json.loads(line)["date"] for line in body.splitlines() if line.strip()]

    def test_all_returns_the_whole_archive(self):
        dates = self._dates("all")
        self.assertEqual(len(dates), 121)

    def test_30d_returns_roughly_two_windows_not_the_whole_archive(self):
        # 30d asks for 2x the window (60ish days) so the client can compute
        # a "vs prior period" delta from one response — see
        # _stats_data_for_range's docstring. It must be narrower than "all".
        dates = self._dates("30d")
        self.assertLess(len(dates), 121)
        self.assertGreaterEqual(len(dates), 30)
        today = time.strftime("%Y-%m-%d", time.gmtime())
        self.assertIn(today, dates)

    def test_7d_is_narrower_than_30d_is_narrower_than_90d(self):
        n7 = len(self._dates("7d"))
        n30 = len(self._dates("30d"))
        n90 = len(self._dates("90d"))
        self.assertLess(n7, n30)
        self.assertLess(n30, n90)

    def test_unknown_range_falls_back_to_the_default(self):
        default = p._stats_data_for_range(p.STATS_RANGE_DEFAULT)
        garbage = p._stats_data_for_range("not-a-real-range")
        self.assertEqual(default, garbage)

    def test_each_range_is_cached_separately_not_under_one_shared_key(self):
        b7 = p._stats_data_for_range("7d")
        b30 = p._stats_data_for_range("30d")
        ball = p._stats_data_for_range("all")
        self.assertNotEqual(b7, b30)
        self.assertNotEqual(b30, ball)
        self.assertEqual(set(p._STATS_DATA_RANGE_CACHE.keys()), {"7d", "30d", "all"})

    def test_refresh_busts_every_ranges_cache_via_mtime(self):
        # /stats/refresh rewrites the archive; that bumps its mtime, which
        # must invalidate every cached range on the next read (no separate
        # "clear the usage cache" call needed — see the function docstring).
        first = p._stats_data_for_range("all")
        cached_mtime = p._STATS_DATA_RANGE_CACHE["all"][0]
        # Simulate a slightly-later refresh appending a new day.
        time.sleep(0.02)
        with p.STATS_DATA_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(_day_row("2099-01-01")) + "\n")
        second = p._stats_data_for_range("all")
        self.assertNotEqual(first, second)
        self.assertNotEqual(p._STATS_DATA_RANGE_CACHE["all"][0], cached_mtime)

    def test_route_defaults_to_30d_when_range_is_omitted(self):
        import panel.routes_stats as rs
        rs.P = p

        class FakeParsed:
            query = ""

        class FakeHandler:
            def __init__(self):
                self.sent = []

            def send_response(self, code):
                self.sent.append(("status", code))

            def send_header(self, *a):
                pass

            def end_headers(self):
                pass

            wfile = mock.Mock()

        h = FakeHandler()
        rs.stats_data(h, FakeParsed())
        expected = p._stats_data_for_range("30d")
        h.wfile.write.assert_called_once_with(expected)


class OwnerExclusionAppliedEverywhere(unittest.TestCase):
    """SYS-41's documented rule ('a fleet query excludes these rows with
    WHERE NOT test_rig') — verify it's on EVERY query, not just some."""

    def test_every_fleet_query_carries_the_exclusion(self):
        for range_key in ("7d", "30d", "90d", "all"):
            queries = p._fleet_queries(range_key)
            for name, hogql in queries.items():
                self.assertIn(
                    "test_rig", hogql,
                    f"query {name!r} (range={range_key}) has no owner-exclusion clause",
                )
                self.assertIn("ifNull(properties['test_rig'], 'false') != 'true'", hogql)

    def test_where_helpers_always_append_the_exclusion(self):
        self.assertIn(p._TEST_RIG_EXCLUDE, p._fleet_where_raw("event = 'app_boot'"))
        self.assertIn(p._TEST_RIG_EXCLUDE, p._fleet_where("event = 'app_boot'", "30d"))
        self.assertIn(p._TEST_RIG_EXCLUDE, p._fleet_where("event = 'app_boot'", "all"))


class RangeAwareWindows(unittest.TestCase):
    def test_fleet_window_sql_all_has_no_clause(self):
        self.assertEqual(p._fleet_window_sql("all"), "")
        self.assertEqual(p._fleet_window_sql("all", prior=True), "")

    def test_fleet_window_sql_scales_with_range(self):
        self.assertIn("INTERVAL 7 DAY", p._fleet_window_sql("7d"))
        self.assertIn("INTERVAL 30 DAY", p._fleet_window_sql("30d"))
        self.assertIn("INTERVAL 90 DAY", p._fleet_window_sql("90d"))

    def test_prior_window_is_the_equal_length_period_before_the_current_one(self):
        w = p._fleet_window_sql("30d", prior=True)
        self.assertIn("INTERVAL 60 DAY", w)
        self.assertIn("INTERVAL 30 DAY", w)


class PctDelta(unittest.TestCase):
    def test_none_when_no_baseline(self):
        self.assertIsNone(p._pct_delta(10, None))
        self.assertIsNone(p._pct_delta(None, 10))
        self.assertIsNone(p._pct_delta(10, 0))  # no divide-by-zero

    def test_computes_signed_percent(self):
        self.assertEqual(p._pct_delta(120, 100), 20.0)
        self.assertEqual(p._pct_delta(80, 100), -20.0)


class LocalReportUsesUtcDayBoundaries(unittest.TestCase):
    """The local-mirror path (every install without a PostHog query key —
    i.e. almost every user but the owner) used to bucket by the MACHINE's
    own local midnight (time.localtime), while the fleet path buckets in
    UTC (HogQL toDate) and the GitHub-stats archive stamps UTC dates. Same
    calendar day, two different bucketings depending on which source
    happened to answer — this pins the fix (time.gmtime)."""

    def test_boots_by_day_bucket_matches_gmtime_not_localtime(self):
        # A timestamp deliberately near a local-midnight boundary in most
        # timezones; comparing gmtime- vs localtime- bucketing catches the
        # regression on any machine, not just one running UTC+ or UTC-.
        now = time.time()
        ts = now - 2 * 86400
        recs = [_rec("app_boot", ts, version="4.9.0")]
        with mock.patch.object(p, "_usage_log_read", lambda: recs):
            u = p._usage_local_report("30d")
        expected_day = time.strftime("%Y-%m-%d", time.gmtime(ts))
        got_days = [row["date"] for row in u["boots_by_day"]]
        self.assertEqual(got_days, [expected_day])

    def test_first_boot_growth_date_is_utc(self):
        now = time.time()
        recs = [_rec("app_boot", now, version="4.9.0")]
        with mock.patch.object(p, "_usage_log_read", lambda: recs):
            u = p._usage_local_report("30d")
        expected = time.strftime("%Y-%m-%d", time.gmtime(now))
        self.assertEqual(u["growth"]["installs_by_day"][0]["date"], expected)


class LocalErrorGroups(unittest.TestCase):
    def test_groups_by_class_counts_installs_and_picks_top_message(self):
        now = time.time()
        recs = [
            _rec("render_failed", now - 1000, error_class="oom_jetsam",
                 error_signature="SIGKILL: out of memory"),
            _rec("render_failed", now - 500, error_class="oom_jetsam",
                 error_signature="SIGKILL: out of memory"),
            _rec("render_failed", now - 100, error_class="oom_jetsam",
                 error_signature="SIGKILL: different message"),
            _rec("render_failed", now - 10, error_class="venv_broken",
                 error_signature="python not found"),
        ]
        with mock.patch.object(p, "_usage_log_read", lambda: recs):
            u = p._usage_local_report("30d")
        groups = {g["error_class"]: g for g in u["error_groups"]}
        self.assertEqual(groups["oom_jetsam"]["count"], 3)
        self.assertEqual(groups["oom_jetsam"]["top_message"], "SIGKILL: out of memory")
        self.assertEqual(groups["oom_jetsam"]["signatures"], 2)
        self.assertEqual(groups["venv_broken"]["count"], 1)
        # local mirror is one machine — "installs" is always 1 per class
        self.assertEqual(groups["oom_jetsam"]["installs"], 1)

    def test_missing_error_class_falls_back_to_other(self):
        now = time.time()
        recs = [_rec("render_failed", now, error_signature="mystery")]
        with mock.patch.object(p, "_usage_log_read", lambda: recs):
            u = p._usage_local_report("30d")
        self.assertEqual(u["error_groups"][0]["error_class"], "other")

    def test_range_scopes_which_failures_are_grouped(self):
        now = time.time()
        recs = [
            _rec("render_failed", now - 100 * 86400, error_class="timeout",
                 error_signature="old failure outside range"),
            _rec("render_failed", now - 1 * 86400, error_class="disk_full",
                 error_signature="recent failure"),
        ]
        with mock.patch.object(p, "_usage_log_read", lambda: recs):
            u7 = p._usage_local_report("7d")
            u_all = p._usage_local_report("all")
        classes_7d = {g["error_class"] for g in u7["error_groups"]}
        classes_all = {g["error_class"] for g in u_all["error_groups"]}
        self.assertEqual(classes_7d, {"disk_full"})
        self.assertEqual(classes_all, {"timeout", "disk_full"})


class FleetDeltasHaveNoPriorPeriodForAllTime(unittest.TestCase):
    """Found live while smoke-testing stats-v2 on :8266: for range='all',
    _fleet_window_sql(prior=True) is also the empty clause (there's no
    "period before all time" to bound it by), so the 'current' and 'prior'
    HogQL queries are IDENTICAL and _pct_delta(x, x) computed a real 0.0 —
    the dashboard showed "±0% vs prior" on the one range where there is no
    prior period at all. Fixed by gating range_key == 'all' explicitly
    rather than trusting the arithmetic."""

    def test_all_time_range_has_no_deltas(self):
        # Every current/prior query pair returns the SAME row on purpose —
        # reproduces exactly what "all" actually does server-side, so this
        # fails again if the range_key=='all' gate is ever removed.
        same_pair = [["render_completed", 40], ["render_failed", 10]]
        with mock.patch.object(p, "_analytics_query_key", return_value="phx_fake"), \
             mock.patch.object(p, "_usage_fleet_query_one",
                               side_effect=lambda hogql, key: (
                                   same_pair if "GROUP BY event" in hogql else same_scalar)):
            report = p._usage_fleet_report("all")
        self.assertIsNotNone(report)
        self.assertEqual(report["deltas"], {})

    def test_narrower_ranges_still_compute_deltas(self):
        same_pair = [["render_completed", 40], ["render_failed", 10]]
        with mock.patch.object(p, "_analytics_query_key", return_value="phx_fake"), \
             mock.patch.object(p, "_usage_fleet_query_one",
                               side_effect=lambda hogql, key: (
                                   same_pair if "GROUP BY event" in hogql else [[100]])):
            report = p._usage_fleet_report("30d")
        self.assertIn("renders_in_range_pct", report["deltas"])


class RateTilesFollowTheRange(unittest.TestCase):
    """Owner: numbers must be consistent with the picker. h3_share_pct and
    error_rate_pct used a hardcoded 14-day window on the local path while
    every other tile followed the range control."""

    def test_h3_share_and_error_rate_use_the_selected_window(self):
        now = time.time()
        recs = [
            # 20 days ago: outside 7d and outside the old fixed 14d window
            _rec("render_failed", now - 20 * 86400, engine="h3", error_class="timeout"),
            _rec("render_completed", now - 20 * 86400, engine="h3"),
            # yesterday: inside every window
            _rec("render_completed", now - 1 * 86400, engine="ltx"),
        ]
        with mock.patch.object(p, "_usage_log_read", lambda: recs):
            u7 = p._usage_local_report("7d")
            u30 = p._usage_local_report("30d")
        self.assertEqual(u7["tiles"]["h3_share_pct"], 0.0)
        self.assertEqual(u7["tiles"]["error_rate_pct"], 0.0)
        self.assertAlmostEqual(u30["tiles"]["h3_share_pct"], 66.7, places=1)
        self.assertAlmostEqual(u30["tiles"]["error_rate_pct"], 33.3, places=1)

    def test_fleet_rate_queries_are_range_scoped(self):
        q = p._fleet_queries("90d")
        self.assertIn("INTERVAL 90 DAY", q["engines"])
        self.assertIn("INTERVAL 90 DAY", q["outcomes"])


class LocalReportFeedsTheActiveTile(unittest.TestCase):
    """4.17.3 review: the stats-v2 "Active this range" tile reads
    tiles.active_in_range; the local fallback only sent the old
    weekly_active_installs, so the tile showed a dash."""

    def test_local_report_carries_active_in_range(self):
        now = time.time()
        recs = [_rec("app_boot", now - 3600, version="4.17.3")]
        with mock.patch.object(p, "_usage_log_read", lambda: recs):
            u = p._usage_local_report("7d")
        self.assertEqual(u["tiles"]["active_in_range"], 1)
        html = (Path(p.ROOT) / "panel_assets" / "stats.html").read_text(encoding="utf-8")
        self.assertIn("t.active_in_range", html)


class StatsLoadersDropSupersededResponses(unittest.TestCase):
    """4.17.3 review: picking 7d while the 30d request was still in flight
    let the older answer land last and paint 30-day totals under 7-day
    labels. Each loader now stamps its request and drops a superseded one."""

    def test_both_loaders_check_their_generation(self):
        html = (Path(p.ROOT) / "panel_assets" / "stats.html").read_text(encoding="utf-8")
        usage = html[html.index("async function loadUsage("):]
        usage = usage[:usage.index("renderUsage(u || {});")]
        self.assertIn("const gen = ++_usageGen;", usage)
        self.assertIn("if (gen !== _usageGen) return;", usage)
        main = html[html.index("async function main() {"):]
        main = main[:main.index("renderMeta(allRows);")]
        self.assertIn("const gen = ++_rowsGen;", main)
        self.assertIn("if (gen !== _rowsGen) return;", main)


class FleetErrorGroupsQueryShape(unittest.TestCase):
    """Can't hit PostHog in a unit test — asserts the HogQL shape carries
    the fields the dashboard's error-group cards need, and the exclusion."""

    def test_error_groups_query_selects_the_expected_columns(self):
        q = p._fleet_queries("30d")["error_groups"]
        for col in ("class", "total", "installs", "first_seen", "last_seen", "top_message"):
            self.assertIn(col, q)
        self.assertIn("render_failed", q)
        self.assertIn(p._TEST_RIG_EXCLUDE, q)
        self.assertIn("GROUP BY class", q)


if __name__ == "__main__":
    unittest.main()
