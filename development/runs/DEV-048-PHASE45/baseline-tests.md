# Baseline tests before implementation

Command (isolated Phase 4.5 checkout, Windows Python 3.12):

```powershell
python -m pytest app/tests/test_dev007_mcp.py app/tests/test_dev007_tool_registry.py app/tests/test_dev007_vault.py -q -p no:cacheprovider --basetemp development/runs/DEV-048-PHASE45/baseline-tmp
```

Actual result: **45 passed, 24 skipped in 17.85s**, process exit 0.

These are existing regression tests, not tests of the proposed prototype. Internal DEV-007 fixtures are not present in this source checkout; test files use `requires_internal_runs`. Skipped cases are not evidence of passing MCP behavior. The future prototype requires its own self-contained fixtures and a genuine SDK protocol test without optional skip-on-missing behavior.

After pytest's result, pyreadline3 emitted an ignored `Console.__del__` Windows invalid-handle exception. It did not change the pytest process exit code. This is an observed environment limitation, not a clean console shutdown claim.

Local SDK inventory: mcp 1.30.0, pytest 9.1.1; `langchain` distribution absent. Maintained LangChain MCP source research must not be represented as a local runtime test.
