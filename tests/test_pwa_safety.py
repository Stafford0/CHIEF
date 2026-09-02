from pathlib import Path


def test_service_worker_explicitly_excludes_private_api_families() -> None:
    source = (Path(__file__).parents[1] / "apps" / "chief-ui" / "public" / "sw.js").read_text(
        encoding="utf-8"
    )
    private_prefixes = {
        "/audit",
        "/business",
        "/chat",
        "/dashboard",
        "/decisions",
        "/foresight",
        "/memory",
        "/notifications",
        "/plans",
        "/portfolio",
        "/runs",
        "/sessions",
        "/tools",
    }

    assert private_prefixes.issubset({line.strip().strip('",') for line in source.splitlines()})
    assert "url.origin !== self.location.origin || isPrivatePath(url.pathname)" in source


def test_pwa_pairing_token_is_session_scoped_and_never_sent_over_remote_http() -> None:
    source = (Path(__file__).parents[1] / "apps" / "chief-ui" / "src" / "api.ts").read_text(
        encoding="utf-8"
    )

    assert "sessionStorage" in source
    assert "localStorage" not in source
    assert 'target.protocol !== "https:" && !loopback' in source
    assert "Authorization: `Bearer ${bearerToken}`" in source


def test_production_pwa_uses_same_origin_api_proxy() -> None:
    source = (
        Path(__file__).parents[1] / "apps" / "chief-ui" / "src" / "main.tsx"
    ).read_text(encoding="utf-8")

    assert "import.meta.env.PROD" in source
    assert "`${window.location.origin}/api`" in source
