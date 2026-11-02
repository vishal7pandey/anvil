#!/usr/bin/env bash
# Smoke test for the sandbox: build the erc32 BSP from an RTEMS checkout and run the
# hello sample on the SIS simulator. Usage: anvil-smoke [path-to-rtems-checkout]
set -euo pipefail
SRC=${1:-/workspace/rtems}
if [ ! -d "$SRC" ]; then
  git clone --depth 1 https://github.com/vishal7pandey/rtems.git "$SRC"
fi
cd "$SRC"
cat > config.ini <<'EOF'
[sparc/erc32]
BUILD_SAMPLES = True
EOF
./waf configure --prefix="$RTEMS_PREFIX"
./waf -j"$(nproc)"
rtems-test --rtems-bsp=erc32-sis --log=/tmp/smoke.log build/sparc/erc32/testsuites/samples/hello.exe
grep -q "Passed:  *1" /tmp/smoke.log && echo "ANVIL SMOKE: PASS"
