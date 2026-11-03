#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: sbom.sh <repo-dir> <out-dir> [--before <dir>]" >&2
  exit 2
}

if (($# < 2 || $# > 4)); then
  usage
fi

repo_dir=$(realpath "$1")
out_dir=$2
before_dir=
if (($# == 4)); then
  [[ $3 == --before ]] || usage
  before_dir=$(realpath "$4")
elif (($# != 2)); then
  usage
fi

[[ -d $repo_dir ]] || { echo "Repository directory does not exist: $repo_dir" >&2; exit 2; }
if [[ -n $before_dir && ! -d $before_dir ]]; then
  echo "Baseline directory does not exist: $before_dir" >&2
  exit 2
fi

out_dir=$(realpath -m "$out_dir")
for source_dir in "$repo_dir" ${before_dir:+"$before_dir"}; do
  if [[ $source_dir == / && $out_dir == /* ]]; then
    echo "Output directory must be outside scanned source directories: $out_dir" >&2
    exit 2
  fi
  case "$out_dir/" in
    "$source_dir/"*) echo "Output directory must be outside scanned source directories: $out_dir" >&2; exit 2 ;;
  esac
done
mkdir -p "$out_dir"

scan_sbom() {
  local source_dir=$1
  local prefix=$2
  local source_name
  source_name=$(basename "$source_dir")

  syft scan "dir:$source_dir" --source-name "$source_name" \
    -o "spdx-json=$out_dir/${prefix}spdx.json" \
    -o "cyclonedx-json=$out_dir/${prefix}cyclonedx.json"
}

scan_osv() {
  local source_dir=$1
  local report=$2
  local source_name=$3
  local status

  set +e
  osv-scanner scan source --recursive "$source_dir" --format json --output-file "$report" >&2
  status=$?
  set -e

  case $status in
    0|1)
      [[ -s $report ]] || {
        echo "OSV-Scanner exited $status without producing $report" >&2
        return 1
      }
      python3 - "$report" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as report:
    json.load(report)
PY
      if ((status == 0)); then
        printf '%s\n' "OSV scan completed without findings: $source_name" >&2
        printf '%s\n' clean
      else
        printf '%s\n' "OSV scan completed with vulnerability findings: $source_name" >&2
        printf '%s\n' findings
      fi
      ;;
    128)
      echo "OSV-Scanner found no package sources in $source_name; recording that scan status." >&2
      python3 - "$report" <<'PY'
import json
import sys

with open(sys.argv[1], "w", encoding="utf-8") as report:
    json.dump(
        {
            "results": [],
            "report_type": "anvil.osv-scan-status.v1",
            "scan_status": "no_packages",
        },
        report,
        indent=2,
    )
    report.write("\n")
PY
      printf '%s\n' no_packages
      ;;
    *)
      echo "OSV-Scanner failed for $source_name with exit code $status" >&2
      return "$status"
      ;;
  esac
}

scan_sbom "$repo_dir" ""
after_status=$(scan_osv "$repo_dir" "$out_dir/osv.json" "$(basename "$repo_dir")")

if [[ -n $before_dir ]]; then
  scan_sbom "$before_dir" "before-"
  before_status=$(scan_osv "$before_dir" "$out_dir/osv-before.json" "$(basename "$before_dir")")

  python3 - "$out_dir/osv-before.json" "$out_dir/osv.json" \
    "$before_status" "$after_status" "$out_dir/osv-delta.json" <<'PY'
import json
import sys

before_path, after_path, before_status, after_status, output_path = sys.argv[1:]


def load_findings(path):
    with open(path, encoding="utf-8") as report:
        document = json.load(report)

    findings = {}
    for result in document.get("results") or []:
        for package_result in result.get("packages") or []:
            package = package_result.get("package", {})
            package_identity = {
                "ecosystem": package.get("ecosystem"),
                "name": package.get("name"),
                "version": package.get("version"),
                "purl": package.get("purl"),
            }
            package_groups = package_result.get("groups") or []
            for vulnerability in package_result.get("vulnerabilities") or []:
                identifiers = {
                    value
                    for value in [
                        vulnerability.get("id"),
                        *(vulnerability.get("aliases") or []),
                    ]
                    if value
                }
                groups = [*package_groups, *(vulnerability.get("groups") or [])]
                for group in groups:
                    group_ids = set(group.get("ids") or [])
                    if identifiers & group_ids:
                        identifiers.update(group_ids)
                if not identifiers:
                    continue

                canonical_ids = sorted(identifiers)
                package_key = json.dumps(package_identity, sort_keys=True)
                key = (package_key, tuple(canonical_ids))
                findings[key] = {
                    "package": package_identity,
                    "vulnerability_ids": canonical_ids,
                }
    return findings


before = load_findings(before_path)
after = load_findings(after_path)
introduced = [after[key] for key in sorted(after.keys() - before.keys())]
resolved = [before[key] for key in sorted(before.keys() - after.keys())]
incomplete = "no_packages" in (before_status, after_status)

delta = {
    "schema": "anvil.osv-delta.v1",
    "comparison_status": "incomplete_no_packages" if incomplete else "complete",
    "before_scan_status": before_status,
    "after_scan_status": after_status,
    "introduced": introduced,
    "resolved": resolved,
    "counts": {"introduced": len(introduced), "resolved": len(resolved)},
}
with open(output_path, "w", encoding="utf-8") as report:
    json.dump(delta, report, indent=2)
    report.write("\n")
PY
fi
