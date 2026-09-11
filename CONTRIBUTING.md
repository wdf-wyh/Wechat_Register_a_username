# Contributing

Thanks for helping improve **wechat-farm**.

## Dev setup

```bash
python -m venv wechat_env
source wechat_env/bin/activate   # or wechat_env\Scripts\activate on Windows
pip install -r requirements.txt
pip install pytest
pytest scripts/ -q -k "unit"
```

## Guidelines

- Prefer changes in `scripts/`, `content/`, `scheduler/`, docs — avoid editing `core/` and coordinate dictionaries unless fixing a confirmed bug
- New phone models: add a file under `config/device_profiles/` and register it; **do not** overwrite existing profile coordinates
- Do not change `storage/schema.sql` casually
- Never commit `.env`, databases, logs, or unredacted screenshots
- Keep unit tests phone-free (mocks only); device smokes stay out of CI

## Pull requests

1. Describe *why* the change helps (docs / farming depth / ops)
2. Note risk to live devices
3. Ensure `pytest scripts/ -q -k "unit"` passes
