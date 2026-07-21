.PHONY: demo-reset demo-run demo-verify

demo-reset:
	@python scripts/demo.py reset

demo-run:
	@python scripts/demo.py run

demo-verify:
	@python scripts/demo.py verify
