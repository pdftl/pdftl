all: man html

man:
	python ./generate_man_page.py

html:
	python ./generate_sphinx_docs.py
	sphinx-build -b html source build

clean:
	rm pdftl.1
	rm -rf build/ source/
