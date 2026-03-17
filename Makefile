.PHONY: install lint fmt test watcher app

install:
	pip install -r requirements.txt
	pip install ruff

lint:
	ruff check .
	ruff format --check .

fmt:
	ruff check --fix .
	ruff format .

test:
	python -m pytest test_database.py -v

# Validate that the installed google-genai SDK has the expected API contract
validate-sdk:
	python - <<'EOF'
	from google import genai, __version__
	import inspect
	sig = inspect.signature(genai.client.Files.upload)
	params = list(sig.parameters.keys())
	print(f"google-genai {__version__}, files.upload params: {params}")
	assert "file" in params, f"API mismatch: {params}"
	print("OK")
	EOF

watcher:
	python watcher.py --once

app:
	streamlit run app.py
