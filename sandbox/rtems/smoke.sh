#!/usr/bin/env bash
# Smoke test for the sandbox: build the erc32 BSP from an RTEMS checkout and run the
# hello sample and a POSIX test on the SIS simulator. Usage: anvil-smoke [path-to-rtems-checkout]
set -euo pipefail
SRC=${1:-/workspace/rtems}
if [ ! -d "$SRC" ]; then
  git clone --depth 1 https://github.com/vishal7pandey/rtems.git "$SRC"
fi
cd "$SRC"
cat > config.ini <<'EOF'
[sparc/erc32]
BUILD_SAMPLES = True
BUILD_PSXTESTS = True
EOF
./waf configure --prefix="$RTEMS_PREFIX"
./waf -j"$(nproc)"
rtems-test --rtems-bsp=erc32-sis --log=/tmp/smoke-hello.log \
  build/sparc/erc32/testsuites/samples/hello.exe 2>&1 | tee /tmp/smoke-hello-console.log
grep -Eq "Passed: *1" /tmp/smoke-hello-console.log
rtems-test --rtems-bsp=erc32-sis --log=/tmp/smoke-psx05.log \
  build/sparc/erc32/testsuites/psxtests/psx05.exe 2>&1 | tee /tmp/smoke-psx05-console.log
grep -Eq "Passed: *1" /tmp/smoke-psx05-console.log
echo "ANVIL SMOKE: PASS"
