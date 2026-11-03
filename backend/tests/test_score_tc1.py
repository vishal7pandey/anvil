import json
from pathlib import Path
from unittest.mock import patch

from anvil.cases import load_case

from scripts.score_tc1 import (
    TARGET_FILE,
    fetch_upstream_diff,
    main,
    parse_unified_diff,
    score_case_diff,
    score_tc1_diff,
)

# Synthetic fixture diff with the 3-hunk structure on cpukit/posix/src/mutexinit.c
# (uses placeholder text so no withheld C source lines live in the test suite).
UPSTREAM_FIXTURE_DIFF = """\
diff --git a/cpukit/posix/src/mutexinit.c b/cpukit/posix/src/mutexinit.c
index 875bcddf447..80056251899 100644
--- a/cpukit/posix/src/mutexinit.c
+++ b/cpukit/posix/src/mutexinit.c
@@ -171,18 +171,6 @@ int pthread_mutex_init(
   ctx_171();
   ctx_172();
   ctx_173();
-  early_init_174();
-  early_init_175();
-  early_init_176();
-  early_init_177();
-  early_init_178();
-  early_init_179();
-  early_init_180();
-  early_init_181();
-  early_init_182();
-  early_init_183();
-  early_init_184();
-  early_init_185();
   ctx_186();
   ctx_187();
   ctx_188();
@@ -203,6 +191,16 @@ int pthread_mutex_init(
   ctx_203();
   ctx_204();
   ctx_205();
+  late_init_1();
+  late_init_2();
+  late_init_3();
+  late_init_4();
+  late_init_5();
+  late_init_6();
+  late_init_7();
+  late_init_8();
+  late_init_9();
+  late_init_10();
   ctx_206();
   ctx_207();
   ctx_208();
@@ -210,5 +208,6 @@ int pthread_mutex_init(
   ctx_210();
   ctx_211();
   ctx_212();
   ctx_213();
+  commit_flags_at_end();
   return 0;
 }
diff --git a/testsuites/psxtests/psx05/init.c b/testsuites/psxtests/psx05/init.c
index fabab1a86b7..b41ee020806 100644
--- a/testsuites/psxtests/psx05/init.c
+++ b/testsuites/psxtests/psx05/init.c
@@ -345,3 +345,7 @@ static void test_mutex_not_initialized( void )
   ctx_345();
+  regression_test_1();
+  regression_test_2();
+  regression_test_3();
+  regression_test_4();
 }
"""


# Agent diff that combines hunks 2 and 3 into a single hunk and shifts insertion by +2 lines
# (within the ±3 line tolerance).
AGENT_MATCHING_DIFF = """\
diff --git a/cpukit/posix/src/mutexinit.c b/cpukit/posix/src/mutexinit.c
index 875bcddf447..11111111111 100644
--- a/cpukit/posix/src/mutexinit.c
+++ b/cpukit/posix/src/mutexinit.c
@@ -172,10 +172,4 @@ int pthread_mutex_init(
   ctx_172();
   ctx_173();
-  early_init_174();
-  early_init_175();
-  early_init_176();
-  early_init_177();
-  early_init_178();
-  early_init_179();
   ctx_180();
@@ -205,9 +199,15 @@ int pthread_mutex_init(
   ctx_205();
   ctx_206();
+  agent_late_init_a();
+  agent_late_init_b();
   ctx_207();
   ctx_208();
   ctx_209();
   ctx_210();
   ctx_211();
+  agent_commit_flags();
   ctx_212();
   ctx_213();
diff --git a/testsuites/psxtests/psx05/init.c b/testsuites/psxtests/psx05/init.c
index fabab1a86b7..22222222222 100644
--- a/testsuites/psxtests/psx05/init.c
+++ b/testsuites/psxtests/psx05/init.c
@@ -345,2 +345,4 @@ static void test_mutex_not_initialized( void )
   ctx_345();
+  agent_regression_test();
+  agent_regression_assert();
"""


# Agent diff that only edits the first hunk and misses hunks 2 and 3.
AGENT_PARTIAL_DIFF = """\
diff --git a/cpukit/posix/src/mutexinit.c b/cpukit/posix/src/mutexinit.c
index 875bcddf447..33333333333 100644
--- a/cpukit/posix/src/mutexinit.c
+++ b/cpukit/posix/src/mutexinit.c
@@ -171,6 +171,5 @@ int pthread_mutex_init(
   ctx_171();
   ctx_172();
   ctx_173();
-  early_init_174();
   ctx_175();
"""


def test_parse_unified_diff_extracts_base_line_numbers():
    parsed = parse_unified_diff(UPSTREAM_FIXTURE_DIFF)
    assert sorted(parsed.keys()) == [
        "cpukit/posix/src/mutexinit.c",
        "testsuites/psxtests/psx05/init.c",
    ]
    hunks = parsed[TARGET_FILE]
    assert len(hunks) == 3
    assert hunks[0]["changed_lines"] == list(range(174, 186))
    assert hunks[1]["changed_lines"] == [206]
    assert hunks[2]["changed_lines"] == [214]


