# Task 3 Report: Pipeline 层改造 - 传递 system_config

## Status: DONE_WITH_CONCERNS

## Summary
Successfully modified `backend/app/v4/pipeline.py` to thread `system_config` from `run_reverse_pipeline` down through `_parse_hlr` to `HLRWordParser`. The pipeline now uses `get_hlr_system_config(system_type)` to load system config (defaulting to "hvac") and passes it to the parser.

## Changes Made

### 1. `_parse_hlr` (line 89)
- Added required `system_config: dict` parameter
- Updated log message to include system name: `f"Parsing HLR: {input_path} (system: {system_config['name']})"`
- Pass `system_config` to `HLRWordParser(input_path, system_config)` (matches new parser signature from commit 64f4160)

### 2. `run_reverse_pipeline` (line 581)
- Added new `system_type: str = "hvac"` keyword parameter (backwards-compatible default)
- Imported `get_hlr_system_config` lazily inside the function
- Loaded `system_config = get_hlr_system_config(system_type)` at start of pipeline
- Passed `system_config` to `_parse_hlr` call

## Verification

### Command
```bash
cd backend && python -c "from app.v4.pipeline import run_reverse_pipeline; print('OK')"
```

### Output
```
OK
```

The module imports cleanly and the function signature is valid.

## Concerns

### C1: `run_forward_pipeline` calls `_parse_hlr` without `system_config`
At pipeline.py:129 (line offset before edit), `run_forward_pipeline` calls:
```python
hlr_out = _parse_hlr(hlr, output_dir / "hlr_requirements.json")
```
This call site was NOT updated because the brief scoped this task to `run_reverse_pipeline` only. With `_parse_hlr` now requiring `system_config`, this call will raise `TypeError: missing 1 required positional argument: 'system_config'` at runtime.

**Recommendation**: Add a separate follow-up task (or extend Task 3) to update `run_forward_pipeline` to also accept `system_type` and pass `system_config` to `_parse_hlr`. This was likely intentional scoping for Task 3, but should be tracked to avoid breaking forward pipeline.

## Files Modified

- `backend/app/v4/pipeline.py`

## Files Created

- `.superpowers/sdd/hlr-system-adapt-impl/task-3-report.md`

## Next Steps

1. Address Concern C1: update `run_forward_pipeline` to also pass `system_config`
2. Add `system_type` parameter to API layer (`backend/app/api/*` or wherever `run_reverse_pipeline` is invoked) to allow runtime selection
3. Add tests for `run_reverse_pipeline` with non-default `system_type`