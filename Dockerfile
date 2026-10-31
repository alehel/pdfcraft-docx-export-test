# The suite's environment, with every tool that can change a score pinned: Chromium (makes the
# corpus), LibreOffice (renders the DOCX), Poppler (reads the PDF), the fonts both of them use,
# Python, and the Rust toolchain that builds pdfcraft-cli. The suite itself is not copied in:
# compose.yaml mounts the checkout at /suite, so runs and baselines land in the repository.
#
# To move a pin: change it here, `docker compose build`, then regenerate the corpus if Chromium or
# the fonts changed (`suite.py corpus --force all`), and accept a new baseline.

# Rust 1.99.1 and Debian 13 (trixie), as multi-arch index digests.
ARG RUST_IMAGE=docker.io/library/rust:1.99.1-slim-trixie@sha256:b8e062b1c3a80183df83d9ba187cadd2b5ab84bc78d453dbfeb2fccd5f982247
ARG DEBIAN_IMAGE=docker.io/library/debian:trixie-slim@sha256:f6b08b09a05725f9431502d3b5714f5c4576e82c1a84fa1debe0c24a7e0c3ec4

FROM ${RUST_IMAGE} AS rust

FROM ${DEBIAN_IMAGE}

# Debian packages as they were at this moment (snapshot.debian.org keeps every archive state), so
# the fonts, Python and their libraries never move. Chromium and Poppler are also named by exact
# version: the build fails rather than install anything else.
ARG DEBIAN_SNAPSHOT=20261030T000000Z
ARG CHROMIUM_VERSION=156.0.8078.73-1~deb13u1
ARG POPPLER_VERSION=25.03.0-5+deb13u4
# The Document Foundation's own build (Debian's is 25.2), the same release as the macOS app.
ARG LIBREOFFICE_VERSION=25.8.2.2
ARG LIBREOFFICE_SHA256_ARM64=e26953050f56e610d946ad4c94e0e85d2503418aa329054cb536a6640d29a4a6
ARG LIBREOFFICE_SHA256_AMD64=d703ce5d6760684061f7d22e2b8df91320c2fe3601a8472975b4b05b22af43ba
ARG DEBIAN_FRONTEND=noninteractive

RUN set -eux; \
    rm -f /etc/apt/sources.list.d/debian.sources; \
    printf '%s\n' \
      "deb http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT} trixie main" \
      "deb http://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT} trixie-updates main" \
      "deb http://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT} trixie-security main" \
      > /etc/apt/sources.list; \
    printf '%s\n' 'Acquire::Check-Valid-Until "false";' 'Acquire::Retries "10";' \
      'APT::Install-Recommends "false";' > /etc/apt/apt.conf.d/90snapshot; \
    apt-get update; \
    apt-get install -y \
      ca-certificates curl git gcc libc6-dev \
      python3 \
      poppler-utils="${POPPLER_VERSION}" poppler-data \
      chromium="${CHROMIUM_VERSION}" chromium-common="${CHROMIUM_VERSION}" \
      libjpeg-turbo-progs \
      fontconfig fonts-dejavu-core fonts-liberation fonts-urw-base35 \
      fonts-crosextra-carlito fonts-crosextra-caladea fonts-noto-core fonts-noto-cjk \
      libxinerama1 libdbus-1-3 libcairo2 libcups2 libsm6 libxslt1.1 libnss3; \
    rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "$(dpkg --print-architecture)" in \
      arm64) dir=aarch64; file=aarch64; sha="${LIBREOFFICE_SHA256_ARM64}" ;; \
      amd64) dir=x86_64; file=x86-64; sha="${LIBREOFFICE_SHA256_AMD64}" ;; \
      *) echo "no LibreOffice build for $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac; \
    curl -fsSL --retry 5 -o /tmp/lo.tar.gz \
      "https://downloadarchive.documentfoundation.org/libreoffice/old/${LIBREOFFICE_VERSION}/deb/${dir}/LibreOffice_${LIBREOFFICE_VERSION}_Linux_${file}_deb.tar.gz"; \
    echo "${sha}  /tmp/lo.tar.gz" | sha256sum -c -; \
    mkdir /tmp/lo; \
    tar -xzf /tmp/lo.tar.gz -C /tmp/lo --strip-components=1; \
    dpkg -i /tmp/lo/DEBS/*.deb; \
    rm -rf /tmp/lo /tmp/lo.tar.gz; \
    ln -s "/opt/libreoffice${LIBREOFFICE_VERSION%.*.*}/program/soffice" /usr/local/bin/soffice; \
    soffice --version

COPY --from=rust /usr/local/rustup /usr/local/rustup
COPY --from=rust /usr/local/cargo /usr/local/cargo

# /work is a named volume: the pdfcraft clone, its Cargo target folder and registry, and the
# LibreOffice profiles, kept apart from the macOS ones in the checkout.
ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/work/cargo \
    PATH=/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    SUITE_WORK=/work \
    PDFCRAFT_REPO=https://github.com/storytold/pdfcraft.git \
    SOFFICE=/usr/local/bin/soffice \
    CHROME=/usr/bin/chromium \
    LANG=C.UTF-8 \
    TZ=UTC \
    SUITE_ENVIRONMENT="container: Debian snapshot ${DEBIAN_SNAPSHOT}, Chromium ${CHROMIUM_VERSION}, LibreOffice ${LIBREOFFICE_VERSION}, Poppler ${POPPLER_VERSION}, Rust 1.99.1"

RUN git config --system --add safe.directory '*'

WORKDIR /suite
ENTRYPOINT ["python3", "-I", "/suite/suite.py"]
CMD ["status"]
