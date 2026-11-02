# Anvil sandbox image for RTEMS work: SPARC cross toolchain + SIS simulator + rtems-tools.
# One image serves the local sandbox (docker run per session) and the cloud environment
# (GitHub Actions `container:`), so "works in the sandbox" means "works in CI".
#
# Build (1-2 h the first time, cached after):
#   docker build -t anvil-sandbox-rtems:7 sandbox/rtems
#
# Test inside: build a BSP and run a test on the simulator, see sandbox/rtems/smoke.sh.

FROM ubuntu:24.04 AS toolchain
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential g++ gcc python3 python3-dev python-is-python3 git bison flex texinfo \
      unzip pax bzip2 xz-utils zlib1g-dev libncurses-dev curl ca-certificates file \
    && rm -rf /var/lib/apt/lists/*

# Pinned for reproducibility. Bump deliberately (that is Test Case 2 territory).
ARG RSB_REPO=https://github.com/vishal7pandey/rtems-source-builder.git
ARG RSB_REF=74fffa09a3df68b7e6cacabbde243ea7828edb34
ARG JOBS=6
RUN git clone ${RSB_REPO} /src/rsb && cd /src/rsb && git checkout ${RSB_REF}
WORKDIR /src/rsb/rtems
RUN ../source-builder/sb-set-builder --prefix=/opt/rtems/7 --jobs=${JOBS} --log=/tmp/rsb.log 7/rtems-sparc \
    || (tail -200 /tmp/rsb.log; exit 1)

FROM ubuntu:24.04
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential python3 python-is-python3 git curl ca-certificates ripgrep jq \
      less vim-tiny patch diffutils file cppcheck clang-format \
    && rm -rf /var/lib/apt/lists/*
COPY --from=toolchain /opt/rtems /opt/rtems
ENV PATH=/opt/rtems/7/bin:$PATH \
    RTEMS_PREFIX=/opt/rtems/7
# Non-root agent user; the workspace is bind-mounted or cloned into /workspace.
RUN useradd -m -u 1001 agent && mkdir -p /workspace && chown agent /workspace
COPY smoke.sh /usr/local/bin/anvil-smoke
RUN chmod +x /usr/local/bin/anvil-smoke
USER agent
WORKDIR /workspace
CMD ["sleep", "infinity"]
