######################
Creating Documentation
######################


Source files and Sphinx
-----------------------

The source files for documentation can be found in docs/source and
consist primarily of reStructuredText (.rst) files. These files contain a
combination of

* Restructured text. RST is primarily for text layout and formatting, such as
  creating headings and subheadings, bolding and italicizing, and creating
  lists, tables, and code blocks. It also allows inserting images and links to
  websites.
* Sphinx directives. Sphinx is a popular program for generating webpages from
  RST and Python code. Sphinx will parse code structure and docstrings to
  automatically create documentation for the API. Sphinx directives tell Sphinx
  which modules, classes, etc. to automatically generate documentation for and
  set some parameters for doing so. The :code:`.. toctree::` directive is also
  used to generate a table of contents.

An easy way to learn how Sphinx converts RST into HTML is to compare the source
files with the documentation it generates. Check out `this handy cheat sheet`_
for the basics.

The API documentation makes heavy use of the `autodoc extension`_, specifically
the :code:`.. autoclass::` directive, to parse class docstrings and generate
documentation. NUPyLab uses the `Google Python style guide`_, which has a
particular docstring format that can be parsed with the Napoleon extension for
Sphinx. Configuration settings for Sphinx and extensions are in
docs/source/conf.py.

.. _this handy cheat sheet: https://sphinx-tutorial.readthedocs.io/cheatsheet/
.. _autodoc extension: https://www.sphinx-doc.org/en/master/usage/extensions/autodoc.html
.. _Google Python style guide: https://google.github.io/styleguide/pyguide.html

Readthedocs
-----------

Documentation for NUPyLab is published by Read the Docs, a popular website
hosting service for Python packages. The Haile Group Read the Docs account is
linked to GitHub, and every time a commit is made to the main branch of the
NUPyLab repository, Read the Docs runs Sphinx and builds updated documentation.

.. image:: https://readthedocs.org/projects/nupylab/badge/?version=latest
    :target: https://nupylab.readthedocs.io/en/latest/?badge=latest
    :alt: Documentation Status

The badge above is displayed on GitHub and the documentation frontpage and
shows whether the documentation build from the latest commit was successful.

See the `Read the Docs tutorial`_ for a detailed guide. NUPyLab settings for
Read the Docs are in the .readthedocs.yaml file, and documentation dependencies
are listed in docs/requirements.txt. These dependencies should be the same as
what is required for the NUPyLab package itself, plus specific requirements for
Sphinx. However, unlike the package dependencies, it is best practice to
specify fixed version numbers to ensure future documentation build stability.
These can be updated periodically. To do so,

* update the packages in your local nupylab environment
* build the documentation locally by changing into the docs directory and
  running :code:`make html`
* verify it built successfully
* set the dependency version numbers to whatever you have locally

.. _Read the Docs tutorial: https://docs.readthedocs.io/en/stable/tutorial/index.html
