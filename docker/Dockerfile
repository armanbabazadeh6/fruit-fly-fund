# The neural engine is Linux-only — `vendor/stonkfly/neural/brain.py` loads `libmemory.so` —
# and `flyvsly prepare` compiles that kernel with a C++17 compiler. This image is that
# environment for a host that has Docker but no Linux distro of its own, which is the case on
# a stock Windows box: `wsl --install` wants an administrator and usually a reboot, while
# Docker Desktop's engine is already a Linux VM.
#
#   docker build -t flyvsly-nn -f docker/Dockerfile .
#   docker run --rm -v "$PWD:/work" -w /work flyvsly-nn \
#     bash -lc 'pip install -e . && flyvsly doctor && flyvsly prepare && flyvsly doctor --measure'
#
# `data/` and `runs/` are bind-mounted from the host and are already git-ignored, so the
# downloaded graph and every recording survive between containers.

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Python 3.12 satisfies the project's `requires-python = ">=3.11"`, so nothing here needs the
# `--ignore-requires-python` that the host's 3.10 venv does.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      python3 python3-venv python3-pip \
      build-essential \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# The dependencies, baked in: a rebuild after a code change then costs seconds, not a download.
RUN python3 -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/venv/bin/pip install --no-cache-dir \
      "numpy>=2,<3" "pandas>=2.2,<4" "pyarrow>=17" "Pillow>=10" "pytest>=8"

ENV PATH=/opt/venv/bin:$PATH
WORKDIR /work
