all: html

html: md
	sphinx-build -b html source build

man: md
	sphinx-build -b man source build

md: clean source/conf.py
	cp sphinx-conf.py source/conf.py
	python ./generate_md_docs.py


source/conf.py: sphinx-conf.py
	mkdir -p source
	cp sphinx-conf.py source/

clean:
	rm -rf build/ source/
