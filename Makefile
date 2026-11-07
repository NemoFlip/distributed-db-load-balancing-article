LATEX ?= tectonic
PYTHON ?= python3

.PHONY: all article example test experiment figure dist clean

all: article example

article:
	mkdir -p build
	$(LATEX) -o build article.tex

example:
	mkdir -p example/build
	cd example && $(LATEX) -o build example.tex

test:
	$(PYTHON) -m unittest discover -s experiment -p 'test_*.py' -v

experiment:
	$(PYTHON) experiment/run.py --out experiment/results
	$(PYTHON) experiment/analyze.py experiment/results

figure:
	$(PYTHON) experiment/make_figure.py

dist:
	rm -rf dist/misis-article
	mkdir -p dist/misis-article/figures dist/misis-article/experiment/results
	cp misisarticle.cls article.tex README.md LICENSE dist/misis-article/
	cp figures/tradeoff.png dist/misis-article/figures/
	cp experiment/results/table.tex dist/misis-article/experiment/results/
	rm -f dist/misis-article.zip
	cd dist && zip -r misis-article.zip misis-article
	rm -rf dist/misis-article

clean:
	rm -rf build example/build dist
	rm -f *.aux *.log *.out *.toc *.bbl *.blg *.bcf *.run.xml *.xdv *.synctex.gz *.fls *.fdb_latexmk
	rm -f article.pdf example/example.pdf
