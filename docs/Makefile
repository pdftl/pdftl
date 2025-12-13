all: html

html: md
	sphinx-build -b html source build

md: clean
	mkdir source
	cp sphinx-conf.py source/conf.py
	python ./generate_md_docs.py

clean:
	rm -rf build/ source/
