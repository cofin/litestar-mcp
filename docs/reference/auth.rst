========
Auth API
========

This module contains the built-in authentication middleware, the OIDC
token-validation engine, and the injectable JWKS cache. The configuration
dataclasses :class:`~litestar_mcp.auth.MCPAuthConfig` and
:class:`~litestar_mcp.auth.OIDCProviderConfig` are documented under
:doc:`types`.

.. currentmodule:: litestar_mcp.auth

MCPAuthBackend
--------------

.. autoclass:: MCPAuthBackend
   :members:
   :show-inheritance:

create_oidc_validator
---------------------

.. autofunction:: create_oidc_validator

TokenValidator
--------------

.. autodata:: TokenValidator

JWKSCache
---------

.. autoclass:: JWKSCache
   :members:
   :show-inheritance:

DefaultJWKSCache
----------------

.. autoclass:: DefaultJWKSCache
   :members:
   :show-inheritance:
