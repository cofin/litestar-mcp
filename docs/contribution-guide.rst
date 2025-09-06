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

    uv sync --group dev

3. Install pre-commit hooks:

.. code-block:: bash

    pre-commit install

Running Tests
-------------

.. code-block:: bash

    # Run all tests
    uv run pytest

    # Run with coverage
    uv run pytest --cov

    # Run specific test
    uv run pytest tests/test_plugin.py

Code Quality
------------

We use several tools to maintain code quality:

.. code-block:: bash

    # Linting
    uv run ruff check src tests examples
    uv run mypy src tests examples

    # Formatting
    uv run ruff format src tests examples

Building Documentation
----------------------

.. code-block:: bash

    # Install doc dependencies
    uv sync --group doc

    # Build docs
    cd docs
    uv run sphinx-build -b html . _build/html

    # Live preview
    uv run sphinx-autobuild . _build/html

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
