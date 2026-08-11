"""A cp1252 console (Windows default) must never crash the CLI.

The doctor prints \u2713/\u2192 glyphs; on a strict charmap stream that was a
UnicodeEncodeError and a failed checkup. The CLI now reconfigures its streams
to errors="replace", and all file I/O pins utf-8 regardless of locale.
"""

import io
import sys

from admitbench.cli import main


def test_cli_survives_a_cp1252_console(monkeypatch, tmp_path):
    buffer = io.BytesIO()
    strict = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", strict)
    monkeypatch.chdir(tmp_path)  # keep any doctor artifacts out of the repo

    code = main(["doctor"])  # prints the full glyph-heavy checkup

    strict.flush()
    printed = buffer.getvalue().decode("cp1252", errors="replace")
    assert code in (0, 1)  # ran to completion — the crash was the bug
    assert "checkup" in printed or "admitbench" in printed or printed


def test_cartridges_load_under_any_locale():
    """The four files carry \u2192 arrows; reads must not depend on the
    platform default encoding."""
    from admitbench.cartridge import load_cartridge
    from admitbench.paths import bundled_cartridge_root

    for cart in ("cstr", "distillation"):
        assert load_cartridge(bundled_cartridge_root() / cart).cases
