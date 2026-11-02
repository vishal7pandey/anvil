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

The first uncached toolchain build is expected to take around 1–2 hours; the
published GitHub Actions run records its actual duration. The first published
image digest will be added here after the workflow completes.

## RTEMS pre-commit hooks

Install and run the focused hooks from the RTEMS repository root:

```sh
pre-commit install --config sandbox/rtems/pre-commit-config.rtems.yaml
pre-commit run --config sandbox/rtems/pre-commit-config.rtems.yaml
```
