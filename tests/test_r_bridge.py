"""Python-to-R bridge tests — verify run_r_script round-trips JSON correctly.

Run with:  pytest tests/test_r_bridge.py -v -s
"""

import pytest

from engines.markov.runner import RScriptError, check_r_installed, run_r_script

R_TEST_SCRIPT = "r/test_connection.R"


# ── Skip the entire module if R is not installed ──────────────────────
def pytest_configure():
    if not check_r_installed():
        pytest.skip(
            "R not installed — skipping R bridge tests.\n"
            "Install R from https://cloud.r-project.org/",
            allow_module_level=True,
        )


# ====================================================================
# 1. Happy-path connection test
# ====================================================================

class TestRConnection:

    def test_sum_and_product(self):
        """run_r_script should return correct arithmetic from R."""
        result = run_r_script(R_TEST_SCRIPT, {"a": 5, "b": 3})

        assert result["sum"] == 8
        assert result["product"] == 15
        assert result["message"] == "R connection working"

        print(f"\n  R returned: {result}")

    def test_negative_numbers(self):
        """Negative values should round-trip correctly."""
        result = run_r_script(R_TEST_SCRIPT, {"a": -4, "b": 7})

        assert result["sum"] == 3
        assert result["product"] == -28
        print(f"\n  R returned: {result}")

    def test_floats(self):
        """Floating-point values should round-trip correctly."""
        result = run_r_script(R_TEST_SCRIPT, {"a": 2.5, "b": 3.5})

        assert result["sum"] == 6.0
        assert result["product"] == 8.75
        print(f"\n  R returned: {result}")


# ====================================================================
# 2. Error handling
# ====================================================================

class TestRErrorHandling:

    def test_missing_parameters(self):
        """R script should fail when required params are absent."""
        with pytest.raises(RScriptError) as exc_info:
            run_r_script(R_TEST_SCRIPT, {"x": 1})

        assert "missing required parameters" in exc_info.value.stderr.lower()
        print(f"\n  Correctly raised RScriptError: {exc_info.value.stderr.strip()}")

    def test_invalid_script_path(self):
        """A non-existent script path should raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            run_r_script("r/does_not_exist.R", {"a": 1, "b": 2})

        print("\n  Correctly raised FileNotFoundError for missing script")

    def test_empty_params(self):
        """An empty params dict should trigger the missing-params error in R."""
        with pytest.raises(RScriptError) as exc_info:
            run_r_script(R_TEST_SCRIPT, {})

        assert "missing required parameters" in exc_info.value.stderr.lower()
        print(f"\n  Correctly raised RScriptError: {exc_info.value.stderr.strip()}")


# ====================================================================
# 3. R installation check
# ====================================================================

class TestCheckRInstalled:

    def test_check_r_installed_returns_true(self):
        """check_r_installed() should return True (we already skipped if not)."""
        assert check_r_installed() is True
        print("\n  R is installed and reachable")


# ====================================================================
# Post-run advice
# ====================================================================

def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if exitstatus == 0:
        terminalreporter.write_line("")
        terminalreporter.write_line(
            "If tests pass, install R packages:  "
            "install.packages(c('jsonlite', 'heemod', 'ggplot2'))",
            green=True,
        )