def test_parse_unified_diff_counts_leading_dash_or_plus_c_lines():
    # Regression for PR #43 review: a removed C line starting with `--` (e.g. `--count;`) must
    # still be recorded as a changed base line, not skipped as a `---` file header.
    diff = """\
diff --git a/cpukit/posix/src/mutexinit.c b/cpukit/posix/src/mutexinit.c
index aaa..bbb 100644
--- a/cpukit/posix/src/mutexinit.c
+++ b/cpukit/posix/src/mutexinit.c
@@ -50,4 +50,4 @@ int pthread_mutex_init(
   ctx_50();
---count;
+  x++;
   ctx_52();
"""
    hunks = parse_unified_diff(diff)["cpukit/posix/src/mutexinit.c"]
    assert len(hunks) == 1
    assert hunks[0]["changed_lines"] == [51, 52]


def test_score_tc1_diff_matches_all_three_hunks_within_tolerance():
    result = score_tc1_diff(AGENT_MATCHING_DIFF, UPSTREAM_FIXTURE_DIFF, tolerance=3)
    assert result["target_file"] == TARGET_FILE
    assert result["hunks_total"] == 3
    assert result["hunks_matched"] == 3
    assert result["all_hunks_matched"] is True
    assert result["hunk_overlap_ratio"] == 1.0
    assert [h["matched"] for h in result["hunks"]] == [True, True, True]
    assert result["upstream_files_hit"] == [
        "cpukit/posix/src/mutexinit.c",
        "testsuites/psxtests/psx05/init.c",
    ]
    assert "3/3 upstream hunks matched (±3 lines)" in result["summary"]


def test_score_tc1_diff_reports_unmatched_hunks():
    result = score_tc1_diff(AGENT_PARTIAL_DIFF, UPSTREAM_FIXTURE_DIFF, tolerance=3)
    assert result["hunks_total"] == 3
    assert result["hunks_matched"] == 1
    assert result["all_hunks_matched"] is False
    assert [h["matched"] for h in result["hunks"]] == [True, False, False]
    assert "1/3 upstream hunks matched" in result["summary"]


def test_cli_outputs_json_and_summary_line(tmp_path: Path, capsys):
    agent_file = tmp_path / "agent.diff"
    out_file = tmp_path / "score_hunks.json"
    agent_file.write_text(AGENT_MATCHING_DIFF, encoding="utf-8")

    with patch("scripts.score_tc1.fetch_upstream_diff", return_value=UPSTREAM_FIXTURE_DIFF) as mock_fetch:
        rc = main([str(agent_file), "--out", str(out_file)])

    assert rc == 0
    mock_fetch.assert_called_once_with()
    saved = json.loads(out_file.read_text(encoding="utf-8"))
    assert saved["hunks_matched"] == 3
    assert saved["all_hunks_matched"] is True

    stdout = capsys.readouterr().out.strip().splitlines()
    assert stdout[-1].startswith("TC1 hunk overlap on cpukit/posix/src/mutexinit.c: 3/3")


CASE_SPEC = """\
id = "demo-case"
repo = "owner/demo"
as_of = "2026-01-01"
[upstream]
issues = ["1"]
mrs = ["2"]
fix_commits = ["deadbee1", "deadbee2"]
[base]
branch = "case/demo-base"
[ticket]
project = "BUG"
summary = "Crash on startup"
body = "It crashes."
[scoring]
target_files = ["src/core.c", "src/extra.c"]
test_files = ["tests/t.c"]
tolerance = 5
"""

DEMO_UPSTREAM_DIFF = """\
diff --git a/src/core.c b/src/core.c
index 111..222 100644
--- a/src/core.c
+++ b/src/core.c
@@ -10,3 +10,2 @@ int f(
   ctx_10();
-  bad_call();
   ctx_12();
diff --git a/src/extra.c b/src/extra.c
index 333..444 100644
--- a/src/extra.c
+++ b/src/extra.c
@@ -20,3 +20,2 @@ int g(
   ctx_20();
-  other_bad();
   ctx_22();
"""

DEMO_AGENT_DIFF = """\
diff --git a/src/core.c b/src/core.c
index 111..aaa 100644
--- a/src/core.c
+++ b/src/core.c
@@ -9,4 +9,3 @@ int f(
   ctx_9();
   ctx_10();
-  bad_call();
   ctx_12();
diff --git a/tests/t.c b/tests/t.c
index 555..666 100644
--- a/tests/t.c
+++ b/tests/t.c
@@ -1,2 +1,3 @@ void t(
  x();
+  new_assert();
"""


def test_score_case_diff_uses_case_fields_not_constants(tmp_path):
    spec = tmp_path / "demo-case.toml"
    spec.write_text(CASE_SPEC, encoding="utf-8")
    case = load_case(spec)

    seen: dict = {}

    def fake_fetch(repo, commits):
        seen["repo"], seen["commits"] = repo, commits
        return DEMO_UPSTREAM_DIFF

    with patch("scripts.score_tc1.fetch_upstream_diff", side_effect=fake_fetch):
        result = score_case_diff(DEMO_AGENT_DIFF, case)

    assert seen == {"repo": "owner/demo", "commits": ("deadbee1", "deadbee2")}
    assert result["case"] == "demo-case"
    assert result["fix_commits"] == ["deadbee1", "deadbee2"]
    assert result["hunks_total"] == 2 and result["hunks_matched"] == 1
    assert result["hunk_overlap_ratio"] == 0.5
    assert sorted(result["per_target_file"]) == ["src/core.c", "src/extra.c"]
    core = result["per_target_file"]["src/core.c"]
    assert core["upstream_commits"] == ["deadbee1", "deadbee2"]
    assert core["upstream_files_hit"] == ["src/core.c", "tests/t.c"]
    assert core["extra_files"] == []
    assert core["tolerance_lines"] == 5
    assert "a62e60b5" not in json.dumps(result)
