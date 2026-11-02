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
ARG JOBS=4
RUN git clone ${RSB_REPO} /src/rsb && cd /src/rsb && git checkout ${RSB_REF}
WORKDIR /src/rsb/rtems
RUN ../source-builder/sb-set-builder --prefix=/opt/rtems/7 --jobs=${JOBS} --log=/tmp/rsb.log 7/rtems-sparc \
    || (tail -200 /tmp/rsb.log; exit 1)

FROM ubuntu:24.04
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential python3 python-is-python3 git curl ca-certificates ripgrep jq \
      less vim-tiny patch diffutils file cppcheck clang-format bear \
      openjdk-17-jre-headless pre-commit \
    && rm -rf /var/lib/apt/lists/*

# Pin standalone CI scanners and verify the published release asset digests.
ARG SYFT_VERSION=1.55.0
ARG SYFT_SHA256=bb65249542db5fedeeb4d3f9b018b75e7264dbfbbb10486f044581a92f90490d
ARG OSV_SCANNER_VERSION=2.7.0
ARG OSV_SCANNER_SHA256=160c6253e2922e9e0a6a9771c5c4c40a4b3c6090b34a3cdd5e80c96cc2e4218b
RUN set -eux; \
    curl -fsSL "https://github.com/anchore/syft/releases/download/v${SYFT_VERSION}/syft_${SYFT_VERSION}_linux_amd64.tar.gz" -o /tmp/syft.tar.gz; \
    echo "${SYFT_SHA256}  /tmp/syft.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/syft.tar.gz -C /usr/local/bin syft; \
    curl -fsSL "https://github.com/google/osv-scanner/releases/download/v${OSV_SCANNER_VERSION}/osv-scanner_linux_amd64" -o /usr/local/bin/osv-scanner; \
    echo "${OSV_SCANNER_SHA256}  /usr/local/bin/osv-scanner" | sha256sum -c -; \
    chmod +x /usr/local/bin/osv-scanner; \
    rm -f /tmp/syft.tar.gz
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
