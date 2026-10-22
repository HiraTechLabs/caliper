import stat

import pytest

from caliper.remote import RemoteError, generate_ed25519_key, scan_host_key
from tests.support import requires_ssh_keygen, requires_ssh_keyscan


@requires_ssh_keygen
def test_real_ssh_keygen_creates_an_owner_only_ed25519_key(tmp_path):
    private = tmp_path / "repo_deploy"
    public = generate_ed25519_key(private, "caliper-test")

    assert public.startswith("ssh-ed25519 ")
    assert private.is_file()
    assert stat.S_IMODE(private.stat().st_mode) == 0o600


@requires_ssh_keyscan
def test_real_ssh_keyscan_reports_an_unreachable_host_plainly():
    with pytest.raises(RemoteError) as error:
        scan_host_key("127.0.0.1", port=1)

    message = str(error.value)
    assert "Cannot read the host key" in message
    assert "127.0.0.1" in message
