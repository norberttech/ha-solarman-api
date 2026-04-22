{ pkgs ? import (fetchTarball {
    url = "https://github.com/NixOS/nixpkgs/archive/b12141ef619e0a9c1c84dc8c684040326f27cdcc.tar.gz";
    sha256 = "0vhprxh6zqrc8bc745crfzs75cl1sqls3hdldlairm0spqsb88k5";
  }) {}
}:

pkgs.mkShell {
  name = "ha-solarman-api-dev";

  packages = [
    pkgs.python313
    pkgs.uv
    pkgs.ruff
    pkgs.mypy
    pkgs.just
  ];

  shellHook = ''
    export UV_PYTHON="${pkgs.python313}/bin/python3.13"
    export UV_PROJECT_ENVIRONMENT="$PWD/.venv"
    export UV_CACHE_DIR="$PWD/.uv-cache"
    export VIRTUAL_ENV="$PWD/.venv"

    if [ -f pyproject.toml ]; then
      uv sync --frozen 2>/dev/null || uv sync
    fi

    export PATH="$PWD/.venv/bin:$PATH"
    echo "ha-solarman-api dev shell ready (python $(python3 --version), uv $(uv --version))"
  '';
}
