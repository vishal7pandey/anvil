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

Run the pinned Syft and OSV-Scanner tools from the sandbox image. The output
directory must be outside the scanned source directories:

```sh
bash /workspace/sandbox/rtems/sbom.sh /workspace/project /workspace/reports
```

This writes `spdx.json`, `cyclonedx.json`, and `osv.json`. OSV vulnerability
findings are reported in the JSON file without failing the scan command;
scanner or infrastructure errors fail it. If OSV-Scanner finds no supported
package sources, the report records `scan_status: no_packages` rather than
claiming a clean scan.

To compare a current checkout with a baseline directory, add `--before`:

```sh
bash /workspace/sandbox/rtems/sbom.sh /workspace/current /workspace/reports --before /workspace/baseline
```

This also writes `before-spdx.json`, `before-cyclonedx.json`,
`osv-before.json`, and `osv-delta.json`. The delta reports introduced and
resolved package vulnerabilities; its comparison status is marked incomplete
if either scan found no supported package sources.

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
