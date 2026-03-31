.PHONY: facts facts-check

facts:
	python3 scripts/product_facts.py

facts-check:
	python3 scripts/product_facts.py --check
