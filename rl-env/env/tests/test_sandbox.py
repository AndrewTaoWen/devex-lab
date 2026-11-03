from futurebench import sandbox


def test_base_tag_is_stable_and_depends_on_base(monkeypatch):
    a = sandbox.base_tag()
    assert a == sandbox.base_tag() and len(a) == 6
    monkeypatch.setattr(sandbox, "BASE_IMAGE", "example.org/python:3.12")
    assert sandbox.base_tag() != a


def test_proxy_args_pass_through_proxy_and_ca(monkeypatch, tmp_path):
    ca = tmp_path / "ca.crt"
    ca.write_text("x")
    for k in ("HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:1")
    monkeypatch.setenv("FUTUREBENCH_CA_BUNDLE", str(ca))
    args = sandbox._proxy_args()
    assert "HTTPS_PROXY=http://proxy:1" in args
    assert f"{ca}:/fb-ca.crt:ro" in args and "SSL_CERT_FILE=/fb-ca.crt" in args


def test_install_and_entry_scripts_target_the_mounts():
    assert set(sandbox.MOUNTS) == {"agent", "score"}
    assert "/opt/generated" in sandbox.INSTALL and "/opt/generated" in sandbox.ENTRY
    assert 'exec "$@"' in sandbox.ENTRY
