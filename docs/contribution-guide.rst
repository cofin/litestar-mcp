==================
Contribution Guide
==================

Thank you for your interest in contributing to Litestar MCP! This guide will help you get started.

Development Setup
-----------------

1. Clone the repository:

.. code-block:: bash

    git clone https://github.com/litestar-org/litestar-mcp.git
    cd litestar-mcp

2. Install development dependencies:

.. code-block:: bash

    make install

Running Tests
-------------

.. code-block:: bash

    # Run tests and the coverage gate
    make test
    make coverage

    # Run the pinned MCP 2026-07-28 conformance framework
    make conformance

    # Run specific test
    uv run --python 3.10 pytest tests/unit/test_plugin.py

The Makefile pins conformance to Node.js ``24.18.1``. If ``nodenv`` is
installed, the target selects it with ``NODENV_VERSION`` without requiring a
tracked ``.node-version`` file. Otherwise, it uses the active Node.js runtime.
The npm dependency itself is locked in ``package-lock.json``.

Code Quality
------------

We use several tools to maintain code quality:

.. code-block:: bash

    make lint
    make check-all

Building Documentation
----------------------

.. code-block:: bash

    make docs
    make validate-examples
    make validate-uvx
    make validate-pep723

Pull Request Guidelines
-----------------------

1. **Fork and Branch**: Create a feature branch from main
2. **Test Coverage**: Ensure new code has appropriate tests
3. **Documentation**: Update docs for new features
4. **Commit Messages**: Use clear, descriptive commit messages
5. **Pull Request**: Create a PR with a clear description

Code Style
----------

- Follow PEP 8
- Use type hints for all public APIs
- Write docstrings for all public functions/classes
- Keep line length to 120 characters

Issue Guidelines
----------------

When reporting bugs or requesting features:

1. Check existing issues first
2. Provide minimal reproduction code
3. Include environment details
4. Be specific about expected vs actual behavior

Community
---------

- **Discord**: Join the Litestar Discord server
- **GitHub Discussions**: For questions and ideas
- **GitHub Issues**: For bugs and feature requests
