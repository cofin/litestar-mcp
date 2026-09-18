==========
MCP Skills
==========

Agent Skills served over MCP let a server publish reusable, versioned
instruction bundles — a ``SKILL.md`` file plus any supporting reference
files — that a client can discover and read the same way it reads any
other MCP resource. The extension identifier is
``io.modelcontextprotocol/skills`` (SEP-2640), and it is layered directly
on top of the existing ``resources/*`` methods rather than introducing a
new content model.

.. note::

    This page documents MCP skills: filesystem-backed instruction
    bundles served through ``skills/list``, ``skills/get``, and
    ``resources/*``. It has no relationship to the A2A agent card's
    ``AgentSkill`` entries described in :doc:`a2a`, which advertise this
    same application's agent's task-handling capabilities over the A2A
    protocol. Do not confuse the two "skills" concepts when integrating
    both extensions in the same application.

Enabling Skills
===============

Pass :class:`~litestar_mcp.mcp.config.MCPSkillsConfig` to
:class:`~litestar_mcp.MCPConfig`:

.. literalinclude:: /examples/snippets/configuration_skills.py
    :language: python
    :caption: ``docs/examples/snippets/configuration_skills.py``
    :start-after: # start-example
    :end-before: # end-example
    :dedent:

``paths`` is typed as ``Sequence[Path]``. Pass :class:`~pathlib.Path`
objects, not strings — ``__post_init__`` coerces each entry with
``Path(...)`` so a plain string happens to work at runtime, but a
type-checked caller passing ``str`` gets a mypy or pyright error against
the declared annotation.

Other :class:`~litestar_mcp.mcp.config.MCPSkillsConfig` fields default to
``directory_read=True``, ``max_files_per_skill=512``, and
``max_bytes_per_skill=16_777_216``.

Directory Layout
================

Each configured path is scanned one level deep for candidate skill
folders:

.. code-block:: text

    <path>/
      hello-skill/
        SKILL.md
        reference.md
        scripts/
          run.py

Only an immediate child folder that contains a ``SKILL.md`` file is
treated as a skill; a folder without one is silently ignored rather than
reported. This is the most common first mistake — the wrong nesting
depth, a case mismatch such as ``Skill.md``, or a file that was never
committed all produce the same silent skip, not an error.

Once a folder is picked up because it has a ``SKILL.md``, the folder name
must equal the frontmatter ``name`` field exactly. A skill's own files are
then collected recursively — ``SKILL.md`` plus every other file nested
under the folder, at any depth, such as ``scripts/run.py`` above.
Dotfiles and symlinked files or directories are excluded from the
manifest entirely.

``SKILL.md`` requires non-empty string ``name`` and ``description``
fields in its YAML frontmatter. Each of the following fails application
startup — when ``LitestarMCP(config)`` builds the catalog — with
:exc:`~litestar.exceptions.ImproperlyConfiguredException`:

- The frontmatter is missing, does not start with a ``---`` block, has no
  closing ``---``, or does not parse to a YAML mapping.
- ``name`` or ``description`` missing, empty, or not a string.
- The frontmatter ``name`` does not match the folder name.
- The same skill ``name`` loaded from more than one configured path.
- A skill's file count exceeds ``max_files_per_skill``, or its total byte
  size exceeds ``max_bytes_per_skill``.
- A configured entry in ``paths`` is not a directory.

Two failures surface earlier still, from
:class:`~litestar_mcp.mcp.config.MCPSkillsConfig.__post_init__` itself, as
a plain :exc:`ValueError`: an empty ``paths`` sequence, or a non-positive
``max_files_per_skill`` / ``max_bytes_per_skill``.

A ``SKILL.md`` that exists but cannot be read, or whose bytes are not
valid UTF-8, raises the underlying :exc:`OSError` or
:exc:`UnicodeDecodeError` directly — it does not get wrapped into
``ImproperlyConfiguredException``.

Wire Methods
============

Three JSON-RPC methods serve the catalog:

``skills/list``
    Returns one page of the skill catalog, one atomic entry per skill —
    a skill's own files are always returned together and never split
    across pages.

``skills/get``
    Returns one skill's entry by its ``skill://<name>/SKILL.md`` URI.

``resources/directory/read``
    Lists the entries directly under a ``skill://`` directory URI, one
    level deep; content further down collapses into a single
    ``inode/directory`` entry per subdirectory. Only advertised and
    served when ``directory_read=True``.

``skills/list`` and ``resources/directory/read`` are paginated exactly
like the other list methods (see :ref:`List Pagination
<usage/configuration:List Pagination>`): a response may include
``nextCursor``, bounded by ``MCPConfig.list_page_size`` (default
``100``); pass it back as ``params.cursor`` for the next page. A client
that ignores ``nextCursor`` silently sees a truncated catalog.

Individual skill files are not fetched through a skills-specific method —
they are read with the ordinary ``resources/read`` method, using the same
``skill://<name>/<relative-path>`` URI that appears in the manifest.

The examples below use the real sample skill shipped with this page's
snippet (``docs/examples/skills/hello-skill``): digests and sizes are its
true values, shown in full — a digest is ``sha256:`` followed by 64
lowercase hex characters — and long ``text`` content is truncated with
``...`` for width.

.. code-block:: json

    // Request
    {"jsonrpc":"2.0","id":1,"method":"skills/list","params":{}}

    // Response
    {"jsonrpc":"2.0","id":1,"result":{
      "skills":[{
        "uri":"skill://hello-skill/SKILL.md",
        "frontmatter":{
          "name":"hello-skill",
          "description":"Greets the user and explains how this Litestar server exposes skills."
        },
        "resources":[
          {"uri":"skill://hello-skill/SKILL.md",
           "digest":"sha256:733c1d9526318ff7dbd07b507eb711ffd4397ea64534cee5c7f6f9a59fcbda12",
           "size":169},
          {"uri":"skill://hello-skill/reference.md",
           "digest":"sha256:50d4b6470fcf7748340eae1e76628a3da46c96080ec78bec164b379794558f2d",
           "size":111}
        ]
      }],
      "resultType":"complete","ttlMs":0,"cacheScope":"private"
    }}

.. code-block:: json

    // Request
    {"jsonrpc":"2.0","id":2,"method":"resources/read",
     "params":{"uri":"skill://hello-skill/reference.md"}}

    // Response
    {"jsonrpc":"2.0","id":2,"result":{"contents":[
      {"uri":"skill://hello-skill/reference.md",
       "mimeType":"text/markdown",
       "text":"Greet the user by name..."}
    ],"resultType":"complete","ttlMs":0,"cacheScope":"private"}}

``SKILL.md`` is always served as ``text/markdown``; every other file's
MIME type comes from :func:`mimetypes.guess_type`, falling back to
``application/octet-stream`` when the type cannot be guessed. A resource
whose MIME type is a text media type is returned as ``text`` content;
anything else is returned as base64 ``blob`` content, bounded by
``MCPConfig.max_blob_bytes``. A file with a text MIME type whose bytes
are not valid UTF-8 also falls back to ``blob``.

``skills/list`` and ``skills/get`` results carry ``resultType``, ``ttlMs``,
and ``cacheScope`` (from ``MCPConfig.cache_ttl_ms`` / ``cache_scope``);
``resources/read`` carries the same cache fields for every resource,
skill files included. ``resources/directory/read`` carries ``resultType``
only — it is deliberately not cacheable.

Error Contract
==============

``INVALID_PARAMS`` (``-32602``) is returned for:

- ``skills/get`` for an unknown skill URI.
- ``resources/read`` for an unknown ``skill://`` file URI.
- ``resources/directory/read`` for a trailing-slash URI, or a URI that
  does not name an existing directory.

``INTERNAL_ERROR`` (``-32603``) is returned for:

- A file whose bytes on disk no longer match the digest captured at
  startup.
- A file that cannot be read from disk.
- A file whose blob content would exceed ``MCPConfig.max_blob_bytes``.

Static Catalog
==============

The skill catalog is built once, when ``LitestarMCP(config)`` constructs
the plugin, and is never rescanned afterward. Editing a skill file on
disk after that point does not update ``skills/list`` or ``skills/get`` —
both keep reporting the manifest captured at construction time — and a
subsequent ``resources/read`` for that file fails with ``-32603``
because its bytes no longer match the digest captured then. Restart the
application to pick up filesystem changes.

``MCPConfig.include_operations`` / ``exclude_operations`` /
``include_tags`` / ``exclude_tags`` do not filter skills. Those filters
apply only to Litestar route handlers marked with ``mcp_tool`` /
``mcp_resource`` / ``mcp_prompt``; every configured skill is always
served.

Disabling Directory Listing
===========================

Set ``directory_read=False`` to skip advertising and serving
``resources/directory/read``. The extension still advertises
``{"directoryRead": false}`` under
``capabilities.extensions["io.modelcontextprotocol/skills"]`` in
``server/discover``, and a client that calls the method anyway receives
``METHOD_NOT_FOUND`` (``-32601``). Skill files remain readable through
``resources/read`` either way.

Interaction With Handler-Declared Resources
===========================================

``resources/read`` resolves URIs in a fixed order: the built-in
``litestar://openapi`` resource, then the skill catalog, then route
handlers. Once skills are enabled, every ``skill://`` URI is claimed by
the skill branch — an unmatched ``skill://`` URI returns ``-32602``
without ever reaching route-handler resolution. Do not declare
``mcp_resource_uri="skill://..."`` on a route handler: it is not merely
shadowed by a same-named skill, it is unreachable outright. See
:doc:`resources` for handler-declared resource URIs.
