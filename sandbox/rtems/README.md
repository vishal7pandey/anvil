# RTEMS 7 sandbox image

The image contains the pinned SPARC RTEMS 7 toolchain, SIS test simulator, and the
analysis tools used by Anvil CI. Published image tags are `7` and `sha-<short commit>`
at `ghcr.io/vishal7pandey/anvil-sandbox-rtems`.

## Build an erc32 BSP

From an RTEMS source checkout in the container:

```sh
cat > config.ini <<'EOF'
[sparc/erc32]
BUILD_SAMPLES = True
BUILD_PSXTESTS = True
EOF
./waf configure --prefix="$RTEMS_PREFIX"
./waf -j"$(nproc)"
```

## Run tests on SIS

Run the complete sandbox acceptance check (builds the BSP, then runs hello and
`psx05`):

```sh
anvil-smoke /workspace/rtems
```

Or, after building the BSP, run either test directly:

```sh
rtems-test --rtems-bsp=erc32-sis build/sparc/erc32/testsuites/samples/hello.exe
rtems-test --rtems-bsp=erc32-sis build/sparc/erc32/testsuites/psxtests/psx05.exe
```

## Generate SBOM and OSV reports

Run the pinned Syft and OSV-Scanner tools from the sandbox image in CI, or on a
host where the OSV API is reachable. The agent sandbox can be offline after
checkout, so its environment is not suitable for the OSV commit queries. The
source must be a Git checkout with its submodule metadata; submodules do not
need to be initialized. Keep the output directory outside the scanned sources:

```sh
bash /workspace/sandbox/rtems/sbom.sh /workspace/project /workspace/reports
```

This writes `spdx.json`, `cyclonedx.json`, and `osv.json`. OSV-Scanner findings
and OSV API results for each Git submodule commit are recorded in the report;
vulnerability findings do not fail the scan command, while scanner or API
errors do. If neither package sources nor Git submodules can be scanned, the
report records `scan_status: no_packages` rather than claiming a clean scan.

To compare a current checkout with a baseline directory, add `--before`:

```sh
bash /workspace/sandbox/rtems/sbom.sh /workspace/current /workspace/reports --before /workspace/baseline
```

This also writes `before-spdx.json`, `before-cyclonedx.json`,
`osv-before.json`, and `osv-delta.json`. The delta reports introduced and
resolved package or Git-source vulnerabilities by ID; Git-source findings are
compared by repository and vulnerability ID across commit changes. Its
comparison status is marked incomplete only if either source had no supported
package sources or Git submodules to scan.

## Published image

The public `:7` image is built from the main Dockerfile and published at:

```text
ghcr.io/vishal7pandey/anvil-sandbox-rtems:7
sha256:15e3705829d08b8b803bc863ef3b07c7536c9aa5a4f20e88bc8de46635823de1
```

The image passed `anvil-smoke` locally: the erc32 BSP built, and `hello.exe` and
`psx05.exe` passed on SIS. An unauthenticated manifest inspection and `docker pull`
also succeeded. The [GitHub Actions verification run](https://github.com/vishal7pandey/anvil/actions/runs/41574286616)
checks the published image with `anvil-smoke`.

## RTEMS pre-commit hooks

Install and run the focused hooks from the RTEMS repository root:

```sh
pre-commit install --config sandbox/rtems/pre-commit-config.rtems.yaml
pre-commit run --config sandbox/rtems/pre-commit-config.rtems.yaml
```
