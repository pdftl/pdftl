all: html

html: md
	cp sphinx-conf.py source/conf.py
	sphinx-build -b html source build

md: clean
	python ./generate_md_docs.py

clean:
	rm -rf build/ source/
