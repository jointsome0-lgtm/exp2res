.PHONY: demo-reset demo-run demo-verify

demo-reset:
	@PYTHONPATH=. python scripts/demo.py reset

demo-run:
	@PYTHONPATH=. python scripts/demo.py run

demo-verify:
	@PYTHONPATH=. python scripts/demo.py verify
